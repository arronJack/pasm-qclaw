---
name: connector-remote
description: 跨端远程——把结果推到手机，也能接收手机发来的指令让桌面端干活。当用户说"推到我手机""发到我微信""手机提醒我""跨端推送怎么配置"时使用。
keywords: 跨端,远程,手机,推送,微信,钉钉,飞书,Telegram,Bark,Server酱
category: 连接器
type: connector
version: 1.0.0
author: PASM
---

# 跨端远程桥（remote_bridge）

## 这个技能给你什么
让 PASM 不困在桌面里 —— **在手机上发一句话，桌上的 PASM 干活，结果再推回手机**。

## 它跑在什么之上
基座 `pasm-skills`；实现是 `desktop/remote_bridge.py`（标准库 `urllib`/`http.server`，零新依赖）。

## 三种通道（URL 自动识别，不用自己选）
| 通道 | 要填什么 |
|---|---|
| Telegram Bot | `bridge_tg_token` + `bridge_tg_chat`（最适合双向收发指令） |
| 企业微信 / 钉钉 / 飞书 群机器人 | 只填 `bridge_webhook_url`，我按域名自动认平台 |
| Server酱 / Bark | 把推送 URL 填进 `bridge_webhook_url` |

## 执行步骤
1. **推出去**：「把刚才的结果推到我手机」→ 推送**上一条回复**（或你指定的内容）。
2. **收回来**：配了 Telegram token 后，后台每 6 秒拉一次新消息，投进正常聊天管线处理，
   回复自动推回手机（手机端和桌面端行为完全一致）。
3. **本机接收端**（可选）：`LocalInbox` 提供 `POST /msg`，给局域网/内网穿透用。

## 输出结构
· 推送成功：明确列出「已推送到 Telegram / 企业微信…（多少字）」
· 未配置：给出中文配置指引，挑一种最省事的即可

## 质量红线
- 未配置时**绝不假装已发送**。
- 本机接收端**只绑 127.0.0.1 且必须带 token**，没有 token 时直接拒绝启动 ——
  绝不把用户电脑暴露成公网开放中继。
- token / 授权码不回显、不写日志（状态里做掩码）。
- 所有出网调用带超时，失败给中文原因。
