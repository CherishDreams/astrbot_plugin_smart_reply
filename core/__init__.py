"""EchoSense 核心模块"""
from .ledger import ConversationLedger
from .state_manager import StateManager, PluginState
from .detectors import detect_dense_conversation, detect_echo_chamber, should_trigger_active_state
from .detention import ProcessingLock, DetentionQueue, DetentionManager

__all__ = [
    "ConversationLedger",
    "StateManager",
    "PluginState",
    "detect_dense_conversation",
    "detect_echo_chamber",
    "should_trigger_active_state",
    "ProcessingLock",
    "DetentionQueue",
    "DetentionManager",
]