"""
ConversationLedger - 消息总账

维护每个会话的消息缓存，支持：
- 消息有序插入
- 时间窗口查询
- 已处理/未处理标记
"""
import time
import threading
from typing import Optional


class ConversationLedger:
    """维护每个会话的消息缓存"""

    PER_CHAT_LIMIT = 100  # 单会话最大消息数
    MIN_RETAIN_COUNT = 10  # 剪枝时保留的最小消息数

    def __init__(self):
        self._ledgers: dict[str, list[dict]] = {}
        self._processed_marks: dict[str, float] = {}  # session_id -> last_processed_timestamp
        self._lock = threading.Lock()

    def add_message(self, session_id: str, message: dict) -> None:
        """
        添加消息到总账。
        message 应包含：timestamp, sender_id, sender_name, content, has_image
        """
        with self._lock:
            if session_id not in self._ledgers:
                self._ledgers[session_id] = []

            ledger = self._ledgers[session_id]

            # 添加消息
            ledger.append(message)

            # 检查是否需要剪枝
            if len(ledger) > self.PER_CHAT_LIMIT:
                self._prune_ledger(session_id)

    def get_messages(self, session_id: str, window_seconds: int = 0) -> list[dict]:
        """
        获取会话的消息列表。
        window_seconds > 0 时只返回时间窗口内的消息。
        """
        with self._lock:
            ledger = self._ledgers.get(session_id, [])
            if not ledger:
                return []

            if window_seconds <= 0:
                return list(ledger)

            current_time = time.time()
            window_start = current_time - window_seconds

            return [
                msg for msg in ledger
                if msg.get("timestamp", 0) >= window_start
            ]

    def get_unprocessed_messages(self, session_id: str) -> list[dict]:
        """
        获取未处理的消息（last_processed_timestamp 之后的消息）。
        """
        with self._lock:
            ledger = self._ledgers.get(session_id, [])
            if not ledger:
                return []

            last_processed = self._processed_marks.get(session_id, 0)

            return [
                msg for msg in ledger
                if msg.get("timestamp", 0) > last_processed
            ]

    def mark_processed(self, session_id: str) -> None:
        """
        标记当前所有消息为已处理。
        使用高水位标记（timestamp）而非逐条标记。
        """
        with self._lock:
            ledger = self._ledgers.get(session_id, [])
            if ledger:
                # 标记最新消息的时间戳为处理点
                latest_timestamp = max(msg.get("timestamp", 0) for msg in ledger)
                self._processed_marks[session_id] = latest_timestamp

    def get_participant_count(self, session_id: str, window_seconds: int) -> int:
        """
        获取时间窗口内的参与人数。
        """
        messages = self.get_messages(session_id, window_seconds)
        participants = set()
        for msg in messages:
            sender_id = msg.get("sender_id")
            if sender_id:
                participants.add(sender_id)
        return len(participants)

    def get_text_content_counts(self, session_id: str, window_seconds: int) -> dict[str, int]:
        """
        获取时间窗口内纯文本内容的出现次数统计。
        用于复读检测，跳过包含图片的消息。
        """
        messages = self.get_messages(session_id, window_seconds)
        content_counts: dict[str, int] = {}

        for msg in messages:
            # 跳过包含图片的消息
            if msg.get("has_image", False):
                continue

            content = msg.get("content", "").strip()
            if content:
                content_counts[content] = content_counts.get(content, 0) + 1

        return content_counts

    def clear_session(self, session_id: str) -> None:
        """清除指定会话的消息缓存"""
        with self._lock:
            self._ledgers.pop(session_id, None)
            self._processed_marks.pop(session_id, None)

    def clear_all(self) -> None:
        """清除所有会话的消息缓存"""
        with self._lock:
            self._ledgers.clear()
            self._processed_marks.clear()

    def _prune_ledger(self, session_id: str) -> None:
        """剪枝消息列表，保留最近的消息"""
        ledger = self._ledgers[session_id]
        if len(ledger) > self.MIN_RETAIN_COUNT:
            # 按时间戳排序后保留最近的
            ledger.sort(key=lambda m: m.get("timestamp", 0))
            self._ledgers[session_id] = ledger[-self.MIN_RETAIN_COUNT:]