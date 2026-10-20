# -*- coding: utf-8 -*-
"""群聊里被 @ 一次之后，把这段对话接住。

问题
----
    AstrBot 的唤醒判定是**逐条**的：每条群消息都要 @ 或唤醒前缀，否则
    `WakingCheckStage` 直接 `stop_event()`。所以真实体验是：

        你 @ 她 → 她回一句
        你接着说"那你说说看" → 没人理          ← 对话断了

    而人不是这样聊天的。被叫到之后，接下来的几句话默认还在跟她说。

做法
----
    记住"这个群最近一次被她接住的时间"，在这个窗口内放行普通消息；
    每放行一条就续一次窗，所以对话持续时窗口一直不断。

    - 被 @ → 开窗
    - 窗口内 → 放行并续窗
    - 窗口外 → 照旧拦掉（和框架默认行为一致）
    - @ 了别人（不是她）→ 拦掉，别插嘴
    - 命令（wake_prefix 开头）→ 放行

    窗口默认 120 秒、同群冷却 90 秒，按群独立。
"""

from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import At
from astrbot.api.star import Context, Star

PLUGIN_NAME = "astrbot_plugin_wake_window"

DEFAULT_CONFIG = {
    "enabled": True,
    # 与 _conf_schema.json 的 default 保持一字不差：配置文件丢了/被重置时
    # 回退到的是这里，两边不一致会让冷却悄悄变回旧值（2026-10-20 已踩过）。
    "window_seconds": 120,
    "wake_cooldown_seconds": 90,
    "groups": [],          # 留空 = 所有群
    "log": False,
    # 先发图、再 @ 她 —— 两条消息本来各走各的，@ 那条看不到那张图。
    # 这里把同一成员在 @ 之前 N 秒内的消息并进来。
    "merge_previous_seconds": 90,
    "merge_max_messages": 3,
}


class WakeWindowPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.cfg = dict(DEFAULT_CONFIG)
        if isinstance(config, dict):
            self.cfg.update({k: v for k, v in config.items() if k in DEFAULT_CONFIG})
        self._until: dict[str, float] = {}
        self._last_wake: dict[str, float] = {}
        self._last_extend_log: dict[str, float] = {}
        # gid -> [{"uid","ts","chain","text"}]，只留最近几条，用来给 @ 补前文
        self._recent: dict[str, list[dict]] = {}

    def _cfg(self, key):
        return self.cfg.get(key, DEFAULT_CONFIG.get(key))

    async def initialize(self) -> None:
        if not self._cfg("enabled"):
            logger.info("[wake-window] 关着")
            return
        logger.info(
            f"[wake-window] 就绪：被 @ 后 {self._cfg('window_seconds')} 秒内继续接话"
            f"｜范围 {self._cfg('groups') or '所有群'}"
        )

    def _cooldown(self) -> float:
        try:
            return max(0.0, float(self._cfg("wake_cooldown_seconds") or 90))
        except Exception:  # noqa: BLE001
            return 90.0

    def _groups(self) -> set[str]:
        return {str(g).strip() for g in (self._cfg("groups") or []) if str(g).strip()}

    def _at_bot(self, event: AstrMessageEvent) -> bool:
        self_id = str(event.get_self_id() or "")
        try:
            for m in event.get_messages():
                if isinstance(m, At) and str(getattr(m, "qq", "")) == self_id:
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _at_other(self, event: AstrMessageEvent) -> bool:
        self_id = str(event.get_self_id() or "")
        try:
            for m in event.get_messages():
                if isinstance(m, At) and str(getattr(m, "qq", "")) != self_id:
                    return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _prefixes(self, event: AstrMessageEvent) -> list[str]:
        try:
            cfg = self.context.get_config(umo=event.unified_msg_origin)
            return [str(p) for p in (cfg.get("wake_prefix") or []) if str(p)]
        except Exception:  # noqa: BLE001
            return []

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def keep_window(self, event: AstrMessageEvent):
        if not self._cfg("enabled"):
            return
        try:
            if event.is_private_chat():
                return
        except Exception:  # noqa: BLE001
            return

        gid = str(event.get_group_id() or "").strip()
        if not gid:
            return
        scope = self._groups()
        if scope and gid not in scope:
            return

        now = time.time()
        window = max(10, int(self._cfg("window_seconds") or 120))
        text = (event.message_str or "").strip()
        uid = str(event.get_sender_id() or "").strip()

        # 0) 每条群消息先入缓冲区（含图片），给后面的 @ 补前文用。
        #    @ 了别人的消息不缓冲：那是他跟别人的话，别在下次 @ 她时被并进来。
        entry = None if self._at_other(event) else self._remember(gid, uid, event, now)

        # 1) @ 她 → 开窗，并把同一成员刚发的图/话并进来
        if self._at_bot(event):
            self._until[gid] = now + window
            self._last_wake[gid] = now   # 被 @ 也算一次"刚说过话"，冷却从现在起算
            self._mark_awake(event)
            merged = self._merge_previous(gid, uid, event, now, exclude=entry)
            if self._cfg("log"):
                logger.info(
                    f"[wake-window] {gid} 被 @，开窗 {window}s"
                    + (f"，并入前 {merged} 条" if merged else "")
                )
            return

        # 2) 命令（唤醒前缀开头）→ 交给框架
        if any(text.startswith(p) for p in self._prefixes(event)):
            return

        # 3) @ 了别人、没 @ 她 → 不接
        if self._at_other(event):
            event.stop_event()
            return

        # 4) 窗口内 → 放行并续窗，但要冷却：
        #    窗口放开后每条消息都会真的唤醒她，群里一热闹就变成逐条回复（19:15–19:40 实测
        #    17 条用户消息换来 16 条回复）。所以同一群 cooldown 秒内只唤醒一次。
        if now < self._until.get(gid, 0.0):
            self._until[gid] = now + window
            if now - self._last_wake.get(gid, 0.0) >= self._cooldown():
                self._last_wake[gid] = now
                self._mark_awake(event)
                return
            if self._cfg("log"):
                last = self._last_extend_log.get(gid, 0.0)
                if now - last > 30:
                    self._last_extend_log[gid] = now
                    logger.info(f"[wake-window] {gid} 窗口内续窗（冷却中，本条不唤醒）")
            event.stop_event()
            return

        # 5) 窗口外 → 和默认行为一致，拦掉
        event.stop_event()

    @staticmethod
    def _mark_awake(event: AstrMessageEvent) -> None:
        """把这条消息标成"已唤醒"。

        只"不拦"是不够的：ProcessStage 发起 LLM 的条件是
        `not event._has_send_oper and event.is_at_or_wake_command and not event.call_llm`
        （astrbot/core/pipeline/process_stage/stage.py:57）。窗口内的消息没有 @，
        这个标志是 False，于是管线跑完 handlers 就直接 completed，一次请求都不发。
        2026-10-20 19:04 那次"对方解释了但她没接"就是这个原因。
        """
        try:
            event.is_wake = True
            event.is_at_or_wake_command = True
        except Exception:  # noqa: BLE001
            pass

    def _remember(self, gid: str, uid: str, event: AstrMessageEvent, now: float):
        try:
            chain = list(event.get_messages())
        except Exception:  # noqa: BLE001
            return None
        if not chain:
            return None
        buf = self._recent.setdefault(gid, [])
        entry = {
            "uid": uid,
            "ts": now,
            "chain": chain,
            "text": (event.message_str or "").strip(),
        }
        buf.append(entry)
        # 只留最近 8 条、且 60 秒内
        cutoff = now - 60
        self._recent[gid] = [x for x in buf if x["ts"] >= cutoff][-8:]
        return entry

    def _merge_previous(self, gid: str, uid: str, event: AstrMessageEvent, now: float,
                        exclude=None) -> int:
        """把同一成员在 @ 之前 N 秒内的消息并进当前事件。返回并入条数。"""
        span = max(0, int(self._cfg("merge_previous_seconds") or 0))
        cap = max(0, int(self._cfg("merge_max_messages") or 3))
        if span <= 0 or cap <= 0:
            return 0

        buf = self._recent.get(gid) or []
        prev = [
            x for x in buf
            if x is not exclude and x["uid"] == uid and now - x["ts"] <= span
        ][-cap:]
        if not prev:
            return 0

        extra_chain = []
        extra_text = []
        for x in prev:
            # 前文里的 At 丢掉：当前这条已经有 @ 了，再并进来会变成双重艾特。
            extra_chain.extend(m for m in x["chain"] if not isinstance(m, At))
            if x["text"]:
                extra_text.append(x["text"])

        try:
            cur = list(event.get_messages())
            merged = extra_chain + cur
            event.message_obj.message = merged
            if extra_text:
                prefix = " ".join(extra_text)
                event.message_str = f"{prefix} {event.message_str or ''}".strip()
                event.message_obj.message_str = event.message_str
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[wake-window] 合并前文失败: {e}")
            return 0

        # 用过的从缓冲里去掉，避免下一句再并一遍。
        # 用 id() 而不是字典相等：同一个人连发两句一样的话时，== 会把两条都判成"用过"。
        used = {id(x) for x in prev}
        self._recent[gid] = [x for x in buf if id(x) not in used]
        return len(prev)

    async def terminate(self) -> None:
        self._until.clear()
        self._last_wake.clear()
        self._last_extend_log.clear()
        self._recent.clear()
