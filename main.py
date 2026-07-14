"""A 股大盘热力图插件

发送指令生成实时行情 treemap 截图，支持定时推送到群/私聊。

功能：
- 手动触发：发送配置的关键词（默认"热力图"）生成热力图
- 定时推送：按 Cron 表达式自动推送到配置的目标群/用户
- 多触发词：逗号分隔配置多个关键词
- 斜杠忽略：可选忽略 / 或 # 前缀
- 冷却限制：防止频繁刷图
- 管理员限制：可选仅管理员可用
"""

import asyncio
import os
import time
from datetime import datetime, timezone, timedelta

import httpx

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register
import astrbot.api.message_components as Comp

from .data_fetcher import fetch_all
from .renderer import render_treemap

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    _APS_AVAILABLE = True
except ImportError:
    _APS_AVAILABLE = False

TZ = timezone(timedelta(hours=8))

PLUGIN_NAME = "astrbot_plugin_a_heatmap"


@register(
    PLUGIN_NAME,
    "konley",
    "A股大盘热力图，发送指令生成实时行情 treemap 截图，支持定时推送",
    "0.1.0",
)
class AHeatmapPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.scheduler = None
        self._last_call: dict[str, float] = {}  # user_id -> timestamp

        # 解析触发关键词
        raw_kw = (self.config.get("command_keywords") or "热力图").strip()
        self._keywords = [k.strip() for k in raw_kw.split(",") if k.strip()]
        if not self._keywords:
            self._keywords = ["热力图"]

        self._ignore_slash = self.config.get("ignore_slash", True)
        self._admin_only = self.config.get("admin_only", False)
        self._cooldown = max(0, int(self.config.get("cooldown", 30) or 30))

        if self.config.get("schedule_enable"):
            self._setup_scheduler()

    # ──────────────────────────────────────────────
    # 生成热力图核心逻辑
    # ──────────────────────────────────────────────

    async def _generate_heatmap(self) -> str | None:
        """拉取数据 + 渲染热力图，返回图片文件路径。"""
        try:
            stocks, indices, up_down = await fetch_all()
        except Exception as e:
            logger.error(f"[a_heatmap] 数据获取失败: {e}")
            return None

        if not stocks:
            logger.error("[a_heatmap] 未获取到股票数据")
            return None

        # 临时文件存到 data 目录
        data_dir = self._get_data_dir()
        filename = f"heatmap_{int(time.time())}.png"
        out_path = os.path.join(data_dir, filename)

        max_ind = int(self.config.get("max_industries", 35) or 35)
        max_stk = int(self.config.get("max_stocks_per_industry", 50) or 50)
        dpi = int(self.config.get("dpi", 130) or 130)

        try:
            render_treemap(stocks, indices, up_down, out_path,
                           max_industries=max_ind, max_stocks_per_industry=max_stk,
                           dpi=dpi)
        except Exception as e:
            logger.error(f"[a_heatmap] 渲染失败: {e}")
            return None

        return out_path

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
        """删除临时图片文件。"""
        try:
            if os.path.exists(path):
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
        """返回 True 表示允许调用，False 表示在冷却中。"""
        if self._cooldown <= 0:
            return True
        now = time.time()
        last = self._last_call.get(user_id, 0)
        if now - last < self._cooldown:
            return False
        self._last_call[user_id] = now
        return True

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def on_message(self, event: AstrMessageEvent):
        """监听所有消息，匹配关键词触发。"""
        text = event.message_str.strip()
        if not text:
            return

        # 斜杠忽略
        check_text = text
        if self._ignore_slash:
            check_text = text.lstrip("/#").strip()

        matched = False
        for kw in self._keywords:
            if check_text == kw:
                matched = True
                break

        if not matched:
            return

        # 阻止后续插件处理
        event.stop_event()

        # 管理员检查
        if self._admin_only and not self._is_admin(event):
            yield event.plain_result("该指令已设置为仅管理员可用。")
            return

        # 冷却检查
        user_id = event.get_sender_id()
        if not self._check_cooldown(user_id):
            yield event.plain_result("生成中，请稍后再试～")
            return

        yield event.plain_result("正在生成 A 股热力图，请稍候...")

        img_path = await self._generate_heatmap()
        if img_path is None:
            yield event.plain_result("热力图生成失败，请稍后重试。")
            return

        try:
            yield event.chain_result([Comp.Image(file=img_path)])
        finally:
            self._cleanup(img_path)

    # ──────────────────────────────────────────────
    # 主动推送基础设施
    # ──────────────────────────────────────────────

    def _resolve_platform_id(self) -> str | None:
        """解析推送用的平台实例 ID。"""
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
        """构造统一会话标识。"""
        target_type = (self.config.get("schedule_target_type") or "group").strip()
        platform = self._resolve_platform_id() or "aiocqhttp"
        msg_type = "GroupMessage" if target_type == "group" else "PrivateMessage"
        return f"{platform}:{msg_type}:{target_id}"

    async def _send_image(self, target_id: str, img_path: str):
        """主动推送图片到目标会话。"""
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
        """定时任务回调：生成热力图并推送到所有配置目标。"""
        targets = self.config.get("schedule_targets") or []
        if not targets:
            logger.warning("[a_heatmap] 定时推送触发，但未配置推送目标。")
            return

        img_path = await self._generate_heatmap()
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
        """插件卸载/重载时停止调度器。"""
        if self.scheduler is not None:
            try:
                self.scheduler.shutdown(wait=False)
            except Exception:
                pass
            self.scheduler = None
