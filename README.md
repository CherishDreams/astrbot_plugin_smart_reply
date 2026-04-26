# 🌟 AstrBot 智能回复判断插件

<div align="center">

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-lightgrey)
[![PRs Welcome](https://img.shields.io/badge/PRs-Welcome-brightgreen)]()

</div>

## 📑 目录

- [🌟 AstrBot 智能回复判断插件](#-astrbot-智能回复判断插件)
  - [📑 目录](#-目录)
  - [🚀 功能特点](#-功能特点)
  - [📦 安装方法](#-安装方法)
  - [⚙️ 配置说明](#️-配置说明)
  - [📝 工作原理](#-工作原理)
  - [📜 更新日志](#-更新日志)
    - [v0.0.1](#v001)
  - [⚠️ 注意事项](#️-注意事项)
  - [🛠️ 问题反馈](#️-问题反馈)
  - [📄 许可证](#-许可证)

一个基于 LLM 的 AstrBot 智能回复判断插件，通过分析对话上下文智能判断是否应该回复消息，并进行基础任务规划。

## 🚀 功能特点

| 功能 | 描述 |
|------|------|
| 🔍 全消息监听 | 监听所有消息事件，支持白名单过滤 |
| 🤖 LLM 智能判断 | 基于对话上下文，调用 AstrBot 配置的 LLM 判断是否需要回复 |
| 📊 上下文分析 | 获取可配置数量的历史消息，进行上下文分析 |
| 📋 任务规划 | 在需要回复时输出基础任务规划 |
| 🔒 白名单机制 | 支持用户 ID 和群 ID 白名单，灵活控制触发范围 |
| ⚙️ 可配置 Prompt | 支持自定义判断提示词模板 |
| 🎯 优先级控制 | 可设置消息处理优先级 |

## 📦 安装方法

1. 确保已安装 AstrBot（版本 >= v4.5.7）
2. 将插件复制到 AstrBot 的插件目录（`addons/plugins/`）
   - 也可以使用 AstrBot 的插件管理器安装
   - 或下载本项目压缩包上传安装
3. 重启 AstrBot 或使用热加载命令

## ⚙️ 配置说明

插件配置项包括：

| 配置项 | 类型 | 默认值 | 描述 |
|--------|------|--------|------|
| `whitelist_users` | list | `[]` | 用户 ID 白名单列表，白名单用户的消息会触发智能回复判断 |
| `whitelist_groups` | list | `[]` | 群 ID 白名单列表，白名单群的消息会触发智能回复判断 |
| `history_count` | int | `10` | 获取的历史消息数量上限（用于判断上下文） |
| `judge_prompt` | str | 见下方 | LLM 判断提示词模板 |

**默认判断 Prompt 模板：**

```text
你是一个智能对话判断助手。请分析以下对话上下文，判断机器人是否应该回复当前消息。

判断标准：
1. 消息是否针对机器人或需要机器人参与
2. 是否有明确的回复意图或问题
3. 上下文是否需要延续对话

历史对话：
{history}

当前消息：{current_msg}
发送者：{sender}

请严格按照以下JSON格式输出结果，不要输出其他内容：
{"should_reply": true或false, "reason": "判断理由简述", "task": "任务规划描述（如需回复）"}
```

**支持的模板变量：**
- `{history}` - 对话历史
- `{current_msg}` - 当前消息内容
- `{sender}` - 发送者名称

### 白名单机制说明

- 当白名单为空时，默认允许所有消息触发判断
- 支持 `whitelist_users`（用户白名单）和 `whitelist_groups`（群白名单）
- 任一白名单匹配即触发判断

## 📝 工作原理

1. **消息监听**：通过 `@filter.event_message_type(filter.EventMessageType.ALL)` 监听所有消息
2. **白名单过滤**：检查消息来源是否在白名单中
3. **上下文获取**：通过 `ConversationManager` 获取对话历史
4. **LLM 判断**：构建 Prompt，调用 AstrBot 已配置的 LLM 模型
5. **结果解析**：解析 JSON 格式的判断结果
6. **回复输出**：如需回复，输出任务规划结果

## 📜 更新日志

### v0.0.1

- 🚀 初始版本发布
- 🔍 实现全消息监听功能
- 🤖 实现基于 LLM 的智能回复判断
- 📊 实现对话上下文获取与分析
- 📋 实现基础任务规划输出
- 🔒 实现白名单过滤机制
- ⚙️ 支持自定义配置项

## ⚠️ 注意事项

1. 需要在 AstrBot 中正确配置 LLM 模型才能正常使用
2. 白名单配置为空时，所有消息都会触发判断（可能导致频繁调用 LLM）
3. 建议根据实际需求调整 `history_count` 参数，避免上下文过长
4. 可通过调整优先级参数控制与其他插件的执行顺序

## 🛠️ 问题反馈

如果遇到问题或有功能建议，欢迎在 GitHub 提交 Issue。

## 📄 许可证

本项目基于 MIT 许可证开源。