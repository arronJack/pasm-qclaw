# PASM Studio（桌面）· 产品说明与打包

> 面向开发者：PASM 私库内的 `desktop/` 是 **PASM Studio** 桌面产品的完整源码，
> 公开发行镜像在 Gitee 仓库 `arronzheng/pasm-qclaw`（同样代码 + Releases 安装包）。
> 约定：引擎/桌面改动**先改本仓库**，再同步 qclaw 镜像；分发的是编译后产物，源码留在私有仓库。

## 产品是什么

**PASM Studio · 界面与自主能力版 v0.23.0** —— Windows 桌面 AI 伙伴（桌面小人 + 聊天窗）：

- **双脑结构**：语言脑（DeepSeek / 任意 OpenAI 兼容 / 本地 Ollama）负责"说"；
  PASM 认知脑（情绪 / 性格 / 发育 / 分层记忆）负责"怎么想、记得你"。
- **会干活的助手**：读文件 / 读文件夹 / 打开应用 / 打开聊天里的路径 / 写并运行脚本 /
  生成 PPT·Word·Excel / 全栈项目开发（Python·JS·HTML·Go·Java·Node）/ 上网自学。
- **会成长**：情绪随相处变化、性格随经历漂移、发育阶段婴儿期→成年期（聊天窗与桌面小人一致）。
- **v0.16 新形态**：聊天/干活模式分栏（聊天永不自动开工）；多行大输入框 + 大模型切换；
  左侧会话列表（实时落盘，聊天永不丢）；技能系统（可增删技能、自动检索调用）；
  桌面小人情绪状态机 + 7 套性格 + 性别；自动上网学默认开 + 学后自测。
- **v0.16.5 资料库/学习链路**：学到=原文入库（要点不卡学习）；资料库双击读全文、
  书架整本阅读、关键词过滤；自动化动态只记真实结果；工种分栏按文档/创作分组归位；
  语音兜底改系统自带引擎（离线可发声）；改名实时同步；导航可回对话。
- **v0.16.6 语音/工作台/输入精修**：edge 秒级网络预检防 DNS 挂死（连不上约 0.2s 判定即落
  系统语音）；工种栏去组标题直接排按钮；工作台分类胶囊右上角 ✕ 删除 + 磁盘对账；
  输入框圆角 14px + `/` 弹出指令/工种/技能快选窗。
- **v0.16.7 语音出声/删除修复/Markdown**：System.Speech 声线选择花括号转义修复（此前从未
  执行、总用系统默认声线读中文无声）——本机验证选中 Microsoft Huihui(zh-CN) 朗读成功；
  voice.log 诊断日志 + 设置页「🔔 系统音」测试；工作台/会话右键 QMenu 导入修复（此前
  一点右键即 NameError 弹不出菜单）；条目删除按类型二选一（仅删记录保留文件 / 连文件删）
  并二次确认；资料库阅读器与聊天 Markdown 结构化渲染。
- **环境要求（v0.16 起）**：Windows 10/11 x64 · Python 3.10+（构建用 3.13）·
  PySide6（Qt6）单后端——不再兼容 Win7/8，不再回落 PyQt5。

## 关键代码（v0.16.7）

| 模块 | 作用 |
|---|---|
| `cog.py` | 会话级认知状态机：开口前产出"意图/姿态/温度/检索词"（核心镜像 `pasm/cognitive/cog.py`） |
| `memory_layers.py` | 分层记忆：工作/情景/程序，自动归档自动召回（核心镜像 `pasm/cognitive/memory_layers.py`） |
| `pasm_light.py` | 无 torch 时与七层引擎同接口的轻量认知体（核心镜像 `pasm/light.py`） |
| `pasm_companion.py` | 主聊天窗：意图识别 → 认知皮层 → LLM/本地脑 → 工具执行 → 复盘记忆 |
| `agent_tools.py` | 工具集：路径解析/读文件夹/打开应用/脚本沙箱/联网学习/文件生成 |
| `pasm_main.py` | 产品启动器（桌面小人 + 自动开窗 + crash.log；Win10 起步检查）；**打包入口** |
| `pasm_pet.py` | 桌面小人本体 + 持续学习 + 打盹/复习/自动上网 |
| `skillstore.py` | 技能库：内置（`skills/`，随包分发）+ 用户技能（`%APPDATA%\PASMStudio\skills`）；list/search/add/remove |
| `skills/*.md` | 内置技能规程示例：视频脚本创作 / 营销文案 / 漫剧一条龙 |
| `qt_compat.py` | Qt 命名层（v0.16 起 PySide6 单后端，不再双后端回落） |

> 注意：`pasm_desktop.py` 为早期游戏脚手架，勿作入口；PyInstaller spec 指向 `pasm_main.py`。

## 打包流程（Windows）

```bat
:: 1) 编译（构建环境：Python 3.13 venv，装 PySide6 + PyInstaller 6）
"C:\...\python.exe" -m PyInstaller --noconfirm --clean PASMStudio.spec
::    -> dist\PASMStudio\PASMStudio.exe（onedir）

:: 2) 生成安装向导（需 Inno Setup 6；installer.iss 已设 MinVersion=10.0）
"/c/Program Files (x86)/Inno Setup 6/ISCC.exe" /DAppVersion=0.16.7 desktop/installer.iss
::    -> installer\PASMStudio-Setup-0.16.7.exe（约 55MB 单文件，可直传 Gitee Release）
```

版本号：改 `appinfo.py` 的 `APP_VERSION`，同步 `desktop/latest.json` 与安装脚本。

## 运行与数据

- 安装版数据在 `%APPDATA%\PASMStudio`（卸载不删）：`pet_state.json`（成长存档）、
  `config.json`、`notes.json`/`prefs.json`（记忆）、`episodic.json`/`procedural.json`/
  `working.json`（分层记忆）、`crash.log`。
- 开发模式用环境变量 `PASM_STUDIO_DIR=<目录>` 隔离数据。
- 未捕获异常由启动器写入 `%APPDATA%\PASMStudio\crash.log` 并弹窗提示。

## 引擎可选化（重要架构决策）

完整引擎 `pasm.agent` 依赖 torch（~100MB）。策略：`pasm_companion.py` 顶部条件导入——
`import pasm.agent` 失败时自动回退 `pasm_light.PASMAgent`（纯 Python，接口一致，
snapshot 字段齐全）。`PASMStudio.spec` 显式排除 torch/numpy/引擎子模块，
安装包约 55MB（vs 带 torch 的 143MB）；开发环境装了 torch 仍走完整引擎。

## 同步约定（核心 ↔ 桌面 ↔ 公开镜像）

```
E:\AI\PASM                权威私库：pasm/（引擎+认知皮层）+ desktop/ + docs/ + tests/
   └─ desktop/cog.py          ↔ pasm/cognitive/cog.py           （镜像同源）
   └─ desktop/memory_layers.py ↔ pasm/cognitive/memory_layers.py
   └─ desktop/pasm_light.py    ↔ pasm/light.py
E:\AI\pasm_qclaw          开发主仓（纯本地，计划开源）：desktop/ + pasm/ 整目录
E:\AI\pasm_qclaw_release  公开发行镜像（gitee arronzheng/pasm-qclaw）：
                           README / CHANGELOG / latest.json + installer 安装包
```

改动认知皮层/桌面代码后：① 同步 desktop ↔ pasm/cognitive；② 复制到 PASM 私库
与 qclaw 开发主仓；③ 更新 RELEASE_NOTES / CHANGELOG / latest.json；
④ 打包 → 同步发行镜像 → 推 Gitee 公开仓 → 建 Release 传安装包。

> 开源策略（2026-09）：pasm-qclaw 开发主仓计划后续开源供共同研究；
> PASM（完整引擎 + 认知皮层）暂不开源，等进一步优化扩展后再定。
