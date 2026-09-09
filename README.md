# PASM Studio · 会思考、会干活、会记住你的 AI 伙伴（Windows 桌面版）

**简体中文**（正文） · [🇬🇧 English（完整英文版见下方）](#english--英文完整版)

> **v0.27.0 三方向全落地：数学脑 + 多模态感知（看图/听写）+ 量子策略 + 四大趋势** —— 打开就能聊：它能思考、
> 学过的本事（笑话/知识/技能）被点名时能真用出来；回复首字即出、边想边打。
> 每一次相处都会沉淀成它的记忆与性格——你聊得越多，它越"懂你"，越像"只属于你的那个 AI"。

**无需任何 API Key 也能用**：自动接入你本机已装的 Ollama（qwen/llama 等模型），
本机就是你的服务器；填一个 DeepSeek Key 则更强（见下文「设置语言脑」）。

## 最新：v0.27.0（2026-09-09）· 数学脑 + 多模态 + 量子策略 + 四大趋势

- **🧮 数学脑 mathlab**："帮我分析这个表格"→ 真算：回归(R²)/描述统计+离群/相关性/方程组/拓扑排序/最短路/连通分量/马尔可夫稳态，结论一句话中文给出
- **👁 多模态感知（Phase B）**：图片拖进聊天框/直贴截图 → 本机视觉模型看图（不上云零成本），下条消息带着图理解；语音转写 faster-whisper→Vosk 降级链
- **⚛ 量子策略（Phase C）**：候选策略=叠加态，退火测量选路——早期多探索、随经验收敛最优；同族干涉让经验泛化；情绪作外场偏置（急躁→直给）；成败回灌形成决策闭环
- **🔀 多模型路由**：闲聊走本地 Ollama（快）、干活走 DeepSeek（强）、评审走本地交叉校验，本地不可用自动回落
- **🔁 团队自检重试 + 🕸 拓扑校验**：单步异常/空壳/过短 → 自动重试 + 质量自检；流程支持 deps 依赖，环依赖/越界/自环校验不过不准跑
- **🏗 经验沉淀 + 🧠 项目级跨会话记忆**：项目跑完沉淀流程模板一键复用；团队执行自动注入项目档案
- 冒烟：mathlab 29 · phaseA 22 · trends 29 · bc+拓扑 29 · UI 回归 105 = **全绿**

- **📎 @引用真正可视化**：输入框打 `@` 弹出可引用文件快选（最近产物/资料库/本地文件，↑↓+Enter 选择、继续打字过滤），选中后消息里落成 `@路径`、**输入框上方出现 📎引用条**（点 ✕ 移除，输入框里的引用写法同步清掉）；支持 `@“含空格路径”` 引号写法；引用正文只进模型上下文，"你"气泡保持干净
- **🐛 修复 "@小U 被瞎聊"**：找不到的引用不再原样丢给模型（旧版模型见到 `@小U` 就当打招呼），纯引用消息找不到会明确提示并剥除标记；长文本/代码里的 `@` 不受影响
- **📎 @引用补齐**：@完整路径 / @文件名（按名在常用目录搜索兜底）/ @资料词条 → 读进上下文；找不到会提示怎么用
- **🏢 团队=真公司项目组**：新项目=空白（成员与流程亲手组建）；每项目独立编制、可编入/移出成员（人才库保留）；切换项目呈现各自 成员/流程/任务状态✅⚠️○/历次效果档案；跑完状态自动回写；聊天"让团队做X"仍一键开工
- 冒烟：全系回归绿（v0261 26 · v0262 22 · v0260 39 · v0250 33+33 · v0240 57+15 · v0231 26 · v022x/v0190）

## v0.26.2（2026-09-08）· 干活报错修复 / PASM 记忆情感真正调制 / 表格同文件精准续改

- **🐛 修复干活报错**：能力问答 8 个快捷锚点漏包列表导致的 `str.join()` 崩溃（一句话修复）
- **❤️ PASM 记忆情感真正可感知**：情绪词表扩到 40+（口语/抱怨/报错都会拉动心情）；情绪反馈不再被随机步稀释、撞顶；心情翻译成「此刻口吻」注入每条回复（开心→轻快、低落→放轻、急→直给重点）；做表格/方案/PPT/开发时自动带上与你相关的记忆与偏好
- **📄 表格/文档/PPT 同一文件续改**：同会话反复改改的是同一个文件——把上一版完整内容喂回模型（不再整篇重写成不相干内容）→ 覆盖写回；"另存/新做一份"才另开；产物记忆随会话持久
- 冒烟：v0262 新增 18 项全过；v0261/v0260/v0250/v0240/v0231/v0230 全回归绿

## v0.26.1（2026-09-08）· 五连真机修复（体验/打开软件/任务并发）

- **🩺 健康状态不再占屏**：自检自愈收成底部一条小卡，自动动态区回归主位
- **👥 团队页卡片化**：项目条 → 成员/流程两卡 → 开工区；成员行带 ★评分，单击员工卡打分、右键辞退
- **🎓 技能三层落地**：新增 🎯专家2（内容导演/数据分析师）+ 🔌连接器3（联网/本地文件/办公文档）；添加向导可选类型
- **📂 打开软件真启动**：修复"说打开了其实没有"——称谓前缀剥离+品牌安装路径真实探测+启动后进程真验证，绝不口嗨（"小U，帮我打开网易云音乐"现在真能开）
- **⏳ 任务不丢回复**：真机"只有提问没回复"根因修复——重活开工有提示、切走再回来结果当场显示、收尾全程兜底写回原会话
- 冒烟：v0261 新增 25 项全过；v0260/v0250/v0240/v0231/v0230 全回归绿

## v0.26.0（2026-09-08）· 项目台账精准重调 / 真实文件开发 / @引用与/指令

- **📒 项目级台账**：文案/表格/PPT/开发每个项目都有记录（需求史+每轮改了什么+冒烟+你的规则）；
  续写时自动把台账带进上下文精准修改——不再"牛头不对马嘴"
- **🛠 真实文件开发**：`/开发 需求` 或"用php/java/vue/go/c#/c++ 开发…"→ 选目标目录真实落盘
  （8 种语言脚手架 + 逐文件生成 + 按语言静态冒烟），产出自动记台账，"改这个项目"精准续改
- **📎 @引用**：消息里 `@文件路径` / `@资料词条`，自动把内容带进上下文
- **/️⃣ 指令**：`/开发 需求` · `/技能 技能名 需求` · `/项目`（看台账）
- （v0.25.0：公司式项目组·聊天提速·自愈纯后台 · 见 CHANGELOG）

- **👥 公司式项目组**：项目空间保存目标+流程+历次产物；成员=点开看职责与评分的智能体员工卡；
  流程拖拽排序、双击改任务；🌐网上招聘（抓岗位资料自动提炼员工卡）、🎓技能库选才；
  每步表现自动评分——分低就换更好的成员
- **⚡ 聊天提速**：双脑协同只在写代码/写作等重活时启用，闲聊问答单次直出，首字延迟大幅回落
- **🩺 自检自愈纯后台**：静默自动运行不打扰，自动化页只留「查看健康状态」按钮
- **🎓 技能三层化**：🎯专家 / 🎓技能 / 🔌连接器 类型标注与筛选
- **🔧 安装器根除 MoveFile 错误 5**：升级先清空程序目录（保留 data）再全新复制，不再替换旧文件
- **🧬 PASM 核心引擎同频（0.5.0）**：多智能体团队与自检自愈真身移入引擎执行皮层 `pasm.cognitive`，桌面端为兼容门面，二者共用同一实现
- **👥 多智能体技能团队**：内置 PM/策划/调度/开发/审查/调试 六角色卡，可自添加"员工卡"组成自定义流程；
  聊天说「让我的团队做 X」自动开工，审查不过自动返工、失败自动重试，过程全程可视
- **🩺 自检·自定位·自修复**：启动静默巡检日志崩溃栈记入故障案例库；可出文件+行号级诊断报告；
  建议/自动修两档——自动修=补丁→冒烟回归→不过自动回滚（安全阀把守，安装版强制建议档）
- （v0.24.0：方舟404修复·主题加固 / v0.23.0：DeepSeek式闲聊门·排队并发·PASM×LLM真协同——详见 CHANGELOG）

## 它和普通聊天 AI 有什么不同

普通 AI 每轮对话都是"重新开始"，它不记得你、也没有"自己"。
PASM Studio 是 **双脑结构 + 认知执行皮层**：

| 部分 | 负责 | 结果 |
|---|---|---|
| **语言脑**（DeepSeek / 任意 OpenAI 兼容 / 本地 Ollama） | 听懂你、组织回答、反问 | 像真人一样聊得来 |
| **PASM 认知脑**（本地，离线运行） | 每次对话 → 情绪波动、性格漂移、分层记忆 | 真的会长大、真的记得你 |
| **认知执行皮层**（v0.15 新增） | 开口前判断意图/心情 → 决定姿态与语气；按话题自动想起相关经历 | 不再"千人一面"，记得上次怎么干成的事 |

## 从 v0.16 起积累的能力（0.16.0 起步，以上 0.16.1–0.16.7 持续更新）

**聊得更顺手（界面大改）**
- 输入框上方 **[💬 聊天] / [🔧 干活]** 分栏：聊天模式绝不会因为提到
  "做个应用/写个脚本"就擅自动手，想让它干活时一键切到干活模式——动不动手由你说了算
- 多行大输入框（Enter 发送 / Shift+Enter 换行）；顶部可随时**切换大模型**
  （云端 DeepSeek / 本地 Ollama 各模型任选，选择会记住）
- 左侧**会话列表**：每条消息实时保存，聊天内容永不丢；新对话/切换/重启后
  都能点回旧会话继续聊

**学得更真（知识库 v3 + 自主进化）**
- 每条知识 = 全文 + 要点 + 来源；**学到 = 原文入库**——自学会多源搜索并抓全文存库，
  提炼要点只是学完后的速记索引，绝不会因"要点提炼不出"把内容丢掉（v0.16.5 起）
- **资料库可直接读原文**：双击条目/书架打开全文阅读器，翻看它真正学到的整篇内容，
  支持关键词过滤（v0.16.5 起）
- 自动上网自学默认开启，主题由"好奇心选题器"自己挑（避开已学、延伸进阶）；
  学后自测 3 问，答不出如实记入"待弄懂"清单，不假装学会

**本事可扩展（技能系统）**
- 随时给它在数据目录里添加新技能（名称+说明+规程），干活时自动检索调用；
  内置视频脚本创作 / 营销文案 / 漫剧一条龙示例
- "帮我写个短视频脚本"→ 走创作技能产出分镜表，**不再误入写代码**

**会记得（分层记忆）**
- 工作记忆：正在聊的话题 + 正在推进的目标（退出时落盘，重启接得上）
- 情景记忆：每 8 轮 / 新会话 / 退出时自动归档一段经历，跨会话按话题自动召回
- 程序记忆：干完活自动记一笔"要什么→用了什么→成没成→教训"，下次同类任务自动想起

**会干活（上下文与工具打通）**
- 读你电脑上的文件并总结/分析/找关键信息（txt/md/docx/xlsx/pdf/代码…）
- 读文件夹：列出内容并抽取可读文件，"分析一下 D:\某文件夹 里的文件"直接可用
- 打开电脑应用（微信/计算器/记事本/PowerShell…）与聊天里提到的路径
- 上下文关联：读完说"帮我分析刚才那个文件 / 那个文件夹"能定位
- 写并运行脚本；报错会**自动带错修复重跑一次**（计划-执行-验证环）
- 生成 PPT / Word / Excel 文档；全栈项目开发（前端+后端+数据库）
- 上网自学新主题，所学知识反哺聊天专业度

**会成长**
- 情绪随相处变化、性格随经历漂移、发育阶段从婴儿期长到成年期（桌面小人同步变化）

**更稳**
- 移除 Win7/8 与 PyQt5 向下兼容（最低 Windows 10）：旧系统在**安装阶段**即被
  明确提示；启动错误改为可读中文说明并落 crash.log，不再"双击后只闪一个谜之错误"
- 正常退出不再误弹"错误 0"；安装包约 55MB（无 torch 环境自动用内置轻量认知体）

## 下载与安装

最新版见仓库 **Releases**（v0.18.1，单文件约 55MB）：

1. 下载 `PASMStudio-Setup-0.18.1.exe`
2. 双击安装 → 打开 PASM Studio → 点右上「设置」填 LLM Key（或留空用本地 Ollama）
3. 开始聊天；要用「图像/视频/漫剧」真出片时，首次使用按提示接入出图引擎（约 1 分钟）

> 系统要求：**Windows 10/11 x64**（v0.16 起不再支持 Win7/8）· CPU 即可 · 首次安装解压约 1 分钟
> 未填任何 Key 也能用（本地演示模式：同样会记忆与成长，只是话术朴素）
> 历史版本与更新记录见 [CHANGELOG.md](CHANGELOG.md)；升级通道检查仓库 `latest.json`。

## 设置语言脑（一句话）

- **DeepSeek**：填官网 API Key，Base URL 保持 `https://api.deepseek.com/v1`，Model `deepseek-chat`
- **本地免费**：装 [Ollama](https://ollama.com) 后 `ollama run qwen2.5:7b`，
  设置里 Base URL 填 `http://127.0.0.1:11434/v1`、Model 填 `qwen2.5:7b`——完全离线

## 数据与隐私

- 记忆/配置/成长存档在 `%APPDATA%\PASMStudio`（卸载不删除，可随时清空记忆）
- 单机运行，不上传你的对话与文件（仅 LLM 调用本身走你配置的 API）

## 路线图（下一步）

- [ ] **自省训练闭环**：把真实交互沉淀成经验样本，反哺人格成长与轻量模型微调
- [ ] 语音对话打磨 / 云端同步 / 性格报告主页

## 生态与说明

| 仓库 | Gitee | GitHub | 定位 |
|---|---|---|---|
| **PASM**（私有） | [gitee.com/arronzheng/PASM](https://gitee.com/arronzheng/PASM) | [github.com/arronJack/PASM](https://github.com/arronJack/PASM) | 核心认知引擎 + 认知皮层 + 桌面全源码 + 文档（开发主仓镜像） |
| **pasm-qclaw**（本仓库，公开） | [gitee.com/arronzheng/pasm-qclaw](https://gitee.com/arronzheng/pasm-qclaw) | [github.com/arronJack/pasm-qclaw](https://github.com/arronJack/pasm-qclaw) | PASM Studio 产品发布：Releases 安装包 + 更新通道 + 文档 |
| **PASM-Lite**（公开） | [gitee.com/arronzheng/PASM-Lite](https://gitee.com/arronzheng/PASM-Lite) | [github.com/arronJack/PASM-Lite](https://github.com/arronJack/PASM-Lite) | 零 token 认知最小实现的教学版（约 260 行单文件，可读可改，MIT） |

产品当前为闭源发行（公开仓库分发编译产物与更新通道）。
开源计划（2026-09 拟定）：**pasm-qclaw 开发主仓后续开源**，供大家一起研究
桌面产品与双脑结合；**PASM 完整引擎 + 认知皮层暂不开源**，待进一步优化扩展后再议。
研究理念与教学代码可先行阅读 [PASM-Lite](https://gitee.com/arronzheng/PASM-Lite)。
问题 / 反馈 / 合作请提 issue——**主要归口 GitHub**（[issues](https://github.com/arronJack/PASM/issues)），Gitee 同步镜像也会看。

> **商标说明 / Trademark**：「PASM Studio」名称与品牌标识归 arronZheng 所有。
> MIT 许可授予的是代码版权而非商标权——Fork / 再分发请更换名称与标识。
> The name and branding of "PASM Studio" belong to arronZheng; the MIT license covers
> the code, not the trademark — please rename your forks.

---

<a id="english--英文完整版"></a>

# English · PASM Studio — A Desktop AI Companion That Thinks, Works, and Remembers You (Windows)

> **v0.27.0 All three directions landed: math brain + multimodal perception (vision/ASR) + quantum strategy + four trend items** — Chat right out of the box: it thinks,
**Works with zero API keys**: it auto-detects a local [Ollama](https://ollama.com) install (qwen/llama models) — your machine *is* the server. A DeepSeek key unlocks even better conversations (see *LLM setup* below).

## Latest: v0.27.0 (2026-09-09) · Math brain + multimodal + quantum strategy + four trend items

- **🧮 Math brain mathlab**: "analyze this table" → real computation: regression (R²) / descriptive stats + outliers / correlation / linear equations / topo sort / shortest path / connected components / Markov steady state — with a one-line Chinese conclusion
- **👁 Multimodal perception (Phase B)**: drop an image or paste a screenshot into the chat → a local Ollama vision model describes it (no cloud, zero cost); the next message understands the image. Speech-to-text chain: faster-whisper → Vosk fallback
- **⚛ Quantum strategy (Phase C)**: candidate strategies form a superposition; Boltzmann-annealed measurement picks the route — exploratory early, converging to the best path with experience; intra-family interference generalizes lessons; mood (PAD) acts as an external field; outcomes feed back into amplitudes
- **🔀 Model router**: chat → local Ollama (fast), work → DeepSeek (strong), review → local cross-check; auto-fallback to cloud when local is down
- **🔁 Team self-check retry + 🕸 topo validation**: a failed/empty/short step auto-retries with a quality self-check; flows support `deps` dependencies — cycles / out-of-range / self-loops are rejected before running
- **🏗 Experience sedimentation + 🧠 project-level memory**: finished projects auto-save flow templates for one-click reuse; team runs auto-inject the project archive
- Smoke: mathlab 29 · phaseA 22 · trends 29 · bc+topo 29 · UI regressions 105 = **all green**

- **📎 Visible @references**: typing `@` pops a file picker (recent artifacts / knowledge entries / local text files; ↑↓+Enter pick, keep typing to filter). Choosing one inserts `@path` and shows a 📎 chip above the input — click ✕ to drop it (also cleans the text). `@“paths with spaces”` supported. File bodies go into the model context only; your bubble stays clean
- **🐛 "@小U got chit-chat" fixed**: unresolvable references are no longer sent verbatim to the model (it used to treat `@小U` as a greeting); pure-reference messages now explain and strip the mark. `@` in pasted code is never touched
- **🏢 Teams = real per-project crews**: a new project starts **blank** (crew & flow are built by you); each project has its own members — hire in (🌐 / 🎓 / ＋ / 🧑‍💼 pool), move out anytime (pool record kept); switching projects shows that project's own members / flow / **step status ✅⚠️○** / **run archive with outcomes**; runs write step status back automatically; "让团队做 X" still boots a built-in crew
- Smoke: full regression green (v0261 26 · v0262 22 · v0260 39 · v0250 33+33 · v0240 57+15 · v0231 26 · v022x/v0190)

## v0.26.2 (2026-09-08) · tool-error fix / PASM emotion & memory really shaping replies / same-file edits

- **🐛 Tool error fixed**: the capability menu's 8 quick-action anchors were missing a list wrapper → `str.join() takes exactly one argument (8 given)` crash is gone
- **❤️ PASM emotion & memory are now felt**: sentiment lexicon widened to 40+ spoken phrases (complaints, errors, "卡死" all move the mood); mood feedback is applied in one decisive step (no more dilution/clamping); mood is translated into a "此刻口吻 / tone-now" instruction injected into every reply (happy → light & playful, down → softer & shorter, urgent → straight to the point); tables/plans/PPT/dev now auto-carry your related memories & preferences
- **📄 Same-file precise re-edits**: "表格再加两行 / 文档补一节 / PPT 换配色" now edits **the same file** — the previous full content is fed back to the model (keep the theme & structure, change only what you asked), then written back in place; "另存/新做一份" opens a new one; product memory persists with the conversation
- Smoke: v0262 adds 18 checks, all green; v0261/v0260/v0250/v0240/v0231/v0230 full regression green

## v0.26.1 (2026-09-08) · five real-machine fixes

- **🩺 Health card compacted** to a small footer strip — the auto-activity feed is the main area again
- **👥 Team page redesigned into cards**: project bar → members / flow cards → run area; member rows show ★ rating, single-click opens the employee card (⭐ manual rating), right-click fires
- **🎓 3-tier skills now real**: built-in 🎯 experts ×2 (content director / data analyst) and 🔌 connectors ×3 (web search / local files & apps / office documents); the add-skill wizard asks for the tier
- **📂 App launching truly works**: "小U，帮我打开网易云音乐" now really opens it — name-prefix stripping + brand install-path probing across all drives + real process verification after launch (never fake "opened")
- **⏳ Long tasks never lose their reply**: real-machine root cause fixed — heavy work shows a "working…" notice, returning to the conversation shows the finished reply immediately, and turn-finalization has a hard fallback that always writes the reply back into its session file
- Smoke: v0261 adds 25 checks (UI offscreen + turn lifecycle + brand probing) all green; v0260/v0250/v0240/v0231/v0230 full regression green

## v0.26.0 (2026-09-08) · project ledger / real file development / @refs & slash commands

- **📒 Project ledger**: every project (copy/table/PPT/dev) keeps a working ledger — request history, what changed each round, smoke results, your standing rules; follow-up edits auto-inject the digest for precise changes, never "lost in the weeds"
- **🛠 Real file development**: `/开发 <spec>` or "用php/java/vue/go/c#/c++ 开发…" → pick a target folder and write a real project (8-language scaffolds, per-file generation, per-language static smoke); every round is recorded, "改这个项目" continues precisely
- **📎 @references**: `@file` or `@knowledge entry` in a message pulls the content into context
- **/️⃣ Commands**: `/开发 <spec>` · `/技能 <skill> <task>` · `/项目` (view ledger)
- (v0.25.0: company-style teams · faster chat · background self-heal — see CHANGELOG)
- **🧬 PASM core engine parity (0.5.0)**: multi-agent team & self-heal moved into the engine executive cortex (`pasm.cognitive`); desktop side is now a thin facade — one shared implementation
- **👥 Multi-agent skill team**: built-in role cards (PM/Planner/Dispatcher/Developer/Reviewer/Debugger), add your own "employee cards" and compose custom flows; say "let my team do X" in chat — review-fail auto-rework, auto-retry, fully visible steps
- **🩺 Self-check · self-diagnose · self-heal**: silent log sentinel records crash cases; file+line level diagnosis reports; report/auto-fix modes (auto-fix = patch → smoke regression → auto-rollback on failure; frozen builds forced to report-only)
- (v0.24.0: Ark 404 fix · theme hardening / v0.23.0: chat-only gate · queued concurrency · true PASM×LLM synergy — see CHANGELOG)

## What makes it different from a regular chatbot

A normal chatbot starts over every turn — no memory of you, no sense of "self".
PASM Studio is a **dual-brain architecture + cognitive execution cortex**:

| Part | Role | Result |
|---|---|---|
| **Language brain** (DeepSeek / any OpenAI-compatible / local Ollama) | Understands you, composes replies, asks back | Conversations that feel human |
| **PASM cognitive brain** (local, fully offline) | Every chat → emotional drift, personality shift, layered memory | It genuinely grows up and genuinely remembers you |
| **Cognitive execution cortex** (since v0.15) | Judges intent & mood before speaking → picks stance and tone; recalls relevant experiences by topic | No more "one-size-fits-all"; it remembers how things got done last time |

## Core capabilities (accumulated since v0.16)

**A chat UI that stays out of the way**
- **[💬 Chat] / [🔧 Work]** mode split: chat mode never starts working just because you *mentioned* "an app" or "a script" — you decide when it acts
- Large multi-line input (Enter to send / Shift+Enter for newline); switch LLMs anytime (cloud DeepSeek / any local Ollama model — remembered)
- **Session list** on the left: every message is saved in real time; switch sessions or restart freely

**Learning that sticks (knowledge base v3 + self-evolution)**
- Each knowledge entry = full text + key points + source; **what it learns, it keeps verbatim** — self-study searches multiple sources and stores full articles (since v0.16.5)
- Double-click any entry to open the full-text reader; keyword filtering supported
- Automatic self-study on by default, topics chosen by a built-in "curiosity selector"; after learning it quizzes itself and honestly tracks what it didn't understand

**Extensible skills**
- Drop new skills (name + description + procedure, standard `SKILL.md` format) into the data directory; they're auto-discovered and invoked when relevant
- Built-in examples: video scripting / marketing copy / manga one-take pipeline

**Layered memory**
- *Working memory*: current topic + goals in flight (persisted on exit)
- *Episodic memory*: experiences archived every 8 turns / per session, recalled by topic across sessions
- *Procedural memory*: after each task it records "asked → used → worked? → lesson", and remembers next time

**It actually works**
- Reads and summarizes files on your computer (txt/md/docx/xlsx/pdf/code…)
- Reads folders ("analyze the files in D:\some-folder"), opens apps (WeChat/Calculator/Notepad/PowerShell…)
- Writes & runs scripts; on errors it **auto-repairs and re-runs once** (plan–execute–verify loop)
- Generates PPT / Word / Excel documents; full-stack project development (frontend + backend + database)
- Generates real images (local Stable Diffusion WebUI or cloud engines), short videos, and manga episodes with subtitles and voice-over (HTML player + real MP4 when ffmpeg is present)

**It grows**
- Emotions shift with interaction, personality drifts with experience, and it develops from infancy to adulthood (the desktop avatar changes with it)

**Reliability**
- Windows 10/11 x64 minimum (Win7/8 dropped); startup errors are readable messages + `crash.log` instead of a mysterious flash
- ~55 MB installer; runs without torch (built-in lightweight cognitive core)

## Download & install

Grab the latest from **[Releases](https://github.com/arronJack/pasm-qclaw/releases)** (v0.18.1, single ~55 MB file):

1. Download `PASMStudio-Setup-0.18.1.exe`
2. Install → launch PASM Studio → open ⚙ Settings and enter an LLM key (or leave empty for local Ollama)
3. Start chatting. For real image/video/manga output, connect an image engine on first use (~1 minute)

> Requirements: **Windows 10/11 x64** · CPU is enough · ~1 minute to unpack on first install.
> Works with no API key at all (local demo mode: still remembers and grows, plainer speech).
> Full history in [CHANGELOG.md](CHANGELOG.md); the updater checks `latest.json` in this repo.

## LLM setup (one line each)

- **DeepSeek**: paste your API key; Base URL `https://api.deepseek.com/v1`, Model `deepseek-chat`
- **Free & local**: install [Ollama](https://ollama.com), run `ollama run qwen2.5:7b`; Base URL `http://127.0.0.1:11434/v1`, Model `qwen2.5:7b` — fully offline

## Data & privacy

- Memory / config / growth archives live in `%APPDATA%\PASMStudio` (not removed on uninstall; wipe anytime)
- Runs standalone; your conversations and files never leave your machine (only LLM API calls you configure go online)

## Roadmap

- [ ] **Self-reflection training loop**: distill real interactions into experience samples, feeding personality growth and light model fine-tuning
- [ ] Voice conversation polish / cloud sync / personality report home page

## Ecosystem

| Repo | Visibility | Role |
|---|---|---|
| **PASM** (private) | Private mirror on Gitee & GitHub | Core cognitive engine + cortex + full desktop source (development main repo mirror) |
| **pasm-qclaw** (this repo) | Public | PASM Studio releases: installers, update channel, docs |
| **PASM-Lite** (public, MIT) | Public | Minimal token-free cognitive engine for teaching (~260 lines, readable & hackable) |

The product is currently distributed as compiled binaries (public repos host builds and the update channel).
Open-source plan (drafted 2026-09): the **pasm-qclaw development repo is planned to open up** so everyone can study the desktop product and the dual-brain integration; the **full PASM engine + cortex stay closed for now**, pending further refinement. In the meantime, read the research concepts and teaching code in [PASM-Lite](https://github.com/arronJack/PASM-Lite).
Issues / feedback / collaboration: open an issue on GitHub ([issues](https://github.com/arronJack/PASM/issues) — primary tracker); the Gitee mirror is monitored too.

> **Trademark**: the name and branding of "PASM Studio" belong to arronZheng.
> The MIT license grants code rights, not trademark rights — please rename your forks
> and redistributions.
