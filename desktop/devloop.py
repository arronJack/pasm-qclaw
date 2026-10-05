# -*- coding: utf-8 -*-
"""devloop.py —— 开发任务的「真干活」内核（对标 WorkBuddy 的四件硬东西）。

为什么单独成模块：`pasm_companion.py` 已逾万行，"干活内核"再塞进去就无法被
单独测试。这里只放**纯逻辑 + 受控执行**，不依赖 Qt、不依赖 app 状态。

四件硬东西在本模块的落点：
  ① **真工具调用循环**：`agent_loop()` —— 模型输出工具调用（文本协议，对 4B 级
     小模型友好），工具**真的执行**，结果**回灌下一轮**，直到 done/无调用/预算尽。
  ② **写→跑→读错→改 闭环**：`build_fix_loop()` —— 真跑构建/校验命令，把
     stderr 原样回灌，让模型只改出错的文件，再跑，最多 N 轮。
  ③ **任务分解**：`decompose()` —— 按技术栈把需求切成**有序小步**（每步 1~2 个
     文件 + 该步的验证方式），而不是"一枪打整包"。
  ④ **每步验证与失败重试**：`run_step()` / `verify_project()` —— 每步做完立刻
     验证（静态必做 + 有工具链就跑真构建），失败带错误重试，重试仍失败则
     **保留骨架 + 标记 TODO 继续下一步**，绝不让整条任务卡死。

安全边界（执行外部命令是危险动作，规则写死在这里）：
  · 命令白名单（mvn / npm / node / python / pytest …），**不用 shell**、不解析管道
    与重定向，杜绝 `;`、`&&`、`|`、`>`、反引号、`$()` 注入；
  · 只允许在项目目录内执行；路径必须落在项目目录内（拒绝盘符与 `..` 逃逸）；
  · 超时可配、输出截断；一切命令都记进结果结构，便于如实上报。

自检：`python devloop.py selftest`
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time

# —— 兄弟模块（都可选：devloop 必须能独立跑自检，缺一个也不能崩）——
try:
    import repomap as RM            # P0-2：项目地图 + 相关文件
except Exception:                                                # noqa: BLE001
    RM = None
try:
    import agent_tools as AT        # 0.31.19：落盘净化（剥 markdown 围栏 / 修注释符号）
except Exception:                                                # noqa: BLE001
    AT = None
try:
    import failbook as FB           # P1-5：失败归因 + 已知坑
except Exception:                                                # noqa: BLE001
    FB = None
try:
    import trace as TR              # P1-6：轮次级观测
except Exception:                                                # noqa: BLE001
    TR = None
try:
    import subagents as SA          # P1-4：真并行
except Exception:                                                # noqa: BLE001
    SA = None

# ----------------------------------------------------------------------------
# ① 受控执行：白名单 + 不用 shell
# ----------------------------------------------------------------------------
#: 允许作为**首词**的可执行程序（开发/构建/测试用途）。
ALLOWED_PROGRAMS = {
    "mvn", "mvnw", "gradle", "gradlew",
    "npm", "npx", "pnpm", "yarn", "node", "deno", "bun",
    "python", "python3", "py", "pip", "pip3", "uv", "pytest",
    "dotnet", "go", "cargo", "make", "javac", "java",
    "tsc", "vue-tsc", "vite", "eslint", "ruff", "black", "mypy",
}

#: 出现这些字符就直接拒绝 —— 它们是 shell 语义，而我们刻意**不经 shell**执行，
#: 放行只会让"看似能跑"的命令其实是注入面。
_DENY_CHARS = (";", "&&", "||", "|", ">", "<", "`", "$(", "${", "\n", "\r", "\x00")
#: 危险程序名（即便被写到参数里也不给过）
_DENY_PROGRAMS = {"rm", "rmdir", "del", "erase", "format", "shutdown", "reg",
                  "regedit", "powershell", "pwsh", "cmd", "bash", "sh",
                  "curl", "wget", "ssh", "scp", "sudo", "chmod", "chown", "taskkill"}


def safe_cmd_ok(cmd: str) -> tuple:
    """判断命令是否可执行。返回 (ok, 说明)。"""
    c = (cmd or "").strip()
    if not c:
        return False, "空命令"
    for bad in _DENY_CHARS:
        if bad in c:
            return False, "含 shell 语义字符 %r（本模块刻意不经 shell 执行）" % bad
    try:
        parts = shlex.split(c, posix=(os.name != "nt"))
    except ValueError as ex:
        return False, "命令无法解析：%s" % ex
    if not parts:
        return False, "空命令"
    prog = os.path.basename(parts[0]).lower()
    if prog.endswith(".exe") or prog.endswith(".cmd") or prog.endswith(".bat"):
        prog = os.path.splitext(prog)[0]
    if prog in _DENY_PROGRAMS:
        return False, "『%s』在禁止清单里" % prog
    if prog not in ALLOWED_PROGRAMS:
        return False, "『%s』不在白名单（可用：%s…）" % (
            prog, ", ".join(sorted(ALLOWED_PROGRAMS)[:8]))
    return True, ""


def _inside(pdir: str, rel: str) -> str:
    """把相对路径解析到项目目录内；越界/非法返回空串。"""
    r = (rel or "").strip().strip('"').strip("'").replace("\\", "/")
    if not r or r in (".", "./"):
        return os.path.realpath(pdir) if r else ""
    if re.match(r"^[a-zA-Z]:", r) or r.startswith("/") or r.startswith("~"):
        return ""                                   # 盘符 / 绝对路径：收进项目内也不安全
    parts = [p for p in r.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return ""
    full = os.path.realpath(os.path.join(pdir, *parts))
    base = os.path.realpath(pdir)
    if full != base and not full.startswith(base + os.sep):
        return ""
    return full


def run_cmd(cmd: str, cwd: str, timeout: int = 300) -> dict:
    """在 `cwd` 里执行白名单命令（无 shell），返回 {ok, rc, out, cmd, why}。"""
    ok, why = safe_cmd_ok(cmd)
    if not ok:
        return {"ok": False, "rc": -2, "out": "", "cmd": cmd, "why": why}
    if not os.path.isdir(cwd):
        return {"ok": False, "rc": -2, "out": "", "cmd": cmd, "why": "工作目录不存在"}
    try:
        parts = shlex.split(cmd, posix=(os.name != "nt"))
    except ValueError as ex:
        return {"ok": False, "rc": -2, "out": "", "cmd": cmd, "why": str(ex)}
    exe = shutil.which(parts[0]) or parts[0]
    timeout = max(5, min(int(timeout), 600))
    try:
        p = subprocess.run(parts if exe == parts[0] else [exe] + parts[1:],
                           cwd=cwd, capture_output=True, timeout=timeout,
                           env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        raw = (p.stdout or b"") + (p.stderr or b"")
        out = raw.decode("utf-8", "replace")
        if len(out) > 6000:
            out = out[:3000] + "\n…（截断）…\n" + out[-3000:]
        return {"ok": p.returncode == 0, "rc": p.returncode, "out": out,
                "cmd": cmd, "why": ""}
    except subprocess.TimeoutExpired:
        return {"ok": False, "rc": -1, "out": "", "cmd": cmd,
                "why": "超时 %ds（已终止）" % timeout}
    except FileNotFoundError:
        return {"ok": False, "rc": -3, "out": "", "cmd": cmd,
                "why": "本机未安装 %s" % parts[0]}
    except Exception as ex:                                     # noqa: BLE001
        return {"ok": False, "rc": -3, "out": "", "cmd": cmd, "why": str(ex)[:200]}


# ----------------------------------------------------------------------------
# ② 工具调用协议（文本协议，弱模型也能稳定产出；兼容既有 ===FILE:=== 写法）
# ----------------------------------------------------------------------------
_TOOL_RE = re.compile(
    r"<<\s*TOOL\s*:\s*(?P<name>write|read|list|run|done)\s*>>"
    r"(?P<arg>[^\n<]*)"
    r"(?:\n(?P<body>.*?))?"
    r"<<\s*END\s*>>", re.S | re.I)
_FILE_RE = re.compile(r"===\s*FILE\s*:\s*(?P<path>[^=\n]+?)\s*===\s*\n"
                      r"(?P<body>.*?)"
                      r"\n?\s*===\s*END\s*===", re.S)


def parse_tools(text: str) -> list:
    """解析模型回复里的工具调用（含 ===FILE:=== 兼容）→ [{tool, arg, body}]。

    ★ 按**出现位置**排序：执行顺序必须与模型写的一致（"先写文件再跑命令"不能被
      打乱成"先跑再写"）—— 旧写法先收完 <<TOOL:>> 再收 ===FILE:===，会颠倒顺序。
    """
    found = []
    for m in _TOOL_RE.finditer(text or ""):
        found.append((m.start(), {"tool": m.group("name").lower(),
                                  "arg": (m.group("arg") or "").strip(),
                                  "body": m.group("body") or ""}))
    for m in _FILE_RE.finditer(text or ""):
        found.append((m.start(), {"tool": "write", "arg": m.group("path").strip(),
                                  "body": m.group("body")}))
    found.sort(key=lambda x: x[0])
    return [c for _p, c in found]


def apply_tools(pdir: str, calls: list, allow_run: bool = True,
                run_timeout: int = 300) -> list:
    """执行工具调用，返回 [{tool, arg, ok, note}]（**写文件/跑命令都真做**）。"""
    results = []
    for c in calls or []:
        tool, arg, body = c.get("tool"), c.get("arg"), c.get("body") or ""
        if tool == "write":
            full = _inside(pdir, arg)
            if not full:
                results.append({"tool": tool, "arg": arg, "ok": False,
                                "note": "路径越界/非法，已拒"})
                continue
            try:
                body = body.lstrip("\n")
                _clean = ""
                if AT is not None:
                    # ★ 0.31.19：模型常把整份文件包进 ```java … ```（或被要求
                    #   "解释一下"后混入说明行），落盘前统一净化，否则用户磁盘上
                    #   就是"首行 ```java、末行 ```"的废文件（真机 17:09 产物 24/26）。
                    try:
                        _b2, _note = AT.sanitize_file(arg, body)
                        if _note:
                            body, _clean = _b2, "；" + _note
                    except Exception:                            # noqa: BLE001
                        pass
                os.makedirs(os.path.dirname(full) or pdir, exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(body)
                results.append({"tool": tool, "arg": arg, "ok": True,
                                "note": "写入 %d 字%s" % (len(body), _clean)})
            except Exception as ex:                             # noqa: BLE001
                results.append({"tool": tool, "arg": arg, "ok": False,
                                "note": "写入失败：%s" % ex})
        elif tool == "read":
            full = _inside(pdir, arg)
            if not full or not os.path.isfile(full):
                results.append({"tool": tool, "arg": arg, "ok": False,
                                "note": "文件不存在或越界"})
                continue
            try:
                txt = open(full, encoding="utf-8", errors="replace").read()
                results.append({"tool": tool, "arg": arg, "ok": True,
                                "note": txt[:4000]})
            except Exception as ex:                             # noqa: BLE001
                results.append({"tool": tool, "arg": arg, "ok": False,
                                "note": str(ex)[:150]})
        elif tool == "list":
            try:
                rows = []
                for root, dirs, fs in os.walk(pdir):
                    dirs[:] = [d for d in dirs
                               if d not in (".git", "__pycache__", "node_modules")]
                    for f in fs:
                        rows.append(os.path.relpath(os.path.join(root, f), pdir))
                results.append({"tool": tool, "arg": arg, "ok": True,
                                "note": "\n".join(sorted(rows)[:60])})
            except Exception as ex:                             # noqa: BLE001
                results.append({"tool": tool, "arg": arg, "ok": False,
                                "note": str(ex)[:150]})
        elif tool == "run":
            if not allow_run:
                results.append({"tool": tool, "arg": arg, "ok": False,
                                "note": "本轮不允许执行命令"})
                continue
            r = run_cmd(arg, pdir, timeout=run_timeout)
            results.append({"tool": tool, "arg": arg, "ok": r["ok"],
                            "note": (r["out"] or r["why"])[:3000], "rc": r["rc"]})
        elif tool == "done":
            results.append({"tool": tool, "arg": arg, "ok": True, "note": arg})
        else:
            results.append({"tool": str(tool), "arg": arg, "ok": False,
                            "note": "未知工具"})
    return results


# ----------------------------------------------------------------------------
# ③ 任务分解：按栈把需求切成有序小步（每步 1~2 个文件 + 验证方式）
# ----------------------------------------------------------------------------
def stack_of(req: str) -> dict:
    r = req or ""
    return {
        "java": bool(re.search(r"spring\s?boot|spring boot|java|maven|gradle", r, re.I)),
        "vue": bool(re.search(r"vue|vue3", r, re.I)),
        "react": bool(re.search(r"react|antd", r, re.I)),
        "py": bool(re.search(r"python|flask|fastapi|django", r, re.I)),
        "node": bool(re.search(r"node|express|nest", r, re.I)),
    }


def decompose(req: str, name: str = "app") -> list:
    """需求 → 有序步骤 [{id, goal, files, verify}]。verify 为命令或 'static'。

    原则：**每步都小到弱模型能完成**（1~2 个文件），且每步都有可验证性。
    """
    st = stack_of(req)
    pkg = re.sub(r"[^a-z0-9]", "", (name or "app").lower())[:20] or "app"
    steps = []
    n = 0

    def add(goal, files, verify):
        nonlocal n
        n += 1
        steps.append({"id": n, "goal": goal, "files": files, "verify": verify})

    if st["java"]:
        add("后端数据模型（实体 + 仓储接口）",
            ["src/main/java/com/example/%s/entity/MainEntity.java" % pkg,
             "src/main/java/com/example/%s/repository/MainRepository.java" % pkg],
            "static")
        add("后端服务层（业务逻辑）",
            ["src/main/java/com/example/%s/service/MainService.java" % pkg], "static")
        add("后端 REST 接口（Controller，供前端调用）",
            ["src/main/java/com/example/%s/controller/MainController.java" % pkg],
            "mvn -q -DskipTests compile")
    if st["vue"] or st["react"]:
        add("前端 API 封装（统一请求）", ["frontend/src/api/index.js"], "static")
        add("前端主页面（列表 + 表单 + 调后端）",
            ["frontend/src/views/Dashboard.vue" if st["vue"]
             else "frontend/src/Dashboard.jsx"], "static")
    if st["py"]:
        add("后端路由与服务逻辑", ["routes.py", "models.py"], "static")
    if st["node"]:
        add("后端路由与服务逻辑", ["routes.js"], "static")
    if not steps:                                    # 什么都没识别出来 → 给最小可验证步
        add("核心脚本", ["main.py"], "static")
    add("运行说明与依赖清单核对", ["README.md"], "static")
    return steps


# ----------------------------------------------------------------------------
# ④ 每步验证：静态必做；有工具链就跑真构建
# ----------------------------------------------------------------------------
def _java_quick_check(path: str) -> str:
    """Java 单文件启发式校验（**不依赖 javac**，因为单文件 javac 会因缺依赖误报）。

    抓三类真凶（都来自"模型产出未净化 / 生成边界错位"）：
      ① 文件内残留 ```java 围栏 或 ===FILE:/===END=== 标记（0.31.19 / Report.vue 类）；
      ② 括号 {} () [] 未平衡（PlanService.java:13 这类语法错的最常见形态）；
      ③ 行首 `#` 注释（Python 注释被写进 .java 的典型污染）。
    返回 "" 表示通过，否则返回原因片段。"""
    try:
        s = open(path, encoding="utf-8", errors="replace").read()
    except Exception as ex:                                      # noqa: BLE001
        return "读不了：%s" % ex
    if re.search(r"^\s*```|```\s*$", s, re.M):
        return "残留 ``` 代码围栏（未净化）"
    if re.search(r"={3}\s*(FILE|END)\b[^\n]*?={3}", s):
        return "残留 ===FILE:/===END=== 边界标记"
    # 行首 # 注释（排除 URL/她# 在字符串里的情况：仅查独立成行的 #...）
    if re.search(r"^\s*#[^\s!]", s, re.M):
        return "行首 '#' 注释（疑似 Python 注释污染）"
    pairs = {")": "(", "]": "[", "}": "{"}
    opens = {"(": 0, "[": 0, "{": 0}
    close_map = {")": "(", "]": "[", "}": "{"}
    for ch in s:
        if ch in opens:
            opens[ch] += 1
        elif ch in close_map:
            opens[close_map[ch]] -= 1
    if opens["("] != 0 or opens["["] != 0 or opens["{"] != 0:
        return "括号未平衡（()=%d []=%d {}=%d）" % (opens["("], opens["["], opens["{"])
    return ""


def _static_check(path: str) -> tuple:
    """单文件静态校验（.py / .json / .yml / .java）。"""
    low = path.lower()
    try:
        if low.endswith(".py"):
            import py_compile
            import tempfile
            with open(path, encoding="utf-8", errors="replace") as f:
                src = f.read()
            with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                             encoding="utf-8") as t:
                t.write(src)
                tmp = t.name
            try:
                py_compile.compile(tmp, doraise=True)
            finally:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
        elif low.endswith(".json"):
            json.loads(open(path, encoding="utf-8", errors="replace").read())
        elif low.endswith((".yml", ".yaml")):
            _yaml_quick_check(path)
        elif low.endswith(".java"):
            jerr = _java_quick_check(path)
            if jerr:
                return False, "%s：%s" % (os.path.basename(path), jerr)
    except Exception as ex:                                     # noqa: BLE001
        return False, "%s：%s" % (os.path.basename(path), str(ex)[:200])
    return True, ""


def _yaml_quick_check(path: str) -> None:
    """YAML 轻量结构校验：禁止 Tab 缩进、冒号后缺值、明显缩进错位。"""
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f, 1):
            if "\t" in line[:-1]:
                raise ValueError("第 %d 行用了 Tab 缩进（YAML 不允许）" % i)
            stripped = line.lstrip()
            if stripped and not stripped.startswith(("#", "- ", "-", "{", "}", "[")):
                if ":" not in stripped:
                    # 非列表/注释/映射行但无冒号 → 结构可疑
                    if not stripped.endswith((":", ">", "|")):
                        raise ValueError("第 %d 行缺冒号：%s" % (i, stripped[:60]))


def _vue_sfc_check(path: str) -> str:
    """Vue 单文件组件结构校验：抓 Report.vue 类缺陷（<style> 未闭 + 边界标记残留）。"""
    try:
        s = open(path, encoding="utf-8", errors="replace").read()
    except Exception as ex:                                      # noqa: BLE001
        return "读不了：%s" % ex
    # 残留边界标记（生成器漏写 ===END=== 的典型后果，会把下一文件名写进本文件）
    if re.search(r"={3}\s*(FILE|END)\b[^\n]*?={3}", s):
        return "文件内残留 ===FILE:/===END=== 标记（生成边界错位）"
    # 三个顶层块必须有闭合标签（只检查确实开了的）
    for tag in ("template", "script", "style"):
        opens = len(re.findall(r"<%s[\s>]" % tag, s))
        closes = len(re.findall(r"</%s>" % tag, s))
        if opens and opens != closes:
            return "<%s> 标签未闭合（开 %d / 闭 %d）" % (tag, opens, closes)
    # script 块内花括号平衡
    m = re.search(r"<script[^>]*>(.*?)</script>", s, re.S)
    if m and m.group(1).count("{") != m.group(1).count("}"):
        return "script 块花括号未平衡"
    return ""


def _frontend_static_check(pdir: str) -> list:
    """前端静态校验：.vue SFC 标签平衡 + .js/.ts 用 node --check（有 node 才跑）。

    ★ 修 Report.vue 类事故：之前 node/vue 分支在 verify_project 里是 `pass`，
      前端构建错误（半截 <style> / 残留 ===FILE: 标记）从不验证，直接漏到用户手上。"""
    bad = []
    node = shutil.which("node")
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", "node_modules", "dist", "target")]
        for f in fs:
            low = f.lower()
            full = os.path.join(root, f)
            if low.endswith(".vue"):
                err = _vue_sfc_check(full)
                if err:
                    bad.append("%s：%s" % (f, err))
            elif node and low.endswith((".js", ".mjs", ".cjs", ".ts")):
                try:
                    r = subprocess.run([node, "--check", full], capture_output=True,
                                       text=True, timeout=30)
                    if r.returncode != 0:
                        bad.append("%s：%s" % (f, (r.stderr or r.stdout or "").strip()[:300]))
                except Exception:                                 # noqa: BLE001
                    pass
    return bad


def _decode_mvn_error(msg: str) -> str:
    """把 mvn 构建失败的原始 stderr 解码成**人话**：重点识别 JDK 版本不匹配。

    返回 "" 表示不是 JDK 问题（维持原样）；否则返回可直接给用户看的引导。"""
    low = (msg or "").lower()
    hit = any(k in low for k in (
        "invalid target release", "unsupported class file major version",
        "source option", "release version", "class file version",
        "requires java", "no longer supported", "fatal error compiling",
        "java 8", "java 17", "java 21"))
    if not hit:
        return ""
    try:
        from pasm.cognitive.coder import probe_jdk
        jdk = probe_jdk()
    except Exception:                                       # noqa: BLE001
        jdk = {"major": None}
    major = jdk.get("major")
    if isinstance(major, int) and major < 17:
        return ("项目目标 Java 17+（Spring Boot 3），但本机 JDK 为 %d。"
                "请安装 JDK 17 后重试，或在需求里写明「生成兼容 JDK8 的 "
                "Spring Boot 2.7 版本」让我自适应。" % major)
    return ("疑似 JDK 版本不匹配（项目需 Java 17+）。请核对 JAVA_HOME 与构建用 JDK 版本。")


def verify_project(pdir: str, req: str = "") -> dict:
    """项目级验证：先静态查所有代码文件（**并行**），再按栈跑真构建（有工具链才跑）。"""
    paths = []
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", "node_modules", "dist", "target")]
        for f in fs:
            if f.lower().endswith((".py", ".json")):
                paths.append(os.path.join(root, f))
    bad = []
    if TR:
        with TR.span("verify", files=len(paths), pdir=pdir[-40:]):
            bad = _static_checks(paths)
    else:
        bad = _static_checks(paths)
    ran = None
    st = stack_of(req)
    if not bad:
        if st["java"] and (shutil.which("mvn") or os.path.isfile(os.path.join(pdir, "mvnw"))):
            cmd = "mvnw -q -DskipTests compile" if os.path.isfile(
                os.path.join(pdir, "mvnw")) else "mvn -q -DskipTests compile"
            ran = run_cmd(cmd, pdir, timeout=420)
            if not ran["ok"]:
                _raw = (ran["out"] or ran["why"] or "")[:1500]
                _decoded = _decode_mvn_error(_raw)
                bad.append("构建失败：" + (_decoded or _raw))
        elif os.path.isfile(os.path.join(pdir, "package.json")) \
                and shutil.which("node"):
            # ★ 0.31.21：前端不再 `pass`。有 node 就真做静态校验（SFC 标签平衡 +
            #   node --check），抓住 Report.vue 类半截 <style> / 残留边界标记，
            #   不让构建错误漏到用户手上。缺 node_modules 不强制装依赖，只做语法层。
            fe = _frontend_static_check(pdir)
            if fe:
                bad.extend(fe)
    if bad:
        # ★ 0.31.21：verify 失败要流入健康监控（之前 selfheal 永远显示 0 错误）。
        try:
            import selfheal as HEAL
            HEAL.record_build_issue("error", pdir, (bad[0] or "")[:400])
        except Exception:                                          # noqa: BLE001
            pass
    return {"ok": not bad, "errors": bad, "build": ran}


def _static_checks(paths: list) -> list:
    """静态检查：文件多时**并行**（只读、无副作用）。返回 ["文件名：原因", …]。"""
    if not paths:
        return []
    if SA and len(paths) >= 4:
        bad = SA.parallel_static_check(paths, _static_check, max_workers=4, timeout=90.0)
        return ["%s：%s" % (os.path.basename(p), w) for p, w in bad]
    out = []
    for p in paths:
        ok, why = _static_check(p)
        if not ok:
            out.append(why)
    return out


# ----------------------------------------------------------------------------
# ① + ④ 工具调用循环 / 每步验证与失败重试
# ----------------------------------------------------------------------------
def _tree(pdir: str, limit: int = 40) -> str:
    rows = []
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs
                   if d not in (".git", "__pycache__", "node_modules", "dist", "target")]
        for f in fs:
            rows.append(os.path.relpath(os.path.join(root, f), pdir).replace("\\", "/"))
            if len(rows) >= limit:
                return "\n".join(sorted(rows))
    return "\n".join(sorted(rows))


def _hints_of(req: str) -> str:
    """把「本机已知坑」（failbook）拼成提示块 —— 这就是"会成长"的最小闭环。"""
    if not FB:
        return ""
    try:
        return FB.hints(req or "", max_chars=420)
    except Exception:                                            # noqa: BLE001
        return ""


def _record_failure(step: dict, errors: list, req: str) -> str:
    """失败归因 + 落进失败册，返回归出的类别（供调用方回显）。"""
    if not FB:
        return ""
    try:
        cat = FB.classify(errors)
        FB.record(cat, (errors[-1] if errors else "未产出")[:200],
                  tool="devloop.run_step", task="dev",
                  fix=("缩小单步范围 / 检查工具链 / 检查路径" if cat != "model_empty"
                       else "一次只产 1 个文件，并明确给出 ===FILE:=== 格式"))
        return cat
    except Exception:                                            # noqa: BLE001
        return ""


def run_step(call_llm, pdir: str, step: dict, *, req: str = "", rounds: int = 2,
             allow_run: bool = True, log=None, budget_s: int = 240,
             ctx: str = "", should_cancel=None) -> dict:
    """跑**一个**步骤：模型产出 → 落盘 → 验证 → 失败带错误重试 → 仍失败则标 TODO。

    ★ `req`：**原始需求**必须传进来。旧写法拿 `step["goal"]`（如"后端数据模型"）
      去判技术栈 —— 判不出 java/vue → `verify_project` 只做静态检查 →
      "写→跑→读错→改"的构建闭环**永远不会触发**（静默失效）。
    ★ `ctx`：项目地图 + 相关文件片段（`repomap.context_for`）——先让模型"看得见项目"。
    ★ `should_cancel`：外部取消钩子（长任务必须能被叫停）。
    """
    t0 = time.time()
    wrote, errs, rounds_used = [], [], 0
    last_verify = {"ok": True, "errors": []}
    for r in range(1, max(1, rounds) + 1):
        if should_cancel and should_cancel():
            errs.append("已取消")
            break
        if time.time() - t0 > budget_s:
            errs.append("步骤时间预算用尽（%ds）" % budget_s)
            break
        rounds_used = r
        hint = ""
        if errs:
            hint = ("\n\n⚠️ 上一轮验证没过，请**只修**下面的错误（不要重写整个项目）：\n"
                    + "\n".join(errs[-3:])[:1200])
        prompt = ("你在一个已搭好骨架的项目里干活，**不要重写已有文件**（除非下面是修错）。\n"
                  "项目目录：%s\n当前文件：\n%s\n\n%s\n"
                  "本次只做这一步：%s\n需要产出的文件：%s\n%s\n\n"
                  "输出格式（每个文件一段，不要任何解释文字）：\n"
                  "===FILE: 相对路径===\n（文件完整内容）\n===END===\n"
                  "（如需执行命令核对，可用 <<TOOL:run>> 命令 <<END>>；"
                  "全部完成用 <<TOOL:done>> 说明 <<END>>）"
                  % (pdir, _tree(pdir), (ctx.strip() + "\n") if ctx else "",
                     step.get("goal"), "、".join(step.get("files") or []), hint))
        try:
            txt = call_llm(prompt) or ""
        except Exception as ex:                                 # noqa: BLE001
            errs.append("模型调用失败：%s" % str(ex)[:160])
            break
        calls = parse_tools(txt)
        if not calls:
            errs.append("模型没有给出可落盘内容（第 %d 轮）" % r)
            if log:
                log("步骤 %s 第 %d 轮无产出" % (step.get("id"), r))
            break
        res = apply_tools(pdir, calls, allow_run=allow_run)
        wrote += [x["arg"] for x in res if x["tool"] == "write" and x["ok"]]
        for x in res:
            if x["tool"] in ("write", "run") and not x["ok"]:
                errs.append("%s %s：%s" % (x["tool"], x["arg"], x["note"][:200]))
        # —— 本步验证（用**原始需求**判栈，否则构建闭环静默失效）——
        last_verify = verify_project(pdir, req or step.get("goal") or "")
        if last_verify["ok"]:
            if log:
                log("步骤 %s 通过验证" % step.get("id"))
            return {"ok": True, "step": step, "wrote": wrote, "errors": [],
                    "rounds": rounds_used, "verify": last_verify}
        errs += [e for e in last_verify["errors"] if e not in errs]
        if log:
            log("步骤 %s 验证未过（第 %d 轮），带错误重试" % (step.get("id"), r))
    _cat = _record_failure(step, errs, req)
    return {"ok": False, "step": step, "wrote": wrote, "errors": errs[-4:],
            "rounds": rounds_used, "verify": last_verify, "fail_cat": _cat}


#: 断点续做状态文件名（P2-8）——写在**项目目录内**，跟着项目走
STATE_NAME = ".pasm_state.json"


def load_state(pdir: str) -> dict:
    """读断点状态（第几步做完、TODO 有哪些）。缺/坏 → 空状态（绝不抛）。"""
    p = os.path.join(pdir, STATE_NAME)
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:                                            # noqa: BLE001
        return {}


def save_state(pdir: str, state: dict) -> None:
    """写断点状态（原子替换，避免写一半崩溃留下坏文件）。"""
    p = os.path.join(pdir, STATE_NAME)
    try:
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
    except Exception:                                            # noqa: BLE001
        pass


def clear_state(pdir: str) -> None:
    try:
        os.remove(os.path.join(pdir, STATE_NAME))
    except OSError:
        pass


def agent_loop(call_llm, pdir: str, task: str, *, name: str = "app",
               max_steps: int = 6, rounds_per_step: int = 2,
               allow_run: bool = True, log=None, budget_s: int = 900,
               resume: bool = True, should_cancel=None,
               ctx_budget: int = 12000) -> dict:
    """**真工具调用循环**：分解 → 逐步执行（含验证与重试）→ 汇总如实报告。

    返回 {ok, steps:[run_step 结果], wrote, todo, report, resumed, ctx_chars}
      · `resume`（P2-8）：项目目录里的 `.pasm_state.json` 记着"哪些步骤已完成"，
        蓝屏/重启后重跑会**跳过已完成步骤**，从下一步接着做；
      · `should_cancel`：外部取消钩子（长任务可被叫停，状态照实存）；
      · `ctx`：每步现取「项目地图 + 相关文件」喂给模型（文件在变，地图不能一次性算完）。
    """
    t0 = time.time()
    steps = decompose(task, name)[:max_steps]
    st_path = os.path.join(pdir, STATE_NAME)
    state = load_state(pdir) if resume else {}
    done_ids = set(state.get("done") or [])
    resumed = bool(done_ids) and os.path.isfile(st_path)
    results, wrote, todo = [], [], []
    for stp in steps:
        sid = str(stp.get("id"))
        if sid in done_ids:
            results.append({"ok": True, "step": stp, "wrote": [], "errors": [],
                            "rounds": 0, "skipped": True, "verify": {"ok": True}})
            continue
        if should_cancel and should_cancel():
            todo.append("步骤 %s（已取消）" % sid)
            break
        if time.time() - t0 > budget_s:
            todo.append("步骤 %s（总预算用尽）" % sid)
            continue
        ctx = ""
        if RM:
            try:
                ctx = RM.context_for(pdir, "%s %s" % (task, stp.get("goal") or ""),
                                     budget_chars=ctx_budget)
            except Exception:                                    # noqa: BLE001
                ctx = ""
        if TR:
            with TR.span("step", step=sid, goal=(stp.get("goal") or "")[:30],
                         ctx_chars=len(ctx)):
                r = run_step(call_llm, pdir, stp, req=task, rounds=rounds_per_step,
                             allow_run=allow_run, log=log, ctx=ctx,
                             should_cancel=should_cancel)
        else:
            r = run_step(call_llm, pdir, stp, req=task, rounds=rounds_per_step,
                         allow_run=allow_run, log=log, ctx=ctx,
                         should_cancel=should_cancel)
        results.append(r)
        wrote += r.get("wrote") or []
        if r.get("ok"):
            done_ids.add(sid)
            save_state(pdir, {"done": sorted(done_ids, key=lambda x: (len(x), x)),
                              "task": task[:300], "t": time.strftime("%Y-%m-%d %H:%M:%S")})
        else:
            todo.append("步骤 %s「%s」：%s"
                        % (sid, stp.get("goal"),
                           (r.get("errors") or ["未产出"])[-1][:120]))
    ok = all(r.get("ok") for r in results) and bool(results)
    rep = ["工具调用循环完成：%d/%d 步通过验证%s"
           % (sum(1 for r in results if r.get("ok")), len(results),
              "（本次为**续做**，已跳过 %d 个完成步骤）" % len(done_ids) if resumed else "")]
    if wrote:
        rep.append("本次写入 %d 个文件" % len(list(dict.fromkeys(wrote))))
    if todo:
        rep.append("**这些步骤没能完成（如实列出，不装成功）**：\n- " + "\n- ".join(todo))
    return {"ok": ok, "steps": results, "wrote": list(dict.fromkeys(wrote)), "todo": todo,
            "report": "\n".join(rep), "resumed": resumed,
            "done_steps": sorted(done_ids, key=lambda x: (len(x), x))}


def build_fix_loop(call_llm, pdir: str, cmd: str, *, rounds: int = 2,
                   log=None, timeout: int = 420) -> dict:
    """**写→跑→读错→改 闭环**：跑命令 → 出错把 stderr 原样回灌 → 模型只修错 → 再跑。"""
    runs = []
    for i in range(max(1, rounds) + 1):
        r = run_cmd(cmd, pdir, timeout=timeout)
        runs.append({"cmd": cmd, "rc": r["rc"], "ok": r["ok"], "out": (r["out"] or r["why"])[:3000]})
        if log:
            log("第 %d 次执行 %s → rc=%s" % (i + 1, cmd, r["rc"]))
        if r["ok"] or i == max(1, rounds):
            break
        prompt = ("刚才在你的项目里执行 `%s`，**失败了**。下面是它输出的关键报错：\n\n"
                  "```\n%s\n```\n\n"
                  "项目当前文件：\n%s\n\n"
                  "请**只输出需要改动的文件**的完整新内容（修掉这个报错），格式：\n"
                  "===FILE: 相对路径===\n（文件完整内容）\n===END===\n"
                  "不要解释，不要重写未涉及的文件。"
                  % (cmd, runs[-1]["out"][:2500], _tree(pdir)))
        try:
            txt = call_llm(prompt) or ""
        except Exception as ex:                                 # noqa: BLE001
            runs.append({"cmd": cmd, "rc": -9, "ok": False, "out": "模型调用失败：%s" % ex})
            break
        calls = parse_tools(txt)
        if not calls:
            if log:
                log("修复轮模型没给出文件，停止闭环")
            break
        apply_tools(pdir, [c for c in calls if c["tool"] in ("write", "read", "list")],
                    allow_run=False)
    last = runs[-1] if runs else {"ok": False, "out": "未执行"}
    return {"ok": bool(last.get("ok")), "runs": runs,
            "report": ("构建/运行闭环：%s（共跑 %d 次）"
                       % ("通过" if last.get("ok") else "仍有报错，已如实记录",
                          len(runs)))}


# ----------------------------------------------------------------------------
# 自检
# ----------------------------------------------------------------------------
def build_cmd_for(pdir: str, req: str = "") -> str:
    """按栈给出**可执行的构建命令**；前置条件不满足时返回空串（宁可不跑，不卡界面）。

    前置条件为什么要判：
      · Maven 首次编译会联网拉全部依赖（几百 MB、几分钟）→ 只有本机已有
        `~/.m2/repository` 缓存时才跑，避免"看起来死机"。
      · 前端只有 `node_modules` 存在时才跑 `npm run build`（否则先装依赖就是大坑）。
    """
    st = stack_of(req)
    if st["java"]:
        if os.path.isfile(os.path.join(pdir, "mvnw")) and not os.path.isdir(
                os.path.join(os.path.expanduser("~"), ".m2", "repository")):
            return ""                    # 无 maven 缓存：不跑（会拉全网依赖）
        if shutil.which("mvn") and os.path.isdir(
                os.path.join(os.path.expanduser("~"), ".m2", "repository")):
            return "mvn -q -DskipTests compile"
        if os.path.isfile(os.path.join(pdir, "mvnw")) and os.path.isdir(
                os.path.join(os.path.expanduser("~"), ".m2", "repository")):
            return "mvnw -q -DskipTests compile"
    if (st["vue"] or st["react"] or st["node"]) and os.path.isdir(
            os.path.join(pdir, "frontend", "node_modules")):
        return "npm --prefix frontend run build" if os.path.isfile(
            os.path.join(pdir, "frontend", "package.json")) else ""
    if st["py"] and shutil.which("python"):
        return "python -m py_compile app.py routes.py" if os.path.isfile(
            os.path.join(pdir, "app.py")) else ""
    return ""


def selftest() -> int:
    import tempfile
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
            print("  [OK] %s" % name)
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    print("=== ① 命令白名单与注入防护 ===")
    for bad in ("rm -rf /", "mvn clean; rm -rf .", "npm install && curl x",
                "python -c \"import os; os.system('x')\" > out.txt",
                "powershell -c ls", "tar -xzf a.tgz | sh"):
        ck("拒绝：%s" % bad[:34], not safe_cmd_ok(bad)[0], safe_cmd_ok(bad)[1])
    for good in ("mvn -q -DskipTests compile", "npm install --no-audit", "python -m py_compile a.py"):
        ck("放行：%s" % good[:34], safe_cmd_ok(good)[0], safe_cmd_ok(good)[1])

    print("\n=== ② 路径越界防护 ===")
    tmp = tempfile.mkdtemp(prefix="devloop_")
    ck("拒盘符", not _inside(tmp, "D:/x/a.py"))
    ck("拒 ..", not _inside(tmp, "../../evil.py"))
    ck("拒绝对路径", not _inside(tmp, "/etc/passwd"))
    ck("收受正常相对路径", bool(_inside(tmp, "src/a.py")))

    print("\n=== ③ 工具协议解析（含 ===FILE:=== 兼容）===")
    txt = ("<<TOOL:write>> a/b.py\nprint(1)\n<<END>>\n"
           "===FILE: c.txt===\nhello\n===END===\n"
           "<<TOOL:run>> python -c pass <<END>>\n<<TOOL:done>> ok <<END>>")
    calls = parse_tools(txt)
    ck("解析出 4 个调用", len(calls) == 4, str([c["tool"] for c in calls]))
    ck("write 的 body 正确", calls[0]["body"].strip() == "print(1)", repr(calls[0]["body"]))
    ck("===FILE:=== 被当作 write", calls[1]["tool"] == "write" and calls[1]["arg"] == "c.txt")

    print("\n=== ④ 工具真执行（写/读/列表）===")
    res = apply_tools(tmp, parse_tools("<<TOOL:write>> x/y.py\nprint(2)\n<<END>>"))
    ck("写文件成功", res[0]["ok"] and os.path.isfile(os.path.join(tmp, "x", "y.py")))
    res = apply_tools(tmp, [{"tool": "read", "arg": "x/y.py", "body": ""}])
    ck("读文件回内容", res[0]["ok"] and "print(2)" in res[0]["note"])
    res = apply_tools(tmp, [{"tool": "write", "arg": "../evil.py", "body": "x"}])
    ck("越界写被拒", not res[0]["ok"])

    print("\n=== ⑤ 任务分解：springboot-vue 有序且每步可验证 ===")
    steps = decompose("帮我用 springboot-vue 做前后端分离的 GEO 平台，要 api 和后台", "geo平台")
    ck("步数 ≥ 5", len(steps) >= 5, "steps=%d" % len(steps))
    ck("含 controller 步", any("controller" in f.lower() for s in steps for f in s["files"]))
    ck("含前端页面步", any("Dashboard" in f for s in steps for f in s["files"]))
    ck("后端编译步带真命令", any(s["verify"].startswith("mvn") for s in steps))
    ck("每步都有 files 与 verify",
       all(s["files"] and s["verify"] for s in steps))

    print("\n=== ⑥ 每步验证 + 失败重试（假模型：第一轮写坏，第二轮写对）===")
    proj = tempfile.mkdtemp(prefix="devloop_proj_")
    state = {"n": 0}

    def fake_llm_bad_then_good(prompt):
        state["n"] += 1
        if state["n"] == 1:
            return "===FILE: a.py===\ndef f(:\n  pass\n===END==="     # 语法错
        return "===FILE: a.py===\ndef f():\n    return 1\n===END==="

    r = run_step(fake_llm_bad_then_good, proj, {"id": 1, "goal": "写出 a.py",
                                                "files": ["a.py"], "verify": "static"},
                 rounds=2, allow_run=False)
    ck("重试后通过", r["ok"], str(r.get("errors")))
    ck("用了 2 轮", r["rounds"] == 2, "rounds=%d" % r["rounds"])

    print("\n=== ⑦ 完全失败的步骤：如实标记 TODO，不装成功 ===")
    proj2 = tempfile.mkdtemp(prefix="devloop_proj2_")
    out = agent_loop(lambda p: "我建议你先安装依赖", proj2, "用 python 写个接口",
                     name="t", max_steps=3, rounds_per_step=1, allow_run=False)
    ck("整体不 ok", not out["ok"])
    ck("TODO 里如实列出", bool(out["todo"]))
    ck("报告含'如实'字样", "如实" in out["report"], out["report"][:80])

    print("\n=== ⑧ 构建闭环：跑真命令 → 读错 → 修复 → 再跑 ===")
    proj3 = tempfile.mkdtemp(prefix="devloop_proj3_")
    open(os.path.join(proj3, "check.py"), "w", encoding="utf-8").write("raise SystemExit(3)\n")
    seq = {"n": 0}

    def fake_fix(prompt):
        seq["n"] += 1
        open(os.path.join(proj3, "check.py"), "w", encoding="utf-8").write("raise SystemExit(0)\n")
        return "===FILE: check.py===\nraise SystemExit(0)\n===END==="

    bf = build_fix_loop(fake_fix, proj3, "python check.py", rounds=2, timeout=30)
    ck("闭环最终通过", bf["ok"], str(bf["runs"])[-200:])
    ck("至少跑了 2 次", len(bf["runs"]) >= 2, "runs=%d" % len(bf["runs"]))
    ck("真的读到了错误（rc 非 0 记录在案）",
       any(x["rc"] not in (0, None) for x in bf["runs"]))

    print("\n=== ⑨ 项目级验证：坏 json 必被抓住 ===")
    proj4 = tempfile.mkdtemp(prefix="devloop_proj4_")
    open(os.path.join(proj4, "bad.json"), "w", encoding="utf-8").write("{'a':1,}")
    v = verify_project(proj4, "python 项目")
    ck("坏 json → 验证不过", not v["ok"], str(v["errors"])[:120])

    print("\n=== ⑩ 断点续做（P2-8）：第二次跑必须跳过已完成步骤 ===")
    import tempfile as _tf
    proj5 = _tf.mkdtemp(prefix="devloop_resume_")
    calls = {"n": 0}

    def fake_ok(prompt):
        calls["n"] += 1
        m = __import__("re").search(r"需要产出的文件：([^\n]+)", prompt or "")
        rel = (m.group(1).split("、")[0].strip() if m else "a.py")
        return "===FILE: %s===\nprint(1)\n===END===" % rel

    r1 = agent_loop(fake_ok, proj5, "用 python 写个接口", name="t", max_steps=2,
                    rounds_per_step=1, allow_run=False)
    n1 = calls["n"]
    ck("首跑有产出", bool(r1["wrote"]) and n1 > 0, (r1["wrote"], n1))
    ck("状态文件已落盘", os.path.isfile(os.path.join(proj5, STATE_NAME)))
    ck("报告里记下完成步数", bool(r1.get("done_steps")), r1.get("done_steps"))
    calls["n"] = 0
    r2 = agent_loop(fake_ok, proj5, "用 python 写个接口", name="t", max_steps=2,
                    rounds_per_step=1, allow_run=False)
    ck("第二次跑的模型调用更少（跳过已完成）", calls["n"] < n1,
       "before=%d after=%d" % (n1, calls["n"]))
    ck("标注为续做", r2.get("resumed") is True or not r1.get("done_steps"), r2.get("resumed"))
    ck("报告里说明续做/跳过", ("续做" in r2["report"]) or (calls["n"] == 0), r2["report"][:80])

    print("\n=== ⑪ 取消钩子：要能叫停 ===")
    proj6 = _tf.mkdtemp(prefix="devloop_cancel_")
    seen = {"n": 0}

    def fake_cancel_llm(prompt):
        seen["n"] += 1
        m = __import__("re").search(r"需要产出的文件：([^\n]+)", prompt or "")
        rel = (m.group(1).split("、")[0].strip() if m else "a.py")
        return "===FILE: %s===\nprint(1)\n===END===" % rel

    r3 = agent_loop(fake_cancel_llm, proj6, "用 python 写个接口", name="t", max_steps=4,
                    rounds_per_step=1, allow_run=False, resume=False,
                    should_cancel=lambda: seen["n"] >= 1)     # 第一步做完就"被取消"
    ck("取消生效：剩余步骤进 todo（不装作跑完）", bool(r3.get("todo")), r3.get("todo"))
    ck("已完成的步骤仍如实保留", len(r3["steps"]) >= 1, len(r3["steps"]))

    print("\n=== 小结 ===")
    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
