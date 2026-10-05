# PASM Studio v0.31.21（2026-10-05）

## 主题：根治生成链路的系统性可靠性缺陷（基于 H:\geo 真实产物实证）

用户在另一台机器上实测 H:\geo 项目，日志从应用自检到构建失败簿给出了确凿证据：生成链路有多处
系统性失效，缺陷全部漏到用户手上、只能手动修。本轮把报告里的 8 类问题逐条核对源码，区分"已修"
与"仍漏"，并彻底根治以下根因（其余"续改路由写死"已在 0.31.17/0.31.20 修过、语法/jakarta/pom
类缺陷随真编译与 JDK 自适应间接闭环）：

### 1. 校验不再空转（治本）
- `devloop._static_check` 新增 `.java` 启发式检查（括号平衡 + 残留 ``` / `===FILE:` 标记 +
  行首 `#` 注释）；
- `verify_project` 的前端分支从 `pass` 改为**真跑** Vue SFC 标签平衡校验 + `node --check`，
  专门抓 Report.vue 那种写到一半的 `<style>`。

### 2. 写出完整性
- `agent_tools.parse_bundle` 剥离体内残留的 `===FILE:` / `===END===` 标记
  （修 Report.vue 被下一个文件标记吞进上一文件 body 的拼接缺陷，含行内残标）；
- `save_project` 折叠与项目根同名的顶层段（修 `frontend/frontend` 嵌套导致 npm install 失败）。

### 3. JDK 工具链自适应
- 生成 Java 项目前先 `probe_jdk()` 探测 javac 主版本；本机 JDK<17 时注入
  "生成兼容 Java 8 / Spring Boot 2.7 / javax" 指令，而不是生成一堆编译不过的 Java 17 代码；
- `verify_project` 把"无效目标版本"类失败**解码成清晰的 JDK 不匹配提示**。

### 4. 监控不再盲区
- `run_project` 真正执行 `mvn compile` / `npm build` 并捕获真实退出码，
  账本不再把"已返回命令"误报成"构建成功"；
- `selfheal` 新增 `record_build_issue`，自检/构建失败写入状态，不再显示"0 错误"的虚假健康。

### 附：单元用例当场拦截并修复的两个会线上崩溃的真 bug
- `_java_quick_check` 的 `close_map` 把 `"}"` 错写成 `"}}"`（任何含 `}` 的 Java 文件都会 `KeyError`）；
- `coder.probe_jdk` 用了 `shutil.which` 却没 `import shutil`（`NameError`）。

---

## 下载与安装
- **Gitee 为分卷版**：把 `PASMStudio-Setup-0.31.21-gitee.exe` 与同目录全部 `.bin` 分卷
  **下载到同一个文件夹**后直接运行 exe 即可，无需手动合并。
- **GitHub / GitCode 为单文件整包**：直接运行 `PASMStudio-Setup-0.31.21.exe`。
- macOS / Linux 包请到 GitHub 或 GitCode 下载对应整包。
- 升级通道（`latest.json`）已更新到 0.31.21，客户端会自动提示。

---

# PASM Studio v0.31.21 (2026-10-05) — English

## Topic: fix the systematic reliability holes in the generation pipeline (evidence from the H:\geo project)

This release plugs the systemic reliability holes that let broken generated code slip through to the user.
We audited all 8 issues from the field report against the source, fixed the real root causes, and left the
already-fixed ones alone.

### 1. Verification is no longer a no-op
- `_static_check` now really checks `.java` (brace balance + leftover fence / `===FILE:` markers + `#` comments);
- the frontend branch of `verify_project` now really runs Vue SFC tag-balance checks + `node --check`
  instead of `pass`, catching half-written `<style>` blocks like the old Report.vue.

### 2. Cleaner multi-file output
- `parse_bundle` now strips stray `===FILE:` / `===END===` markers embedded inside a file body
  (fixes Report.vue being swallowed by the next file's marker, including inline leftovers);
- `save_project` collapses top-level segments that duplicate the project-root name
  (fixes `frontend/frontend` nesting that broke `npm install`).

### 3. Java toolchain auto-adapts
- `probe_jdk()` detects the local javac version before generating a Java project; if the local JDK < 17,
  it injects instructions to generate Java 8 / Spring Boot 2.7 / javax compatible code instead of
  Java-17 code that won't compile;
- `verify_project` decodes "invalid target release" failures into a clear "JDK version mismatch" message.

### 4. Monitoring is no longer blind
- `run_project` now really runs `mvn compile` / `npm build` and captures the real exit code,
  so the ledger no longer reports "built successfully" when nothing was actually built;
- `selfheal` records build/verification failures, so status no longer shows a fake "0 errors".

### Bug fixes caught by unit tests
- `_java_quick_check` mapped `"}"` to `"}}"` (KeyError on any Java file with `}`);
- `coder.probe_jdk` used `shutil.which` without `import shutil` (NameError).
