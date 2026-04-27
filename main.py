"""
AstrBot EchoSense 回响感知插件
像回响一样感知对话，智能判断是否应该回复消息

基于 AstrBot 官方 helloworld 模板开发
采用完整状态机制：消息累积 + 密集讨论检测 + 复读检测
使用事件扣押机制确保回复正确触发
"""

import json
import re
import time
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image, Face

from .core import ConversationLedger, StateManager, PluginState, DetentionManager
from .core.detectors import should_trigger_active_state
from .models import AnalysisDecision


class EchoSensePlugin(Star):
    """EchoSense 回响感知插件"""

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

        # 调试模式
        self.debug_mode = self.config.get("debug_mode", False)

        # 核心组件
        self.ledger = ConversationLedger()
        self.state_manager = StateManager(self.observation_timeout)
        self.detention_manager = DetentionManager(self.debug_mode)

    def _debug_log(self, message: str) -> None:
        """输出调试日志"""
        if self.debug_mode:
            logger.info(f"[EchoSense] {message}")
        else:
            logger.debug(f"[EchoSense] {message}")

    async def initialize(self):
        """插件初始化"""
        log_level = "DEBUG" if self.debug_mode else "INFO"
        logger.info(
            f"EchoSense 初始化完成 | 调试模式={self.debug_mode} | "
            f"过滤={self.filter_mode} | 阈值={self.reply_threshold}"
        )
        logger.info(
            f"状态机制: 观察超时={self.observation_timeout}s | "
            f"密集检测={self.dense_threshold}条/{self.dense_window}s/{self.dense_participants}人 | "
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
        return not in_list if self.filter_mode == "blacklist" else True

    def _check_message_type(self, event: AstrMessageEvent) -> tuple[bool, str]:
        """检查消息类型"""
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

    def _extract_message_info(self, event: AstrMessageEvent) -> dict:
        """提取消息信息"""
        messages = event.get_messages()
        has_image = any(isinstance(comp, Image) for comp in messages)
        text_content = "".join(
            comp.text for comp in messages if isinstance(comp, Plain)
        )

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
        """监听所有消息 - 使用事件扣押机制"""
        session_id = event.unified_msg_origin
        event_id = f"{event.get_sender_id()}:{time.time():.0f}"

        # 1. 基础过滤
        if not self._should_process(event):
            self._debug_log(f"消息被过滤: session={session_id}")
            return

        # 2. 跳过机器人自己的消息（但缓存用于上下文）
        is_self = event.get_sender_id() == event.get_self_id()
        if is_self:
            self.ledger.add_message(session_id, self._extract_message_info(event))
            return

        # 3. 消息类型检查
        should_skip, skip_reason = self._check_message_type(event)
        if should_skip:
            self._debug_log(f"消息跳过: {skip_reason}")
            return

        # 4. 缓存消息
        message_info = self._extract_message_info(event)
        self.ledger.add_message(session_id, message_info)
        self._debug_log(
            f"消息缓存: sender={message_info['sender_name']}, "
            f"content={message_info['content'][:20] if message_info['content'] else '[图片]'}"
        )

        # 5. 被@唤醒 → SUMMONED 状态
        if event.is_at_or_wake_command:
            self._debug_log(f"被@唤醒: session={session_id}")
            self.state_manager.transition(session_id, PluginState.SUMMONED)
            await self._process_with_lock(session_id, event)
            return

        # 6. 状态判断
        state = self.state_manager.get_state(session_id)
        self._debug_log(f"当前状态: {state.value}")

        should_trigger = False
        trigger_reason = ""

        if state == PluginState.NOT_PRESENT:
            # 快速检测触发条件
            should_trigger, trigger_reason = should_trigger_active_state(
                self.ledger, session_id,
                dense_threshold=self.dense_threshold,
                dense_participants=self.dense_participants,
                dense_window=self.dense_window,
                echo_threshold=self.echo_threshold,
                echo_window=self.echo_window,
            )
            if should_trigger:
                self._debug_log(f"状态触发: {trigger_reason}")
                self.state_manager.transition(session_id, PluginState.GETTING_FAMILIAR)

        elif state in (PluginState.SUMMONED, PluginState.GETTING_FAMILIAR, PluginState.OBSERVATION):
            should_trigger = True

        # 7. 如果触发分析，使用事件扣押机制
        if should_trigger:
            await self._process_with_lock(session_id, event)

    async def _process_with_lock(self, session_id: str, event: AstrMessageEvent) -> None:
        """
        使用门牌锁机制处理消息。
        - 成功获取锁：直接处理
        - 锁被占用：取票等待
        """
        event_id = f"{event.get_sender_id()}:{time.time():.0f}"

        # 尝试获取门牌锁
        acquired, ticket = await self.detention_manager.acquire_or_wait(session_id, event_id)

        if not acquired and ticket:
            # 需要等待（事件扣押）
            self._debug_log(f"进入扣押队列: session={session_id}")
            result = await ticket  # Future 阻塞

            if result == "KILL":
                # 被新消息取代，终止处理
                self._debug_log(f"扣押被终止: session={session_id}")
                event.stop_event()
                return

            self._debug_log(f"扣押解除，继续处理: session={session_id}")

        # 执行分析（同步，事件仍在 Pipeline 中）
        try:
            analysis_result = await self._analyze_messages(session_id, event)

            if analysis_result and analysis_result.should_reply:
                self._debug_log(
                    f"分析完成: should_reply=True, topic={analysis_result.topic}"
                )

                # 标记消息已处理
                self.ledger.mark_processed(session_id)

                # 转换到观察期
                self.state_manager.transition(session_id, PluginState.OBSERVATION)

                # 设置唤醒标志（此时 Pipeline 还在运行）
                event.is_at_or_wake_command = True
                event.set_extra("smart_reply_triggered", True)
                event.set_extra("analysis_topic", analysis_result.topic)
                event.set_extra("analysis_strategy", analysis_result.reply_strategy)

                self._debug_log(f"已设置唤醒标志: session={session_id}")
            else:
                self._debug_log(f"分析完成: should_reply=False")

        except Exception as e:
            logger.error(f"分析失败: {e}")

        finally:
            # 释放门牌锁，唤醒等待者
            self.detention_manager.release_and_resolve(session_id)

    async def _analyze_messages(self, session_id: str, event: AstrMessageEvent) -> AnalysisDecision | None:
        """分析累积的消息"""
        # 获取未处理消息
        unprocessed = self.ledger.get_unprocessed_messages(session_id)
        if not unprocessed:
            return None

        # 获取背景消息
        all_messages = self.ledger.get_messages(session_id)
        last_processed = self.ledger._processed_marks.get(session_id, 0)
        background = [
            msg for msg in all_messages
            if msg.get("timestamp", 0) <= last_processed
        ]

        background_text = self._format_messages(background[-5:])
        recent_text = self._format_messages(unprocessed)

        self._debug_log(
            f"分析消息: background={len(background[-5:])}条, "
            f"recent={len(unprocessed)}条"
        )

        # 获取人格
        persona = await self._get_persona_prompt(event)

        prompt = self.judge_prompt.format(
            persona=persona,
            background=background_text or "无背景对话",
            recent=recent_text,
            threshold=self.reply_threshold,
        )

        # 调用 LLM
        start_time = time.time()
        result = await self._call_llm(prompt, event)
        elapsed = time.time() - start_time

        self._debug_log(f"LLM耗时: {elapsed:.1f}s")

        return result

    def _format_messages(self, messages: list[dict]) -> str:
        """格式化消息"""
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

    async def _call_llm(self, prompt: str, event: AstrMessageEvent) -> AnalysisDecision | None:
        """调用 LLM"""
        judge_provider = None
        provider_id = None

        if self.judge_provider_id:
            try:
                judge_provider = self.context.get_provider_by_id(self.judge_provider_id)
            except Exception as e:
                self._debug_log(f"获取判断模型失败: {e}")

        if not judge_provider:
            provider_id = await self.context.get_current_chat_provider_id(
                umo=event.unified_msg_origin
            )
            if not provider_id:
                logger.warning("无法获取聊天模型 ID")
                return None

        try:
            if judge_provider:
                resp = await judge_provider.text_chat(prompt=prompt, contexts=[], image_urls=[])
            else:
                resp = await self.context.llm_generate(chat_provider_id=provider_id, prompt=prompt)

            if not resp or not resp.completion_text:
                return None

            return self._parse_decision(resp.completion_text)

        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return None

    def _parse_decision(self, text: str) -> AnalysisDecision | None:
        """解析 LLM 结果"""
        try:
            cleaned = re.sub(r"^```(?:json)?\s*", "", text.strip(), flags=re.IGNORECASE)
            cleaned = re.sub(r"\s*```$", "", cleaned).strip()

            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if match:
                data = json.loads(match.group(0))
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

            if "should_reply: true" in text.lower():
                return AnalysisDecision(should_reply=True, reason="文本判断通过")

            return AnalysisDecision(should_reply=False)

        except json.JSONDecodeError as e:
            logger.warning(f"JSON 解析失败: {e}")
            return None

    async def _get_persona_prompt(self, event: AstrMessageEvent) -> str:
        """获取人格"""
        try:
            mgr = self.context.persona_manager
            if not mgr:
                return "默认角色：智能助手"

            cid = await self.context.conversation_manager.get_curr_conversation_id(
                event.unified_msg_origin
            )
            persona_id = None
            if cid:
                conv = await self.context.conversation_manager.get_conversation(
                    event.unified_msg_origin, cid
                )
                if conv:
                    persona_id = conv.persona_id

            if persona_id == "[%None]":
                return "默认角色：智能助手"

            if persona_id:
                try:
                    persona = await mgr.get_persona(persona_id)
                    return persona.system_prompt or "默认角色：智能助手"
                except ValueError:
                    pass

            default = await mgr.get_default_persona_v3(event.unified_msg_origin)
            return default.get("prompt", "默认角色：智能助手")

        except Exception:
            return "默认角色：智能助手"

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req):
        """注入提示"""
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
        """销毁插件"""
        self.detention_manager.clear_all()
        self.ledger.clear_all()
        self.state_manager.clear_all()
        logger.info("EchoSense 插件已卸载")