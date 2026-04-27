"""
Detention - 事件扣押机制

使用 asyncio.Future 阻塞事件，让事件在 Pipeline 流程中等待分析完成。

核心概念：
- 门牌锁 (Processing Lock)：确保同一会话串行处理
- 扣押队列 (Detention Queue)：新消息等待旧消息处理完成
- 等候票 (Hold Ticket)：Future 对象，用于阻塞和唤醒
"""
import asyncio
import time
import threading
from typing import Optional
from astrbot.api import logger


class ProcessingLock:
    """
    门牌锁 - 确保同一会话串行处理。

    比喻：
    - 老板 = 正在处理消息的分析任务
    - 来访者 = 新消息事件
    - 门牌 = 锁标记（表示老板正在忙）
    """

    def __init__(self):
        self._locks: dict[str, tuple[float, str]] = {}  # session_id -> (timestamp, owner)
        self._lock_lock = threading.Lock()  # 保护 _locks 的线程锁

    def try_acquire(self, session_id: str, owner: str = "default") -> bool:
        """
        尝试获取门牌锁。

        Returns:
            bool: True 表示成功获取锁，False 表示锁已被占用
        """
        with self._lock_lock:
            if session_id in self._locks:
                return False

            self._locks[session_id] = (time.time(), owner)
            return True

    def release(self, session_id: str) -> None:
        """释放门牌锁"""
        with self._lock_lock:
            self._locks.pop(session_id, None)

    def is_locked(self, session_id: str) -> bool:
        """检查锁是否被占用"""
        with self._lock_lock:
            return session_id in self._locks

    def get_lock_info(self, session_id: str) -> Optional[tuple[float, str]]:
        """获取锁信息"""
        with self._lock_lock:
            return self._locks.get(session_id)


class DetentionQueue:
    """
    扣押队列 - 管理等待的事件。

    比喻：
    - 等候牌 = asyncio.Future
    - 候室 = 扣押队列
    - 叫号 = set_result()（通知可以继续处理）
    """

    def __init__(self):
        self._tickets: dict[str, asyncio.Future] = {}  # session_id -> Future
        self._ticket_lock = threading.Lock()

    def create_ticket(self, session_id: str) -> asyncio.Future:
        """
        创建等候票。

        Returns:
            asyncio.Future: 用于阻塞等待的 Future 对象
        """
        ticket = asyncio.Future()

        with self._ticket_lock:
            # 如果已有旧票，先解除它（KILL）
            old_ticket = self._tickets.get(session_id)
            if old_ticket and not old_ticket.done():
                old_ticket.set_result("KILL")

            self._tickets[session_id] = ticket

        return ticket

    def resolve_ticket(self, session_id: str, result: str = "PROCESS") -> None:
        """
        解除等候票（叫号）。

        Args:
            session_id: 会话 ID
            result: "PROCESS" 表示继续处理，"KILL" 表示终止
        """
        with self._ticket_lock:
            ticket = self._tickets.get(session_id)
            if ticket and not ticket.done():
                ticket.set_result(result)
            self._tickets.pop(session_id, None)

    def has_pending_ticket(self, session_id: str) -> bool:
        """检查是否有待处理的票"""
        with self._ticket_lock:
            ticket = self._tickets.get(session_id)
            return ticket is not None and not ticket.done()


class DetentionManager:
    """
    扣押管理器 - 统一管理门牌锁和扣押队列。
    """

    def __init__(self, debug_mode: bool = False):
        self.lock = ProcessingLock()
        self.queue = DetentionQueue()
        self.debug_mode = debug_mode

    def _log(self, message: str) -> None:
        """输出日志"""
        if self.debug_mode:
            logger.info(f"[EchoSense] {message}")
        else:
            logger.debug(f"[EchoSense] {message}")

    async def acquire_or_wait(self, session_id: str, event_id: str) -> tuple[bool, Optional[asyncio.Future]]:
        """
        获取处理权：要么立即获取门牌，要么取票等待。

        Args:
            session_id: 会话 ID
            event_id: 事件标识（用于日志）

        Returns:
            tuple[bool, Optional[Future]]:
                - True, None: 成功获取门牌，可以直接处理
                - False, Future: 需要等待，返回等候票
        """
        if self.lock.try_acquire(session_id, event_id):
            self._log(f"门牌获取成功: session={session_id}")
            return True, None

        self._log(f"门牌被占用，取票等待: session={session_id}")
        ticket = self.queue.create_ticket(session_id)
        return False, ticket

    def release_and_resolve(self, session_id: str) -> None:
        """
        释放门牌并唤醒等待者。
        """
        self._log(f"释放门牌: session={session_id}")

        # 先唤醒等待者
        self.queue.resolve_ticket(session_id, "PROCESS")

        # 再释放门牌
        self.lock.release(session_id)

    def kill_pending(self, session_id: str) -> None:
        """
        终止等待者（新消息取代旧消息）。
        """
        self._log(f"终止等待票: session={session_id}")
        self.queue.resolve_ticket(session_id, "KILL")

    def clear_all(self) -> None:
        """清除所有锁和票"""
        # 终止所有待处理的票
        for session_id in list(self.queue._tickets.keys()):
            self.queue.resolve_ticket(session_id, "KILL")

        # 清除所有锁
        self.lock._locks.clear()