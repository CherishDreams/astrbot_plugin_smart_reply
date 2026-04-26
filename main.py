"""
AstrBot 智能回复判断插件
根据上下文智能判断是否应该回复消息，并进行基础任务规划

基于 AstrBot 官方 helloworld 模板开发
"""

import json
import re
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger


@register("astrbot_plugin_smart_reply", "智能回复判断插件", "根据上下文智能判断是否回复消息，并进行基础任务规划", "0.0.1")
class SmartReplyPlugin(Star):
    """智能回复判断插件"""

    # 默认判断 Prompt 模板
    DEFAULT_JUDGE_PROMPT = """你是一个智能对话判断助手。请分析以下对话上下文，判断机器人是否应该回复当前消息。

判断标准：
1. 消息是否针对机器人或需要机器人参与
2. 是否有明确的回复意图或问题
3. 上下文是否需要延续对话

历史对话：
{history}

当前消息：{current_msg}
发送者：{sender}

请严格按照以下JSON格式输出结果，不要输出其他内容：
{"should_reply": true或false, "reason": "判断理由简述", "task": "任务规划描述（如需回复）"}"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.filter_mode = "none"
        self.filter_list = []
        self.history_count = 10
        self.judge_prompt = self.DEFAULT_JUDGE_PROMPT

    async def initialize(self):
        """插件初始化方法，加载配置"""
        config = self.context.get_config() or {}

        # 基础设置
        basic = config.get("basic", {})
        self.filter_mode = basic.get("filter_mode", "none")
        self.filter_list = basic.get("filter_list", [])
        self.history_count = basic.get("history_count", 10)

        # 提示词设置
        prompt_config = config.get("prompt", {})
        self.judge_prompt = prompt_config.get("judge_prompt", self.DEFAULT_JUDGE_PROMPT)

        logger.info(f"智能回复插件初始化完成: 模式={self.filter_mode}, 过滤列表={len(self.filter_list)}条, 历史数量={self.history_count}")

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查消息是否应该被处理"""
        if self.filter_mode == "none":
            return True

        # 获取会话ID
        session_id = event.unified_msg_origin
        group_id = event.get_group_id()
        sender_id = event.message_obj.sender.user_id if event.message_obj.sender else None

        # 检查是否在列表中（支持完整会话ID或简写ID）
        in_list = False
        for item in self.filter_list:
            # 完整会话ID匹配
            if item == session_id:
                in_list = True
                break
            # 简写ID匹配（群号或用户ID）
            if group_id and str(group_id) == str(item):
                in_list = True
                break
            if sender_id and str(sender_id) == str(item):
                in_list = True
                break

        # whitelist 模式：在列表中才处理
        # blacklist 模式：不在列表中才处理
        if self.filter_mode == "whitelist":
            return in_list
        elif self.filter_mode == "blacklist":
            return not in_list

        return True

    async def _get_history(self, event: AstrMessageEvent) -> str:
        """获取对话历史"""
        try:
            uid = event.unified_msg_origin
            conv_mgr = self.context.conversation_manager

            if not conv_mgr:
                logger.warning("对话管理器不可用")
                return ""

            curr_cid = await conv_mgr.get_curr_conversation_id(uid)
            if not curr_cid:
                return ""

            conversation = await conv_mgr.get_conversation(uid, curr_cid)
            if not conversation or not conversation.history:
                return ""

            history_text = conversation.history

            # 截取最近的历史
            if self.history_count > 0:
                lines = history_text.split('\n')
                if len(lines) > self.history_count * 2:
                    lines = lines[-(self.history_count * 2):]
                history_text = '\n'.join(lines)

            return history_text

        except Exception as e:
            logger.error(f"获取对话历史失败: {e}")
            return ""

    def _build_judge_prompt(self, history: str, current_msg: str, sender: str) -> str:
        """构建判断 Prompt"""
        return self.judge_prompt.format(
            history=history or "无历史对话",
            current_msg=current_msg,
            sender=sender or "未知用户"
        )

    def _parse_result(self, response_text: str) -> tuple:
        """解析 LLM 返回结果"""
        try:
            # 提取 JSON
            json_match = re.search(r'\{[^{}]*"should_reply"[^{}]*\}', response_text, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))

                should_reply = result.get("should_reply", False)
                reason = result.get("reason", "")
                task = result.get("task", "")

                logger.info(f"判断结果: should_reply={should_reply}, reason={reason}")

                if should_reply and task:
                    return True, f"[任务规划] {task}"
                elif should_reply:
                    return True, f"[判断] {reason}"
                return False, ""

            # 简单文本判断
            if "should_reply: true" in response_text.lower() or '"should_reply": true' in response_text.lower():
                return True, response_text

            return False, ""

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")
            return False, ""

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_all_message(self, event: AstrMessageEvent):
        """监听所有消息，智能判断是否回复"""
        # 消息过滤
        if not self._should_process(event):
            logger.debug(f"消息被过滤，跳过")
            return

        current_msg = event.message_str
        sender = event.get_sender_name() or "未知用户"

        # 获取上下文
        history = await self._get_history(event)

        # 构建 prompt
        prompt = self._build_judge_prompt(history, current_msg, sender)

        # 获取当前聊天模型
        umo = event.unified_msg_origin
        provider_id = await self.context.get_current_chat_provider_id(umo=umo)

        if not provider_id:
            logger.warning("无法获取当前聊天模型 ID")
            return

        # 调用 LLM 判断
        logger.info(f"调用 LLM 判断: {current_msg[:50]}...")

        llm_resp = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
        )

        if not llm_resp or not llm_resp.completion_text:
            logger.warning("LLM 返回空响应")
            return

        # 解析结果
        should_reply, task_plan = self._parse_result(llm_resp.completion_text)

        if should_reply:
            logger.info(f"智能回复: {task_plan}")
            yield event.plain_result(task_plan)
        else:
            logger.info("智能回复判断: 不需要回复")

    async def terminate(self):
        """插件销毁方法"""
        logger.info("智能回复插件已卸载")