"""
AnalysisDecision - 分析决策结果

LLM 分析后的决策输出结构。
"""
from dataclasses import dataclass, field


@dataclass
class AnalysisDecision:
    """LLM 分析决策结果"""

    should_reply: bool = False
    """是否应该回复"""

    topic: str = ""
    """当前话题概要"""

    reply_strategy: str = ""
    """回复策略建议"""

    reply_target: str = ""
    """回复目标用户"""

    reason: str = ""
    """判断理由"""

    # 评分维度
    relevance: float = 0.0
    willingness: float = 0.0
    social: float = 0.0
    timing: float = 0.0

    @property
    def overall_score(self) -> float:
        """综合评分（0-1）"""
        if self.relevance or self.willingness or self.social or self.timing:
            return (self.relevance + self.willingness + self.social + self.timing) / 40.0
        return 0.0

    def meets_threshold(self, threshold: float) -> bool:
        """检查综合评分是否达到阈值"""
        return self.overall_score >= threshold