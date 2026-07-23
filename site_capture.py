"""从 52etf.site 通过 Playwright 导出官方热力图 PNG。

与官网「截图分享」同源：等待 treemap 就绪后点击 .screenshot-trigger，
从预览 dataURL 解码 PNG。失败时用高 DPI 视口截图兜底（device_scale_factor，
接近官方导出清晰度）。浏览器实例进程内复用，全局锁防并发爆内存。
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from typing import Any

from astrbot.api import logger

DEFAULT_URL = "https://52etf.site/"
DEFAULT_VIEWPORT = {"width": 1400, "height": 900}
# 与 52etf exportTreemapImage 内部 scale≈3 对齐，视口截图像素约 4200×2700
DEFAULT_DEVICE_SCALE = 3.0
LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-extensions",
]


class SiteCaptureError(Exception):
    """52etf 截图失败。"""


class SiteHeatmapCapturer:
    """复用 Chromium 的 52etf 热力图导出器。"""

    def __init__(
        self,
        url: str = DEFAULT_URL,
        viewport: dict[str, int] | None = None,
        device_scale_factor: float = DEFAULT_DEVICE_SCALE,
        goto_timeout_ms: int = 45000,
        ready_wait_ms: int = 8000,
        export_timeout_ms: int = 15000,
    ):
        self.url = (url or DEFAULT_URL).strip() or DEFAULT_URL
        self.viewport = viewport or dict(DEFAULT_VIEWPORT)
        self.device_scale_factor = max(1.0, min(4.0, float(device_scale_factor or DEFAULT_DEVICE_SCALE)))
        self.goto_timeout_ms = max(5000, int(goto_timeout_ms))
        self.ready_wait_ms = max(0, int(ready_wait_ms))
        self.export_timeout_ms = max(3000, int(export_timeout_ms))

        self._lock = asyncio.Lock()
        self._pw: Any = None
        self._browser: Any = None
        self._playwright_cm: Any = None

    async def capture_to_file(self, out_path: str) -> str:
        """导出热力图到 out_path，成功返回路径。"""
        async with self._lock:
            try:
                page = await self._ensure_page()
                png = await self._export_png_bytes(page)
            except SiteCaptureError:
                await self._reset_browser()
                raise
            except Exception as e:
                await self._reset_browser()
                raise SiteCaptureError(f"浏览器抓图异常: {e}") from e

            os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(png)
            return out_path

    async def close(self) -> None:
        async with self._lock:
            await self._reset_browser()

    async def _ensure_page(self):
        if self._browser is not None and self._browser.is_connected():
            return await self._browser.new_page(
                viewport=self.viewport,
                device_scale_factor=self.device_scale_factor,
            )

        await self._reset_browser()
        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise SiteCaptureError(
                "未安装 playwright。请在 AstrBot Python 环境中执行: pip install playwright && playwright install chromium"
            ) from e

        self._playwright_cm = async_playwright()
        self._pw = await self._playwright_cm.__aenter__()
        try:
            self._browser = await self._pw.chromium.launch(
                headless=True,
                args=LAUNCH_ARGS,
            )
        except Exception as e:
            await self._reset_browser()
            raise SiteCaptureError(
                f"Chromium 启动失败（是否已 playwright install chromium？）: {e}"
            ) from e
        return await self._browser.new_page(
            viewport=self.viewport,
            device_scale_factor=self.device_scale_factor,
        )

    async def _export_png_bytes(self, page) -> bytes:
        t0 = time.time()
        try:
            await page.goto(
                self.url,
                wait_until="domcontentloaded",
                timeout=self.goto_timeout_ms,
            )
            await page.wait_for_selector("#treemap", timeout=self.goto_timeout_ms)
            if self.ready_wait_ms > 0:
                await page.wait_for_timeout(self.ready_wait_ms)

            # 1) 优先：官网「截图分享」dataURL（构图/水印最正）
            raw = await self._try_official_export(page)
            mode = "site_export"
            if raw is None:
                # 2) 兜底：高 DPI 视口截图（构图含侧栏，清晰度接近官方）
                raw = await self._fallback_viewport_screenshot(page)
                mode = "viewport_hd"

            if len(raw) < 1000:
                raise SiteCaptureError(f"导出图片过小（{len(raw)} bytes），可能渲染未完成")

            logger.info(
                f"[a_heatmap] 52etf 导出成功 mode={mode} size={len(raw)} "
                f"dpr={self.device_scale_factor} cost={time.time() - t0:.1f}s"
            )
            return raw
        finally:
            try:
                await page.close()
            except Exception:
                pass

    async def _try_official_export(self, page) -> bytes | None:
        """点击截图分享或 window.exportTreemapImage，成功返回 PNG bytes。"""
        data_url = None
        btn = await page.query_selector(".screenshot-trigger")
        if btn:
            try:
                await btn.click()
            except Exception as e:
                logger.warning(f"[a_heatmap] 点击截图分享失败: {e}")
                btn = None
            if btn:
                deadline = time.time() + self.export_timeout_ms / 1000.0
                while time.time() < deadline:
                    data_url = await page.evaluate(
                        """() => {
                          const img = document.querySelector(
                            '.preview-image, img[src^="data:image"]'
                          );
                          if (!img) return null;
                          const src = img.getAttribute('src') || '';
                          return src.startsWith('data:image') ? src : null;
                        }"""
                    )
                    if data_url:
                        break
                    await page.wait_for_timeout(300)

        if not data_url:
            data_url = await page.evaluate(
                """async () => {
                  try {
                    if (typeof window.exportTreemapImage === 'function') {
                      return window.exportTreemapImage();
                    }
                  } catch (e) {}
                  return null;
                }"""
            )

        if not data_url or not str(data_url).startswith("data:image"):
            return None
        try:
            return _data_url_to_bytes(str(data_url))
        except SiteCaptureError as e:
            logger.warning(f"[a_heatmap] dataURL 解码失败，将尝试高清视口截图: {e}")
            return None

    async def _hide_overlays(self, page) -> None:
        try:
            await page.evaluate(
                """() => {
                  document.querySelectorAll(
                    '.preview-overlay, .preview-frame'
                  ).forEach((el) => {
                    el.style.setProperty('display', 'none', 'important');
                  });
                }"""
            )
        except Exception:
            pass

    async def _fallback_viewport_screenshot(self, page) -> bytes:
        """官方导出不可用时：高 DPI 视口截图；再失败才截 #treemap。"""
        await self._hide_overlays(page)

        # 主兜底：当前视口（含侧栏，用户观感更好；像素 = CSS × device_scale_factor）
        try:
            raw = await page.screenshot(type="png", full_page=False)
            if raw and len(raw) >= 1000:
                logger.warning(
                    f"[a_heatmap] 官方截图分享不可用，已高清视口兜底 "
                    f"dpr={self.device_scale_factor} bytes={len(raw)}"
                )
                return raw
        except Exception as e:
            logger.warning(f"[a_heatmap] 视口截图失败: {e}")

        for selector in ("#treemap", "canvas#treemap"):
            loc = page.locator(selector).first
            try:
                if await loc.count() == 0:
                    continue
                if not await loc.is_visible():
                    try:
                        await loc.wait_for(state="visible", timeout=3000)
                    except Exception:
                        continue
                raw = await loc.screenshot(type="png")
                if raw and len(raw) >= 1000:
                    logger.warning(
                        f"[a_heatmap] 视口截图失败，已兜底截取 {selector}"
                    )
                    return raw
            except Exception as e:
                logger.warning(f"[a_heatmap] 元素截图失败 {selector}: {e}")

        raise SiteCaptureError("官方导出与高清视口截图兜底均失败")

    async def _reset_browser(self) -> None:
        browser, pw_cm = self._browser, self._playwright_cm
        self._browser = None
        self._pw = None
        self._playwright_cm = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass
        if pw_cm is not None:
            try:
                await pw_cm.__aexit__(None, None, None)
            except Exception:
                pass


def _data_url_to_bytes(data_url: str) -> bytes:
    if "," not in data_url:
        raise SiteCaptureError("dataURL 格式无效")
    b64 = data_url.split(",", 1)[1]
    try:
        return base64.b64decode(b64)
    except Exception as e:
        raise SiteCaptureError(f"dataURL base64 解码失败: {e}") from e
