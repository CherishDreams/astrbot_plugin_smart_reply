"""
AstrBot EchoSense 回响感知插件
像回响一样感知对话，智能判断是否应该回复消息

基于 AstrBot 官方 helloworld 模板开发
采用完整状态机制：消息累积 + 密集讨论检测 + 复读检测
"""

import json
import re
import time
import asyncio
from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, Face

from .core import ConversationLedger, StateManager, PluginState
from .core.detectors import should_trigger_active_state
from .models import AnalysisDecision


class EchoSensePlugin(Star):
    """EchoSense 回响感知插件 - 像回响一样感知对话，智能判断是否回复消息"""

    # 默认判断 Prompt 模板（用于批量分析）
    DEFAULT_ANALYSIS_PROMPT = """你是一个智能对话判断系统。请分析以下群聊对话，判断机器人是否应该参与当前话题。

## 机器人角色设定
{persona}

## 对话历史（背景）
{background}

## 近期对话（待分析）
{recent}

## 判断标准
请从以下维度评估（0-10分）：
1. **内容相关度**: 话题是否有趣、有价值、适合机器人参与
2. **回复意愿**: 基于角色特点，机器人是否应该主动参与
3. **社交适宜性**: 在当前氛围下参与是否合适
4. **时机恰当性**: 参与时机是否恰当

回复阈值: {threshold}

请严格按照以下JSON格式输出结果，不要输出其他内容：
{{"should_reply": true或false, "topic": "话题概要", "reply_strategy": "回复策略", "reply_target": "目标用户", "relevance": 分数, "willingness": 分数, "social": 分数, "timing": 分数, "reason": "判断理由"}}
"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = config or {}

        # 基础配置
        basic = self.config.get("basic", {})
        self.filter_mode = basic.get("filter_mode", "none")
        self.filter_list = basic.get("filter_list", [])
        self.history_count = basic.get("history_count", 10)
        self.reply_threshold = basic.get("reply_threshold", 0.6)

        # 模型配置
        model_config = self.config.get("model", {})
        self.judge_provider_id = model_config.get("judge_provider_id", "")

        # 提示词配置
        prompt_config = self.config.get("prompt", {})
        self.judge_prompt = prompt_config.get("judge_prompt", self.DEFAULT_ANALYSIS_PROMPT)

        # 防刷屏配置
        anti_spam = self.config.get("anti_spam", {})
        self.skip_image_message = anti_spam.get("skip_image_message", True)
        self.skip_face_message = anti_spam.get("skip_face_message", True)
        self.min_text_length = anti_spam.get("min_text_length", 2)

        # 状态机制配置
        state_machine = self.config.get("state_machine", {})
        self.observation_timeout = state_machine.get("observation_timeout", 600)
        self.dense_threshold = state_machine.get("dense_threshold", 10)
        self.dense_participants = state_machine.get("dense_participants", 3)
        self.dense_window = state_machine.get("dense_window", 60)
        self.echo_threshold = state_machine.get("echo_threshold", 3)
        self.echo_window = state_machine.get("echo_window", 30)

        # 核心组件
        self.ledger = ConversationLedger()
        self.state_manager = StateManager(self.observation_timeout)

        # 异步任务追踪
        self._pending_tasks: dict[str, asyncio.Task] = {}
        self._analysis_interval = 2.0  # 分析任务最小间隔（秒）
        self._last_analysis_time: dict[str, float] = {}

    async def initialize(self):
        """插件初始化方法"""
        logger.info(
            f"EchoSense 初始化完成: 模式={self.filter_mode}, "
            f"过滤列表={len(self.filter_list)}条, 阈值={self.reply_threshold}"
        )
        logger.info(
            f"状态机制: 观察超时={self.observation_timeout}s, "
            f"密集检测={self.dense_threshold}条/{self.dense_window}s/{self.dense_participants}人, "
            f"复读检测={self.echo_threshold}次/{self.echo_window}s"
        )

    def _should_process(self, event: AstrMessageEvent) -> bool:
        """检查消息是否应该被处理"""
        if self.filter_mode == "none":
            return True

        session_id = event.unified_msg_origin
        group_id = event.get_group_id()
        sender_id = event.message_obj.sender.user_id if event.message_obj.sender else None

        in_list = False
        for item in self.filter_list:
            if item == session_id:
                in_list = True
                break
            if group_id and str(group_id) == str(item):
                in_list = True
                break
            if sender_id and str(sender_id) == str(item):
                in_list = True
                break

        if self.filter_mode == "whitelist":
            return in_list
        elif self.filter_mode == "blacklist":
            return not in_list

        return True

    def _check_message_type(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """检查消息类型是否应该跳过"""
        messages = event.get_messages()

        if self.skip_image_message:
            for comp in messages:
                if isinstance(comp, Image):
                    return True, "包含图片消息"

        if self.skip_face_message:
            has_face = False
            has_text = False
            for comp in messages:
                if isinstance(comp, Face):
                    has_face = True
                elif isinstance(comp, Plain) and comp.text.strip():
                    has_text = True
            if has_face and not has_text:
                return True, "纯表情消息"

        return False, ""

    def _check_text_length(self, event: AstrMessageEvent) -> bool:
        """检查文本长度"""
        if self.min_text_length <= 0:
            return True

        messages = event.get_messages()
        text_content = ""
        for comp in messages:
            if isinstance(comp, Plain):
                text_content += comp.text

        if not text_content.strip():
            return True

        return len(text_content.strip()) >= self.min_text_length

    def _extract_message_info(self, event: AstrMessageEvent) -> dict:
        """提取消息信息用于缓存"""
        messages = event.get_messages()

        # 检查是否包含图片
        has_image = any(isinstance(comp, Image) for comp in messages)

        # 提取文本内容
        text_content = ""
        for comp in messages:
            if isinstance(comp, Plain):
                text_content += comp.text

        return {
            "timestamp": time.time(),
            "sender_id": event.get_sender_id(),
            "sender_name": event.get_sender_name() or "未知用户",
            "content": text_content.strip(),
            "has_image": has_image,
            "is_self": event.get_sender_id() == event.get_self_id(),
        }

    @filter.event_message_type(filter.EventMessageType.ALL, priority=5)
    async def on_all_message(self, event: AstrMessageEvent):
        """监听所有消息，状态机制处理"""
        session_id = event.unified_msg_origin

        # 1. 基础过滤
        if not self._should_process(event):
            return

        # 2. 跳过机器人自己的消息（但仍缓存用于上下文）
        is_self = event.get_sender_id() == event.get_self_id()
        if is_self:
            # 只缓存自己的消息，不处理
            self.ledger.add_message(session_id, self._extract_message_info(event))
            return

        # 3. 提取并缓存消息（立即完成，不阻塞）
        message_info = self._extract_message_info(event)
        self.ledger.add_message(session_id, message_info)

        # 4. 如果是 @/唤醒消息，立即标记需要回复
        if event.is_at_or_wake_command:
            self.state_manager.transition(session_id, PluginState.SUMMONED)
            self._schedule_analysis(session_id, event)
            return

        # 5. 根据状态路由处理
        state = self.state_manager.get_state(session_id)

        if state == PluginState.NOT_PRESENT:
            # 快速检测触发条件（密集讨论/复读）
            should_trigger, reason = should_trigger_active_state(
                self.ledger, session_id,
                dense_threshold=self.dense_threshold,
                dense_participants=self.dense_participants,
                dense_window=self.dense_window,
                echo_threshold=self.echo_threshold,
                echo_window=self.echo_window,
            )
            if should_trigger:
                logger.info(f"状态触发: {reason}")
                self.state_manager.transition(session_id, PluginState.GETTING_FAMILIAR)
                self._schedule_analysis(session_id, event)

        elif state == PluginState.SUMMONED:
            # 已标记需要回复，如果还没有任务则创建
            self._schedule_analysis(session_id, event)

        elif state == PluginState.GETTING_FAMILIAR:
            # 尝试融入，触发分析
            self._schedule_analysis(session_id, event)

        elif state == PluginState.OBSERVATION:
            # 观察期，检查是否应该继续参与
            # 简单策略：观察期内每条消息都触发分析（由间隔控制频率）
            self._schedule_analysis(session_id, event)

    def _schedule_analysis(self, session_id: str, event: AstrMessageEvent) -> None:
        """
        创建后台任务执行 LLM 分析。
        使用 asyncio.create_task() 不阻塞当前事件处理。
        """
        # 检查最小间隔，避免频繁触发
        last_time = self._last_analysis_time.get(session_id, 0)
        if time.time() - last_time < self._analysis_interval:
            return

        # 防止重复创建任务
        if session_id in self._pending_tasks:
            existing_task = self._pending_tasks[session_id]
            if not existing_task.done():
                return

        # 创建后台任务
        self._last_analysis_time[session_id] = time.time()
        task = asyncio.create_task(self._async_analyze(session_id, event))
        self._pending_tasks[session_id] = task

        task.add_done_callback(
            lambda t: self._pending_tasks.pop(session_id, None)
        )

    async def _async_analyze(self, session_id: str, event: AstrMessageEvent) -> None:
        """
        后台异步执行的分析任务。
        """
        try:
            # 获取累积的未处理消息
            unprocessed = self.ledger.get_unprocessed_messages(session_id)

            if not unprocessed:
                return

            # 获取背景消息（已处理的）
            all_messages = self.ledger.get_messages(session_id)
            last_processed = self.ledger._processed_marks.get(session_id, 0)
            background = [
                msg for msg in all_messages
                if msg.get("timestamp", 0) <= last_processed
            ]

            # 构建分析 prompt
            background_text = self._format_messages(background[-5:])  # 最近5条背景
            recent_text = self._format_messages(unprocessed)

            # 获取人格
            persona = await self._get_persona_prompt(event)

            prompt = self.judge_prompt.format(
                persona=persona,
                background=background_text or "无背景对话",
                recent=recent_text,
                threshold=self.reply_threshold,
            )

            # 调用 LLM
            result = await self._call_llm(session_id, event, prompt)

            if result and result.should_reply:
                logger.info(f"分析结果: should_reply=True, topic={result.topic}, reason={result.reason}")

                # 标记消息为已处理
                self.ledger.mark_processed(session_id)

                # 转换状态到观察期
                self.state_manager.transition(session_id, PluginState.OBSERVATION)

                # 触发回复
                event.is_at_or_wake_command = True
                event.set_extra("smart_reply_triggered", True)
                event.set_extra("analysis_topic", result.topic)
                event.set_extra("analysis_strategy", result.reply_strategy)
            else:
                logger.debug(f"分析结果: should_reply=False")
                # 不回复，继续观察

        except Exception as e:
            logger.error(f"异步分析失败: {e}")

    def _format_messages(self, messages: list[dict]) -> str:
        """格式化消息列表为文本"""
        if not messages:
            return ""

        lines = []
        for msg in messages:
            sender = msg.get("sender_name", "未知")
            content = msg.get("content", "")
            if content:
                lines.append(f"{sender}: {content}")
            elif msg.get("has_image"):
                lines.append(f"{sender}: [图片]")

        return "\n".join(lines)

    async def _call_llm(
        self,
        session_id: str,
        event: AstrMessageEvent,
        prompt: str,
    ) -> AnalysisDecision | None:
        """调用 LLM 进行分析"""
        judge_provider = None
        default_provider_id = None

        if self.judge_provider_id:
            try:
                judge_provider = self.context.get_provider_by_id(self.judge_provider_id)
                if not judge_provider:
                    logger.warning(f"配置的判断模型提供商不存在: {self.judge_provider_id}")
            except Exception as e:
                logger.warning(f"获取判断模型提供商失败: {e}")

        if not judge_provider:
            default_provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
            if not default_provider_id:
                logger.warning("无法获取当前聊天模型 ID")
                return None

        try:
            if judge_provider:
                llm_resp = await judge_provider.text_chat(
                    prompt=prompt,
                    contexts=[],
                    image_urls=[],
                )
            else:
                llm_resp = await self.context.llm_generate(
                    chat_provider_id=default_provider_id,
                    prompt=prompt,
                )

            if not llm_resp or not llm_resp.completion_text:
                logger.warning("LLM 返回空响应")
                return None

            return self._parse_decision(llm_resp.completion_text)

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return None

    def _parse_decision(self, response_text: str) -> AnalysisDecision | None:
        """解析 LLM 返回结果"""
        try:
            cleaned = re.sub(
                r"^```(?:json)?\s*", "", response_text.strip(),
                flags=re.IGNORECASE
            )
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group(0))

                return AnalysisDecision(
                    should_reply=data.get("should_reply", False),
                    topic=data.get("topic", ""),
                    reply_strategy=data.get("reply_strategy", ""),
                    reply_target=data.get("reply_target", ""),
                    reason=data.get("reason", ""),
                    relevance=float(data.get("relevance", 0)),
                    willingness=float(data.get("willingness", 0)),
                    social=float(data.get("social", 0)),
                    timing=float(data.get("timing", 0)),
                )

            # 简单文本判断
            if "should_reply: true" in response_text.lower():
                return AnalysisDecision(should_reply=True, reason="文本判断通过")

            return AnalysisDecision(should_reply=False)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")
            return None

    async def _get_persona_prompt(self, event: AstrMessageEvent) -> str:
        """获取当前对话的人格系统提示词"""
        try:
            persona_mgr = self.context.persona_manager
            if not persona_mgr:
                return "默认角色：智能助手"

            curr_cid = await self.context.conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            persona_id = None
            if curr_cid:
                conversation = await self.context.conversation_manager.get_conversation(
                    event.unified_msg_origin, curr_cid
                )
                if conversation:
                    persona_id = conversation.persona_id

            if persona_id == "[%None]":
                return "默认角色：智能助手"

            if persona_id:
                try:
                    persona = await persona_mgr.get_persona(persona_id)
                    return persona.system_prompt or "默认角色：智能助手"
                except ValueError:
                    logger.debug(f"未找到人格 {persona_id}")

            default_persona = await persona_mgr.get_default_persona_v3(
                event.unified_msg_origin
            )
            return default_persona.get("prompt", "默认角色：智能助手")

        except Exception as e:
            logger.debug(f"获取人格失败: {e}")
            return "默认角色：智能助手"

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """智能回复触发时，注入提示"""
        if not event.get_extra("smart_reply_triggered"):
            return

        if not req or not hasattr(req, "system_prompt"):
            return

        topic = event.get_extra("analysis_topic", "")
        strategy = event.get_extra("analysis_strategy", "")

        note = "\n（本次是你主动参与对话的，回复应自然随意。"
        if topic:
            note += f" 当前话题: {topic}。"
        if strategy:
            note += f" 建议: {strategy}"
        note += "）"

        req.system_prompt = (req.system_prompt or "") + note

    async def terminate(self):
        """插件销毁方法"""
        # 取消所有待处理任务
        for task in self._pending_tasks.values():
            if not task.done():
                task.cancel()

        self._pending_tasks.clear()
        self.ledger.clear_all()
        self.state_manager.clear_all()
        logger.info("EchoSense 插件已卸载")