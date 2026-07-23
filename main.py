"""A 股大盘热力图插件

发送指令生成实时行情热力图，支持定时推送到群/私聊。

功能：
- 主路径「热力图」：Playwright 抓取 52etf.site 官方截图分享图
- 备路径「热力图2」：本地 matplotlib + squarify 自绘 treemap
- 定时推送：按 Cron 表达式自动推送到配置的目标群/用户
- 多触发词、斜杠忽略、冷却限制、管理员限制
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import timezone, timedelta

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

from .data_fetcher import fetch_all
from .renderer import render_treemap
from .site_capture import SiteCaptureError, SiteHeatmapCapturer

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    _APS_AVAILABLE = True
except ImportError:
    _APS_AVAILABLE = False

TZ = timezone(timedelta(hours=8))

PLUGIN_NAME = "astrbot_plugin_a_heatmap"


def _parse_keywords(raw: str | None, default: str) -> list[str]:
    text = (raw if raw is not None else default) or default
    kws = [k.strip() for k in str(text).split(",") if k.strip()]
    return kws or [default]


@register(
    PLUGIN_NAME,
    "konley",
    "A股大盘热力图：52etf官方图 + 本地自绘(热力图2)，支持定时推送",
    "0.2.1",
)
class AHeatmapPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.scheduler = None
        self._last_call: dict[str, float] = {}  # user_id -> timestamp

        self._keywords = _parse_keywords(self.config.get("command_keywords"), "热力图")
        self._keywords_legacy = _parse_keywords(
            self.config.get("command_keywords_legacy"), "热力图2"
        )
        self._ignore_slash = self.config.get("ignore_slash", True)
        self._admin_only = self.config.get("admin_only", False)
        self._cooldown = max(0, int(self.config.get("cooldown", 30) or 30))
        self._fallback_on_site_fail = bool(
            self.config.get("fallback_to_legacy_on_site_fail", False)
        )

        self._capturer = SiteHeatmapCapturer(
            url=(self.config.get("site_url") or "https://52etf.site/").strip()
            or "https://52etf.site/",
            viewport={
                "width": max(800, int(self.config.get("site_viewport_width", 1400) or 1400)),
                "height": max(600, int(self.config.get("site_viewport_height", 900) or 900)),
            },
            goto_timeout_ms=max(
                5000, int(self.config.get("site_goto_timeout_ms", 45000) or 45000)
            ),
            ready_wait_ms=max(0, int(self.config.get("site_ready_wait_ms", 8000) or 8000)),
            export_timeout_ms=max(
                3000, int(self.config.get("site_export_timeout_ms", 15000) or 15000)
            ),
        )

        if self.config.get("schedule_enable"):
            self._setup_scheduler()

    # ──────────────────────────────────────────────
    # 生成热力图
    # ──────────────────────────────────────────────

    async def _generate_site_heatmap(self) -> str | None:
        """52etf 官方图，返回本地 PNG 路径。"""
        data_dir = self._get_data_dir()
        out_path = os.path.join(data_dir, f"heatmap_site_{int(time.time())}.png")
        try:
            return await self._capturer.capture_to_file(out_path)
        except SiteCaptureError as e:
            logger.error(f"[a_heatmap] 52etf 抓图失败: {e}")
            return None
        except Exception as e:
            logger.error(f"[a_heatmap] 52etf 抓图异常: {e}")
            return None

    async def _generate_legacy_heatmap(self) -> str | None:
        """本地 matplotlib 自绘。"""
        try:
            stocks, indices, up_down = await fetch_all()
        except Exception as e:
            logger.error(f"[a_heatmap] 数据获取失败: {e}")
            return None

        if not stocks:
            logger.error("[a_heatmap] 未获取到股票数据")
            return None

        data_dir = self._get_data_dir()
        out_path = os.path.join(data_dir, f"heatmap_legacy_{int(time.time())}.png")

        max_ind = int(self.config.get("max_industries", 35) or 35)
        max_stk = int(self.config.get("max_stocks_per_industry", 50) or 50)
        dpi = int(self.config.get("dpi", 130) or 130)

        try:
            await asyncio.to_thread(
                render_treemap,
                stocks,
                indices,
                up_down,
                out_path,
                max_industries=max_ind,
                max_stocks_per_industry=max_stk,
                dpi=dpi,
            )
        except Exception as e:
            logger.error(f"[a_heatmap] 渲染失败: {e}")
            return None

        return out_path

    async def _generate_primary(self) -> tuple[str | None, str]:
        """主路径：52etf，可选失败降级自绘。返回 (path, mode)。"""
        path = await self._generate_site_heatmap()
        if path:
            return path, "site"
        if self._fallback_on_site_fail:
            logger.warning("[a_heatmap] 52etf 失败，降级本地自绘")
            path = await self._generate_legacy_heatmap()
            if path:
                return path, "legacy_fallback"
        return None, "site"

    def _get_data_dir(self) -> str:
        """获取 AstrBot data 目录下本插件的子目录。"""
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            base = get_astrbot_data_path()
        except Exception:
            base = os.path.join(os.path.dirname(__file__), "..", "data")
        d = os.path.join(base, "a_heatmap")
        os.makedirs(d, exist_ok=True)
        return d

    def _cleanup(self, path: str):
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # 指令处理
    # ──────────────────────────────────────────────

    @staticmethod
    def _is_admin(event: AstrMessageEvent) -> bool:
        role = getattr(event, "role", None)
        if role is not None:
            return role == "admin"
        checker = getattr(event, "is_admin", None)
        if callable(checker):
            try:
                return bool(checker())
            except Exception:
                return False
        return False

    def _check_cooldown(self, user_id: str) -> bool:
        if self._cooldown <= 0:
            return True
        now = time.time()
        last = self._last_call.get(user_id, 0)
        if now - last < self._cooldown:
            return False
        self._last_call[user_id] = now
        return True

    def _normalize_text(self, text: str) -> str:
        check = text.strip()
        if self._ignore_slash:
            check = check.lstrip("/#").strip()
        return check

    def _match_mode(self, text: str) -> str | None:
        """返回 'site' | 'legacy' | None。优先匹配更长/更具体的热力图2。"""
        check = self._normalize_text(text)
        if not check:
            return None
        for kw in self._keywords_legacy:
            if check == kw:
                return "legacy"
        for kw in self._keywords:
            if check == kw:
                return "site"
        return None

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        text = event.message_str.strip()
        if not text:
            return

        mode = self._match_mode(text)
        if mode is None:
            return

        event.stop_event()

        if self._admin_only and not self._is_admin(event):
            yield event.plain_result("该指令已设置为仅管理员可用。")
            return

        user_id = event.get_sender_id()
        if not self._check_cooldown(user_id):
            yield event.plain_result("生成中，请稍后再试～")
            return

        if mode == "legacy":
            img_path = await self._generate_legacy_heatmap()
            fail_msg = "本地热力图生成失败，请稍后重试。"
        else:
            img_path, used = await self._generate_primary()
            if used == "legacy_fallback" and img_path:
                fail_msg = ""
            else:
                fail_msg = (
                    "官方热力图获取失败（浏览器/页面超时）。"
                    "可稍后重试，或发送「热力图2」使用本地自绘。"
                )

        if img_path is None:
            yield event.plain_result(fail_msg or "热力图生成失败，请稍后重试。")
            return

        try:
            yield event.chain_result([Comp.Image(file=img_path)])
        finally:
            self._cleanup(img_path)

    # ──────────────────────────────────────────────
    # 主动推送基础设施
    # ──────────────────────────────────────────────

    def _resolve_platform_id(self) -> str | None:
        configured = (self.config.get("schedule_platform") or "").strip()

        pm = getattr(self.context, "platform_manager", None)
        insts = getattr(pm, "platform_insts", None)
        if not insts:
            insts_dict = getattr(pm, "platforms", None)
            if isinstance(insts_dict, dict) and insts_dict:
                if configured and configured in insts_dict:
                    return configured
                return next(iter(insts_dict.keys()), None)
            return configured or None

        if configured:
            for inst in insts:
                try:
                    if inst.meta().id == configured:
                        return configured
                except Exception:
                    pass

        if configured:
            for inst in insts:
                try:
                    ptype = type(inst).__name__
                    if configured.lower() in ptype.lower():
                        return inst.meta().id
                except Exception:
                    pass

        for inst in insts:
            try:
                ptype = type(inst).__name__
                if "aiocqhttp" in ptype.lower():
                    return inst.meta().id
            except Exception:
                pass

        for inst in insts:
            try:
                return inst.meta().id
            except Exception:
                pass
        return configured or None

    def _build_session(self, target_id: str) -> str | None:
        target_type = (self.config.get("schedule_target_type") or "group").strip()
        platform = self._resolve_platform_id() or "aiocqhttp"
        msg_type = "GroupMessage" if target_type == "group" else "PrivateMessage"
        return f"{platform}:{msg_type}:{target_id}"

    async def _send_image(self, target_id: str, img_path: str):
        session = self._build_session(target_id)
        if not session:
            logger.warning(f"[a_heatmap] 无法构造推送会话，target={target_id}")
            return
        try:
            await self.context.send_message(
                session,
                MessageChain().message(Comp.Image(file=img_path)),
            )
            logger.info(f"[a_heatmap] 已推送到 {session}")
        except Exception as e:
            logger.error(f"[a_heatmap] 推送失败 {session}: {e}")

    # ──────────────────────────────────────────────
    # 调度器
    # ──────────────────────────────────────────────

    def _setup_scheduler(self):
        if not _APS_AVAILABLE:
            logger.error("[a_heatmap] 未安装 apscheduler，定时推送不可用。")
            return

        self.scheduler = AsyncIOScheduler(timezone=TZ)
        cron = (self.config.get("schedule_cron") or "0 15 * * 1-5").strip()
        try:
            trigger = CronTrigger.from_crontab(cron, timezone=TZ)
            self.scheduler.add_job(self._scheduled_push, trigger, id="a_heatmap_push")
            logger.info(f"[a_heatmap] 定时推送已启用，Cron='{cron}'。")
        except ValueError as e:
            logger.error(f"[a_heatmap] Cron 表达式无效：'{cron}'，错误：{e}")

        if self.scheduler.get_jobs():
            self.scheduler.start()

    async def _scheduled_push(self):
        targets = self.config.get("schedule_targets") or []
        if not targets:
            logger.warning("[a_heatmap] 定时推送触发，但未配置推送目标。")
            return

        schedule_mode = (self.config.get("schedule_mode") or "site").strip().lower()
        if schedule_mode in ("legacy", "local", "热力图2"):
            img_path = await self._generate_legacy_heatmap()
        else:
            img_path, _ = await self._generate_primary()

        if img_path is None:
            logger.error("[a_heatmap] 定时推送：热力图生成失败。")
            return

        try:
            for target_id in targets:
                tid = str(target_id).strip()
                if tid:
                    await self._send_image(tid, img_path)
                    await asyncio.sleep(0.5)
        finally:
            self._cleanup(img_path)

    # ──────────────────────────────────────────────
    # 生命周期
    # ──────────────────────────────────────────────

    async def terminate(self):
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            self.scheduler = None
        try:
            await self._capturer.close()
        except Exception:
            pass
