# PASM Studio v0.31.16 · GEO 平台复测整批修复：落盘混乱 / 开发被限流 / 虚报成功

> 对应真机复测：在 0.31.15 上提「**在D盘建一个geo文件夹**，用 **springboot-vue** 做前后端分离的
> **GEO 优化平台**（后端 API、后台管理、前端展示与操作、接入 AI 分析与排行榜）」。

## 这次修了什么

| # | 你看到的现象 | 真根因 | 现在的行为 |
|---|---|---|---|
| ① | 你说了「在 **D 盘**建 geo 文件夹」，它却在桌面默认目录建了个 `geo文件夹`（空的） | 自然语言盘符（"D盘"）不被识别 → 目标为空 → 项目名兜底取了「geo文件夹」三个字 | 解析成 `D:\geo`，项目名 `geo`；字面路径（如 `H:\geo_plaform`）仍然优先 |
| ② | 项目没按需求做，只落了一个 `pom.xml` | **开发任务被当聊天限流**：按"60 秒出结果"把单轮长度从 3000 收到 **720 token**，全栈项目刚开头就被截断 | 开发走**独立额度**：单轮 **8000 token** / 目标 **10 分钟** / 三段续写，后端+前端+配置一次写全 |
| ③ | 只生成 1 个文件，它却回「🏗 项目搭好了！共 1 个文件」 | 产出不完整也走成功话术 | 前后端分离类需求产出 **<3 个文件即如实判失败**，并给出两条出路：填云端 Key 重发 / 说「分步生成」按模块拼 |
| ④ | 文件跑到 `D:\geo\pom.xml`，注册的项目目录却是空的 | 模型输出带盘符的路径 `D:/geo/pom.xml`，拼路径时**项目前缀被整个丢掉**直写盘根（同时是路径逃逸口子） | 路径**归一化收进项目目录**；含 `..` 的越界路径直接拒绝 |
| ⑤ | 项目生成了却不知道怎么跑 | 只找 `.py/.js` 入口，SpringBoot/Vue 项目一律"没找到可执行入口" | 按技术栈给命令：`mvn spring-boot:run` / `npm install && npm run dev`（读 `package.json` 的 scripts） |

**另外两件**：

- **自检不再空转**：文件里混进生成标记（生成层降级污染）或过半文件语法不过 → 立即停止并如实报告；
  自检总预算 **180 秒**（不再让你对着「复查中」等六分钟）。
- **安装包版本属性修好**：以前文件属性里显示的版本号停在旧版（0.31.14），现在与安装包一致
  （构建时从 `appinfo.py` 自动同步，不会再漂移）。

> 本版同时包含 **V2 认知引擎 2.0.0a11**（契约 1.2，新增可选面：LLM 桥 / 语言出口 / 成长落地）
> 与 0.31.15 的全部修复。

## 下载与安装

- **GitHub / GitCode**：`PASMStudio-Setup-0.31.16.exe`（单文件整包，202.6 MB）
- **Gitee**：`PASMStudio-Setup-0.31.16-gitee.exe` + `.bin` 分卷 ——
  把 exe 与**全部 .bin 分卷下载到同一个文件夹**，直接运行 exe 即可（**不需要手动合并**）。

已安装旧版的用户：直接装这一版覆盖即可（设置、记忆、工作记录都保留）。

### 校验（SHA256）

```
1cbf24d3355ee54ca63c61261cefae8fe718a7e7c8627e7d88ac9c2582a5245c  PASMStudio-Setup-0.31.16.exe        202,644,500 B
ee0e74584ecb2aa8744d9a6f209d0be4c418c8fb0a45902e35458641ab144286  PASMStudio-Setup-0.31.16-gitee.exe       2,596,284 B
5cf2f9f0908f18f74992c60358b20dcae47d8cf281597a83dad77c4300c11140  PASMStudio-Setup-0.31.16-gitee-1.bin    96,403,648 B
d512b77ba8f520766eb581b79aceef0ab5a9c614f69c344259b63a32da1b86b6  PASMStudio-Setup-0.31.16-gitee-2.bin    99,000,000 B
292920c26c8fe356e17b22bd539c098f66ef02ac4a9c1f81694cc421526baa95  PASMStudio-Setup-0.31.16-gitee-3.bin     4,832,328 B
```

## 本版验收（发版前实跑）

| 检查 | 结果 |
|---|---|
| 修复回归脚本 `tools/desktop_verify/verify_v0316.py` | **35/35 通过** |
| 开发端到端回归 `verify_dev_e2e_v0312.py` | **28 通过 / 0 失败** |
| V2 开关 `verify_v2_switch.py` / V2 表达层 `verify_v2_expression.py` | **28/28** · **39/39** |
| pasm2 自校验 + 契约镜像一致性 | **29 项 ALL GREEN** · 镜像一致 |
| 冻结包冒烟 / **装机后**真启动冒烟 | 进程存活、**0 崩溃特征**、落盘正常 |
| 产物核验 | exe 版本属性 = 0.31.16.0；包内 pasm2 = 2.0.0a11；契约 1.2 |

## 验收目标（你可以照这句话复测）

> 「在 **D 盘**建一个 geo 文件夹，用 springboot-vue 做前后端分离的 **GEO 优化平台** ——
> 后端 API、后台管理、前端展示与操作、企业入驻、GEO/SEO 专业优化，接入 AI 分析与排行榜。」

期望：真落 `D:\geo`（项目名 `geo`）、含 `backend/` + `frontend/`（Spring Boot 3 + Vue 3 + Vite）、
启动命令给 `mvn spring-boot:run` 与 `npm run dev`；如果模型这轮产出不完整，
它会**如实说没做成**并给出下一步，而不是回你「搭好了」。

---

### English (short)

**v0.31.16 fixes the whole batch from the GEO-platform retest.** (1) "D drive" phrasing is now
understood (`D:\geo`, project name `geo`); (2) dev work is no longer throttled — it has its own
budget (8000 tokens/turn, 10-minute target, 3 continuations) instead of the old 720-token chat cap
that truncated full-stack projects; (3) incomplete output (<3 files for a front/back-separated
request) now **honestly reports failure** instead of claiming "it's done"; (4) drive-lettered paths
are normalized back inside the project directory (`..` rejected) instead of escaping to the drive
root; (5) startup commands match the stack (`mvn spring-boot:run` / `npm run dev`). Plus a
180-second self-check budget with immediate stop on polluted/systemically broken output, and the
installer's file-properties version number finally matches the release. Also bundles **V2 engine
2.0.0a11** (contract 1.2) and all 0.31.15 fixes.
