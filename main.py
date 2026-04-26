"""
AstrBot EchoSense 回响感知插件
像回响一样感知对话，智能判断是否应该回复消息

基于 AstrBot 官方 helloworld 模板开发
"""

import json
import re
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger


class EchoSensePlugin(Star):
    """EchoSense 回响感知插件 - 像回响一样感知对话，智能判断是否回复消息"""

    # 默认判断 Prompt 模板
    DEFAULT_JUDGE_PROMPT = """你是一个智能对话判断系统。请分析以下对话上下文，判断机器人是否应该回复当前消息。

## 机器人角色设定
{persona}

## 历史对话
{history}

## 当前消息
发送者: {sender}
内容: {current_msg}

## 判断标准
请从以下维度评估（0-10分）：
1. **内容相关度**: 消息是否有趣、有价值、适合机器人回复（结合角色设定）
2. **回复意愿**: 基于角色特点，机器人是否应该主动参与此话题
3. **社交适宜性**: 在当前氛围下回复是否合适
4. **时机恰当性**: 回复时机是否恰当

回复阈值: {threshold}（综合评分达到此分数才回复）

请严格按照以下JSON格式输出结果，不要输出其他内容：
{{"relevance": 分数, "willingness": 分数, "social": 分数, "timing": 分数, "should_reply": true或false, "reason": "判断理由"}}"""

    def __init__(self, context: Context):
        super().__init__(context)
        self.filter_mode = "none"
        self.filter_list = []
        self.history_count = 10
        self.reply_threshold = 0.6
        self.judge_provider_id = ""
        self.judge_prompt = self.DEFAULT_JUDGE_PROMPT

    async def initialize(self):
        """插件初始化方法，加载配置"""
        config = self.context.get_config() or {}

        # 基础设置
        basic = config.get("basic", {})
        self.filter_mode = basic.get("filter_mode", "none")
        self.filter_list = basic.get("filter_list", [])
        self.history_count = basic.get("history_count", 10)
        self.reply_threshold = basic.get("reply_threshold", 0.6)

        # 模型设置
        model_config = config.get("model", {})
        self.judge_provider_id = model_config.get("judge_provider_id", "")

        # 提示词设置
        prompt_config = config.get("prompt", {})
        self.judge_prompt = prompt_config.get("judge_prompt", self.DEFAULT_JUDGE_PROMPT)

        logger.info(f"智能回复插件初始化完成: 模式={self.filter_mode}, 过滤列表={len(self.filter_list)}条, 阈值={self.reply_threshold}, 判断模型={self.judge_provider_id or '默认'}")

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

    async def _get_persona_prompt(self, event: AstrMessageEvent) -> str:
        """获取当前对话的人格系统提示词"""
        try:
            persona_mgr = self.context.persona_manager
            if not persona_mgr:
                return "默认角色：智能助手"

            # 获取当前对话的人格ID
            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(event.unified_msg_origin)
            persona_id = None
            if curr_cid:
                conversation = await self.context.conversation_manager.get_conversation(event.unified_msg_origin, curr_cid)
                if conversation:
                    persona_id = conversation.persona_id

            # 用户显式取消人格
            if persona_id == "[%None]":
                return "默认角色：智能助手"

            if persona_id:
                try:
                    persona = await persona_mgr.get_persona(persona_id)
                    return persona.system_prompt or "默认角色：智能助手"
                except ValueError:
                    logger.debug(f"未找到人格 {persona_id}，回退到默认人格")

            # 使用默认人格
            default_persona = await persona_mgr.get_default_persona_v3(event.unified_msg_origin)
            return default_persona.get("prompt", "默认角色：智能助手")

        except Exception as e:
            logger.debug(f"获取人格系统提示词失败: {e}")
            return "默认角色：智能助手"

    def _build_judge_prompt(self, history: str, current_msg: str, sender: str, persona: str) -> str:
        """构建判断 Prompt"""
        return self.judge_prompt.format(
            history=history or "无历史对话",
            current_msg=current_msg,
            sender=sender or "未知用户",
            persona=persona,
            threshold=self.reply_threshold
        )

    def _parse_result(self, response_text: str) -> tuple:
        """解析 LLM 返回结果，返回 (should_reply, reason)"""
        try:
            # 去除 markdown 代码块
            cleaned = re.sub(r"^```(?:json)?\s*", "", response_text.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

            # 提取 JSON
            json_match = re.search(r'\{.*\}', cleaned, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group(0))

                should_reply = result.get("should_reply", False)
                reason = result.get("reason", "")

                # 计算综合评分（如果有分数维度）
                relevance = result.get("relevance", 0)
                willingness = result.get("willingness", 0)
                social = result.get("social", 0)
                timing = result.get("timing", 0)

                if relevance or willingness or social or timing:
                    overall_score = (relevance + willingness + social + timing) / 40.0  # 4个维度平均
                    logger.info(f"判断结果: 评分={overall_score:.2f}, should_reply={should_reply}, reason={reason}")

                    # 如果综合评分达到阈值，也认为应该回复
                    if overall_score >= self.reply_threshold:
                        should_reply = True
                else:
                    logger.info(f"判断结果: should_reply={should_reply}, reason={reason}")

                return should_reply, reason

            # 简单文本判断
            if "should_reply: true" in response_text.lower() or '"should_reply": true' in response_text.lower():
                return True, "文本判断通过"

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

        # 跳过已经被唤醒的消息（用户主动呼叫）
        if event.is_at_or_wake_command:
            logger.debug("消息已被唤醒，跳过判断")
            return

        # 跳过机器人自己的消息
        if event.get_sender_id() == event.get_self_id():
            return

        current_msg = event.message_str
        sender = event.get_sender_name() or "未知用户"

        # 获取上下文和人格
        history = await self._get_history(event)
        persona = await self._get_persona_prompt(event)

        # 构建 prompt
        prompt = self._build_judge_prompt(history, current_msg, sender, persona)

        # 获取判断模型提供商
        judge_provider = None
        default_provider_id = None

        if self.judge_provider_id:
            try:
                judge_provider = self.context.get_provider_by_id(self.judge_provider_id)
                if not judge_provider:
                    logger.warning(f"配置的判断模型提供商不存在: {self.judge_provider_id}")
            except Exception as e:
                logger.warning(f"获取判断模型提供商失败: {e}")

        # 如果没有配置判断模型，获取当前对话默认模型
        if not judge_provider:
            umo = event.unified_msg_origin
            default_provider_id = await self.context.get_current_chat_provider_id(umo=umo)
            if not default_provider_id:
                logger.warning("无法获取当前聊天模型 ID")
                return

        # 调用 LLM 判断
        logger.info(f"调用 LLM 判断: {current_msg[:50]}...")

        try:
            if judge_provider:
                # 使用配置的判断模型
                llm_resp = await judge_provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    image_urls=[]
                )
            else:
                # 使用当前对话默认模型
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=default_provider_id,
                    prompt=prompt,
                )

            if not llm_resp or not llm_resp.completion_text:
                logger.warning("LLM 返回空响应")
                return

        except Exception as e:
            logger.error(f"LLM 判断调用失败: {e}")
            return

        # 解析结果
        should_reply, reason = self._parse_result(llm_resp.completion_text)

        if should_reply:
            logger.info(f"智能回复判断通过: {reason}")
            # 设置唤醒标志，让 AstrBot 核心系统处理消息并生成人格回复
            event.is_at_or_wake_command = True
            event.set_extra("smart_reply_triggered", True)
            # 不返回任何内容，让核心系统继续处理
            return
        else:
            logger.debug(f"智能回复判断不通过: {reason}")

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """智能回复触发时，在 LLM 请求前注入提示"""
        if not event.get_extra("smart_reply_triggered"):
            return
        if not req or not hasattr(req, "system_prompt"):
            return
        note = "\n（注意：本次是你主动参与对话的，回复应自然随意。）"
        req.system_prompt = (req.system_prompt or "") + note

    async def terminate(self):
        """插件销毁方法"""
        logger.info("智能回复插件已卸载")