# PASM Companion · 会聊天、会成长、会记住你的 AI 伙伴（Windows 桌面版）

> **打开就能和它聊**：它会思考、会回答、会主动反问你；每一次相处都会沉淀成它的记忆与性格——
> 你聊得越多，它越"懂你"，越像"只属于你的那个 AI"。

## 无需任何 API Key：自动接入你本机已装的 Ollama（qwen/llama 模型），
本机就是你的服务器。它和普通聊天 AI 有什么不同

普通 AI 每轮对话都是"重新开始"，它不记得你、也没有"自己"。
PASM Companion 是**双脑结构**：

| 大脑 | 负责 | 结果 |
|---|---|---|
| **语言脑**（DeepSeek / 任意 OpenAI 兼容 / 本地 Ollama） | 听懂你、组织回答、反问 | 像真人一样聊得来 |
| **PASM 认知脑**（本地，离线运行） | 每次对话 → 情绪波动、性格漂移、"关于你"的记忆 | **真的会长大、真的记得你** |

- 🧠 **记得你**：你开心/失落时它都记下（"你上次聊到项目上线时特别开心"）
- 🌱 **性格成长**：同样的经历，温和型/活泼型/机灵型会长成不同的"它"
- 💬 **主动**：聊开之后它会反问你的近况、关心你上次提的事
- 🔒 **隐私**：对话记忆只在本机 `%APPDATA%\PASMStudio`，可随时清空
- 🛠 后续路线：自主编程技能、语音对话（见路线图）

## 下载与安装

最新版见仓库 **Releases**（v0.3.1 对话版）：
1. 下载 `PASMStudio-Setup-0.3.1.exe.part_aa` 与 `…part_ab`（同一目录）
2. Windows CMD 合并：`copy /b PASMStudio-Setup-0.3.1.exe.part_aa + PASMStudio-Setup-0.3.1.exe.part_ab PASMStudio-Setup-0.3.1.exe`
3. 双击安装 → 打开 PASM Studio → 点右上「设置」填入你的 LLM Key 开始聊天

> 系统要求：Windows 10/11 x64 · CPU 即可 · 首次安装解压约 1 分钟
> 未填 Key 也能用（本地演示模式：同样会记忆与成长，只是话术朴素）

## 设置语言脑（一句话）

- **DeepSeek**：填官网 API Key，Base URL 保持 `https://api.deepseek.com/v1`，Model `deepseek-chat`
- **本地免费**：装 [Ollama](https://ollama.com) 后 `ollama run qwen2.5:7b`，
  设置里 Base URL 填 `http://127.0.0.1:11434/v1`、Model 填 `qwen2.5:7b`——完全离线

## 数据与更新

- 记忆/配置：`%APPDATA%\PASMStudio`（卸载不删除）
- 启动自动检查本仓库 `latest.json`，发现新版在聊天区提示

## 路线图

- [ ] **自主编程技能**（让它调用 Python 沙箱帮你写/跑脚本）
- [ ] **语音对话**（说给它听 + 它开口回应）
- [ ] 成长可视化主页 · 性格报告 · 云端同步

## 说明

PASM Companion 为闭源桌面产品（本地推理引擎 + 语言脑 API）；研究理念与
教学代码见 PASM-Lite（开源）。问题/反馈/合作请提 issue。

## 概念与生态文档
- `docs/pasm_ecosystem.md` —— PASM 能做什么 / 效果 / 三仓库生态 / 文档地图
- 其余：双脑总线 B3、知识库 v2、路线图等见 PASM-Lite 仓库 docs/（开源）
