"""
StateManager - 状态管理

管理插件的四状态机制：
- NOT_PRESENT: 默认状态，仅缓存消息
- SUMMONED: 被@或唤醒词，必须回复
- GETTING_FAMILIAR: 检测到活跃讨论，尝试融入
- OBSERVATION: 主动介入后，智能判断是否继续
"""
import time
import threading
from enum import Enum


class PluginState(Enum):
    """插件状态枚举"""
    NOT_PRESENT = "not_present"
    SUMMONED = "summoned"
    GETTING_FAMILIAR = "familiar"
    OBSERVATION = "observation"


class StateManager:
    """状态管理器"""

    def __init__(self, observation_timeout: int = 600):
        """
        Args:
            observation_timeout: 观察期超时时间（秒）
        """
        self.observation_timeout = observation_timeout
        self._states: dict[str, PluginState] = {}
        self._state_timestamps: dict[str, float] = {}  # 状态进入时间
        self._lock = threading.Lock()

    def get_state(self, session_id: str) -> PluginState:
        """获取当前状态"""
        with self._lock:
            state = self._states.get(session_id, PluginState.NOT_PRESENT)

            # 检查观察期超时
            if state == PluginState.OBSERVATION:
                entry_time = self._state_timestamps.get(session_id, 0)
                if time.time() - entry_time > self.observation_timeout:
                    # 超时，回到不在场状态
                    self._states[session_id] = PluginState.NOT_PRESENT
                    return PluginState.NOT_PRESENT

            return state

    def transition(self, session_id: str, new_state: PluginState) -> PluginState:
        """
        状态转换。
        返回转换后的状态。
        """
        with self._lock:
            old_state = self._states.get(session_id, PluginState.NOT_PRESENT)

            # 记录状态进入时间
            self._state_timestamps[session_id] = time.time()
            self._states[session_id] = new_state

            return new_state

    def is_active(self, session_id: str) -> bool:
        """
        检查是否处于活跃状态（需要处理消息）。
        NOT_PRESENT 状态下只缓存消息，不判断。
        """
        state = self.get_state(session_id)
        return state != PluginState.NOT_PRESENT

    def is_summoned(self, session_id: str) -> bool:
        """检查是否处于被呼唤状态"""
        return self.get_state(session_id) == PluginState.SUMMONED

    def reset(self, session_id: str) -> None:
        """重置状态到默认"""
        with self._lock:
            self._states[session_id] = PluginState.NOT_PRESENT
            self._state_timestamps[session_id] = time.time()

    def clear_all(self) -> None:
        """清除所有状态"""
        with self._lock:
            self._states.clear()
            self._state_timestamps.clear()