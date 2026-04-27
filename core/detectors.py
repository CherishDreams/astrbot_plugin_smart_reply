"""
Detectors - 活跃检测

检测群聊是否处于活跃讨论状态：
- 密集讨论检测：短时间内多人多消息
- 复读检测：同一内容被多次重复
"""
from typing import Optional
from .ledger import ConversationLedger


def detect_dense_conversation(
    ledger: ConversationLedger,
    session_id: str,
    threshold: int = 10,
    min_participants: int = 3,
    window_seconds: int = 60,
) -> bool:
    """
    检测密集讨论。

    条件：
    - 消息数量 >= threshold
    - 参与人数 >= min_participants
    - 时间窗口 <= window_seconds

    Args:
        ledger: 消息总账
        session_id: 会话 ID
        threshold: 消息数量阈值
        min_participants: 最少参与人数
        window_seconds: 时间窗口（秒）

    Returns:
        bool: 是否检测到密集讨论
    """
    messages = ledger.get_messages(session_id, window_seconds)
    message_count = len(messages)

    if message_count < threshold:
        return False

    # 统计参与人数
    participant_count = ledger.get_participant_count(session_id, window_seconds)

    return participant_count >= min_participants


def detect_echo_chamber(
    ledger: ConversationLedger,
    session_id: str,
    threshold: int = 3,
    window_seconds: int = 30,
) -> tuple[bool, Optional[str]]:
    """
    检测复读行为。

    条件：
    - 同一内容出现 >= threshold 次
    - 时间窗口 <= window_seconds
    - 只统计纯文字消息（跳过图片）

    Args:
        ledger: 消息总账
        session_id: 会话 ID
        threshold: 复读次数阈值
        window_seconds: 时间窗口（秒）

    Returns:
        tuple[bool, Optional[str]]:
            - 是否检测到复读
            - 被复读的内容（如果检测到）
    """
    content_counts = ledger.get_text_content_counts(session_id, window_seconds)

    for content, count in content_counts.items():
        if count >= threshold:
            return True, content

    return False, None


def should_trigger_active_state(
    ledger: ConversationLedger,
    session_id: str,
    dense_threshold: int = 10,
    dense_participants: int = 3,
    dense_window: int = 60,
    echo_threshold: int = 3,
    echo_window: int = 30,
) -> tuple[bool, str]:
    """
    检测是否应该从 NOT_PRESENT 转换到 GETTING_FAMILIAR 状态。

    Args:
        ledger: 消息总账
        session_id: 会话 ID
        其他参数为检测阈值

    Returns:
        tuple[bool, str]:
            - 是否应该转换状态
            - 触发原因
    """
    # 检测密集讨论
    if detect_dense_conversation(
        ledger, session_id,
        threshold=dense_threshold,
        min_participants=dense_participants,
        window_seconds=dense_window,
    ):
        return True, "密集讨论"

    # 检测复读
    is_echo, echo_content = detect_echo_chamber(
        ledger, session_id,
        threshold=echo_threshold,
        window_seconds=echo_window,
    )
    if is_echo:
        return True, f"复读检测: {echo_content}"

    return False, ""