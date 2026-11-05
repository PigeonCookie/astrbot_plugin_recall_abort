"""撤回即作废 —— 消息被撤回后，正在生成的回复立刻掐掉，绝不发出去。

逻辑：
1. 监听 notice 事件（group_recall / friend_recall），把被撤回的 message_id 记进缓存。
2. on_llm_request：记录正在处理的 event，如果它已经被撤回，直接停，不浪费 token。
3. 撤回瞬间：如果会话里正在处理的就是这条消息，立刻给 event 上 stop_event 标记。
4. on_decorating_result（预发送前）：命中撤回缓存就 clear_result + stop_event，
   保证「他撤回了，我就闭嘴」，一个字都不漏出去。
"""

from __future__ import annotations

import time
import traceback

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

RECALL_NOTICE_TYPES = (
    "group_recall",
    "friend_recall",
    "group_recall_msg",
    "friend_recall_msg",
    "recall",
)


@register(
    "astrbot_plugin_recall_abort",
    "咕子曲奇GuZzz",
    "撤回即作废：用户撤回消息后，正在分析的回复在发送前被丢弃，立刻停止分析",
    "1.0.0",
)
class RecallAbortPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig | None = None):
        super().__init__(context)
        self.config = config or {}
        self.enabled = bool(self.config.get("enable", True))
        self.window = float(self.config.get("window", 120) or 120)
        self.notify = bool(self.config.get("notify", True))
        self.recalled: dict[str, float] = {}
        self.inflight: dict[str, tuple[str, AstrMessageEvent, float]] = {}

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------
    def _mid(self, event: AstrMessageEvent) -> str:
        try:
            mid = str(getattr(event.message_obj, "message_id", "") or "").strip()
        except Exception:
            return ""
        if mid.lower() in ("", "none", "null", "0"):
            return ""
        return mid

    def _gc(self) -> None:
        now = time.time()
        for k, t in list(self.recalled.items()):
            if now - t > self.window:
                self.recalled.pop(k, None)
        for k, v in list(self.inflight.items()):
            if now - v[2] > self.window:
                self.inflight.pop(k, None)

    def _is_recalled(self, mid: str) -> bool:
        if not mid:
            return False
        self._gc()
        return mid in self.recalled

    def _drop(self, event: AstrMessageEvent, why: str) -> None:
        try:
            event.stop_event()
        except Exception:
            pass
        try:
            event.clear_result()
        except Exception:
            logger.warning("[recall_abort] clear_result 失败，可能会漏发一条")
        if self.notify:
            logger.info(f"[recall_abort] 消息已被撤回，回复作废（{why}）")

    # ------------------------------------------------------------------
    # 钩子一：抓撤回通知
    # ------------------------------------------------------------------
    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def catch_recall(self, event: AstrMessageEvent) -> None:
        try:
            raw = getattr(event.message_obj, "raw_message", None) or {}
            if not isinstance(raw, dict):
                return
            notice_type = str(raw.get("notice_type", "") or "")
            if notice_type not in RECALL_NOTICE_TYPES:
                return

            mid = str(raw.get("message_id", "") or "").strip()
            if not mid:
                return

            self.recalled[mid] = time.time()
            if self.notify:
                logger.info(f"[recall_abort] 检测到撤回：message_id={mid}")

            # 正在处理这条消息的话，立刻掐掉
            for sess, (pending_mid, pending_ev, _ts) in list(self.inflight.items()):
                if pending_mid and pending_mid == mid:
                    self._drop(pending_ev, "分析途中被撤回")
                    self.inflight.pop(sess, None)

            event.stop_event()
        except Exception:
            logger.error(f"[recall_abort] catch_recall 出错：\n{traceback.format_exc()}")

    # ------------------------------------------------------------------
    # 钩子二：进 LLM 前先确认人还在
    # ------------------------------------------------------------------
    @filter.on_llm_request(priority=1)
    async def check_before_llm(self, event: AstrMessageEvent, req) -> None:
        try:
            if not self.enabled:
                return
            mid = self._mid(event)
            if not mid:
                return
            self.inflight[event.unified_msg_origin] = (mid, event, time.time())
            if self._is_recalled(mid):
                self._drop(event, "请求前已撤回")
        except Exception:
            logger.error(f"[recall_abort] check_before_llm 出错：\n{traceback.format_exc()}")

    # ------------------------------------------------------------------
    # 钩子三：预发送前最后一道闸
    # ------------------------------------------------------------------
    @filter.on_decorating_result(priority=1)
    async def block_send(self, event: AstrMessageEvent) -> None:
        try:
            if not self.enabled:
                return
            mid = self._mid(event)
            if self._is_recalled(mid):
                self._drop(event, "预发送前检测到撤回")
                return
            self.inflight.pop(event.unified_msg_origin, None)
        except Exception:
            logger.error(f"[recall_abort] block_send 出错：\n{traceback.format_exc()}")

    async def terminate(self) -> None:
        self.recalled.clear()
        self.inflight.clear()
