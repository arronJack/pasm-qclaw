# PASM Studio v0.31.15 · 紧急修复：「🖥 开发」工种整条链路失效

真机报障驱动的紧急修复（0.31.14 实测「开发」完全不可用）。共诊断 7 条根因，本版落地 P0（R1–R5），完整诊断见主仓 `docs/qa/2026-09-30-开发工作流失效诊断.md`。

## 修了什么

| # | 症状（0.31.14 实测） | 根因 | 修法 |
|---|---|---|---|
| R1 | 首轮就报「执行工具时出错：No package metadata was found for httpcore2」 | 改名包 `httpcore2` 在冻结包里偶发 `PackageNotFoundError` → OpenAI 客户端构造崩 | 元数据读取 try/except 兜底 + `llm_gateway.client_for` 包一层可读错误 |
| R2 | 说「文件放 H:\geo_plaform」被无视，文件跑去工作根另建乱名项目 | `_extract_target_dir` 只认已存在目录，待创建路径直接判空 | 允许「父目录存在」的待创建路径；新项目直接落用户指定目录，UI 显示真实路径 |
| R3 | 项目名乱码「恩怪我怪我没有说清楚能否」 | 兜底取整句前 12 字当项目名 | 显式指定输出目录时，项目名直接用目录 basename（geo_plaform） |
| R4 | 要 SpringBoot+Vue，给的是 Python+单页 HTML，还幻觉出 Django | `base_sys` 写死「前端单文件 HTML + 后端 Python」 | 按需求关键词（spring/vue/react/python/node）动态拼装提示词与技术栈说明 |
| R5 | 所有代码塞进 main.py，内容是中文说明文 → SyntaxError | 模型退化为字典格式 → 解析为空 → 毒兜底把整段回复写进 main.py | `parse_bundle` 兼容字典格式；删除散文兜底，改为「严格格式重试一次 → 仍空则如实告知」 |

P1（R6 多技术栈启动命令 / R7 自检即停）下一版做。

## 验收

- 修复逻辑单元自测通过：待创建路径识别（`C:\geo_plaform_test_xyz`）、项目名取 basename、SpringBoot+Vue 检测（java=True, vue=True）、`parse_bundle` 三种格式（标记 / 双引号字典 / 单引号字典）全通过。
- 冻结安装包真启动冒烟：存活 30 秒、**0 个崩溃特征**（`FROZEN_SMOKE_PASS`）；模拟退化链路冒烟通过、引擎工厂自检 ✓。

## 下载

- **GitHub / GitCode**：`PASMStudio-Setup-0.31.15.exe`（单文件整包，约 193MB）
  - SHA256：`47e89ee133dcf7f375d27e8c6b298b5330458c9d9c8752f0007a715f6e745e8e`
- **Gitee**：分卷版 `PASMStudio-Setup-0.31.15-gitee.exe` + 3 个 `.bin` 切片（全部下到同一目录后直接运行 exe，无需手动合并）
  - `-gitee.exe`：`d84e0e4f1748e407a25f2754f2c7acf4b159d2d80a4559ceb18a1d2a326335c0`
  - `-1.bin`：`7ade2d09e1c4367a5174ee782dcc030724a15b0870a0706e7f592a0a64a896c3`
  - `-2.bin`：`56a56f4937d2c3ce47929114d6b33b892a59cfa610eab5b27534d4928ece7bf0`
  - `-3.bin`：`ad1afee299d010135e606ac5de637b44bd60a63e7fbe1c5f9dea7103a0ec9a8b`
- **macOS / Linux**：本版未重出（CI 构建中，完成后补挂本 Release），可暂用上一版安装包。

升级通道（应用内检查更新）已随仓库 `latest.json` 同步至 0.31.15。
