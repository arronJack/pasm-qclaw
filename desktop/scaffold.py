# -*- coding: utf-8 -*-
"""scaffold.py —— 真项目脚手架（v0.30.11，#2-A「真装框架」）

把「写代码 / 做个网站 / 开发一个后台」从「**生成一堆文件**」升级为
「**真装框架 + 真装依赖 + 真落盘可运行**」。

## 为什么单独一个模块

`agent_tools.save_project()` 只负责把模型给的 `{路径: 内容}` 写进
`projects/<名字>/` —— 它**从不装任何依赖**，所以产出永远是「通用模板」：
`package.json` 里写着 `vue`，`node_modules` 却不存在，`npm run dev` 一定报错。
本模块补的就是这一步。

## 三条诚实边界（与 `media_job` / `executors` 同一套底线）

1. **计划与执行分离**：先出命令清单（每条都写清楚"这条命令要干什么"），
   再逐条真跑。不搞「一键跑完」——用户要的是分步骤、看得见。
2. **跑就留档**：每条命令的真实 argv / returncode / 输出尾部都记进结果里。
3. **装不上就说装不上**：缺 node/npm/python、或没网导致装失败时，
   **落盘离线骨架 + `ok=False` + 写明缺什么**，绝不假装成功。
   （只在"依赖确实装好了"或"这个模板本来就不需要依赖"时才 `ok=True`。）

## 安全边界

* 命令一律**固定模板 + argv 列表**，`shell=False`；用户原话**绝不拼进命令**
  （只用于 `detect()` 挑模板，以及清洗成目录名）。
* 落点固定在 `agent_tools.PROJECTS_DIR` 下，名字清洗掉路径分隔符，
  拒绝 `../` 越界。
* `stdin=DEVNULL`：npm 首次会问 "Ok to proceed?"，不给 stdin 就会**静默卡死**
  直到超时（真踩过）。同时 `npm_config_yes=1` 自动确认。

## Windows 真机坑（实测）

`shutil.which("npm")` 拿到的是 `...\npm.CMD`；若直接 `subprocess.run(["npm", ...])`
会 **FileNotFoundError: [WinError 2]**（裸命令名不是可执行文件）。
必须用 which() 解析出的**全路径**。与 `sysops._ps_exe()` 里 powershell 那条同类。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import time

#: Windows 下不弹黑框
_CREATE_NO_WINDOW = 0x08000000

#: 单条命令默认超时（秒）；装依赖很慢，单独放宽
T_CREATE = 180
T_INSTALL = 900


# ==========================================================================
# 运行器探测 / 命令执行
# ==========================================================================
def which(tool: str) -> str:
    """探测运行器，返回**全路径**或空串。

    必须回归全路径：Windows 下裸 "npm" 不可执行（见模块头）。
    """
    try:
        return shutil.which(tool) or ""
    except Exception:  # noqa: BLE001
        return ""


def _sp(argv, cwd=None, timeout=120, env=None):
    """跑一条命令，返回 (returncode, 合并输出文本)。异常不外抛。"""
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = _CREATE_NO_WINDOW
    e = dict(os.environ)
    # 关掉 npm 的交互与彩噪：否则首次会问 "Ok to proceed?" 卡到超时
    e.update({"NO_COLOR": "1", "npm_config_yes": "1",
              "npm_config_fund": "false", "npm_config_audit": "false"})
    if env:
        e.update(env)
    try:
        p = subprocess.run(list(argv), cwd=cwd, timeout=timeout,
                           capture_output=True, encoding="utf-8",
                           errors="replace", stdin=subprocess.DEVNULL,
                           env=e, **kw)
    except subprocess.TimeoutExpired:
        return -1, "⏱ 超时（%ss）：%s" % (timeout, " ".join(argv))
    except FileNotFoundError as ex:
        return -2, "找不到可执行文件：%s（%s）" % (argv[0], ex)
    except Exception as ex:  # noqa: BLE001
        return -3, "%s: %s" % (type(ex).__name__, ex)
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode, out


def _tail(text: str, n: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= n else "…" + text[-n:]


def projects_dir() -> str:
    """项目分类目录（`<工作根>/project`）。

    v0.30.11：判断权**整体搬进 `workspace.root()` 一处** ——
    `PASM_WORK_ROOT` > `PASM_STUDIO_DIR` > `cfg.ws_dir` > 桌面\\PASM工作。
    这里不再自己判 env（曾因"各处各判一套"把测试工程写进过用户真实
    `projects/`；权威实现只能有一份，见 `pasm/cognitive/workspace.py`）。
    走 `agent_tools` 转发是为了让"用户在设置里选的工作根"也生效。
    """
    try:
        import agent_tools as AT
        return AT.projects_dir()
    except Exception:  # noqa: BLE001
        import workspace as WS
        return WS.cat_dir("project")


def proj_base(root: str = "") -> str:
    """`<工作根>/project`（**不建目录**）。

    解析权必须交给唯一权威 `workspace.root()`：把 root 当 `cfg.ws_dir` 传进去，
    让 `PASM_WORK_ROOT` > `PASM_STUDIO_DIR` > `cfg.ws_dir` 的优先级照常生效。
    在本地直接拼 `root/project` 会**绕过隔离** —— 测试/CI 设了 `PASM_WORK_ROOT`
    时工程会落到隔离区外面（2026-09-17 真被测出来：工具总线走 `cfg.ws_dir`
    自己拼路径，与 `projects_dir()` 给出两个不同的落点）。
    """
    if not root:
        return projects_dir()          # 走 agent_tools：认得设置里选的工作根
    try:
        import workspace as WS
        return os.path.join(WS.root({"ws_dir": root}), WS.cat_of("project"))
    except Exception:                                  # noqa: BLE001
        return os.path.join(root, "project")


def safe_name(name: str, fallback: str = "project") -> str:
    """目录名清洗：去掉路径分隔符与 ..，杜绝越界写法。"""
    s = re.sub(r"[^\w\- ]", "_", (name or "").strip())[:32].strip()
    s = s.replace("..", "_").strip(" ._-")
    return s or fallback


# ==========================================================================
# 模板
# ==========================================================================
#: 每个模板：
#:   label   中文名（进计划清单）
#:   mode    "cli"（官方脚手架建工程）| "files"（我们直接落盘骨架）
#:   needs   需要的运行器（缺了就走离线回退）
#:   steps   命令步骤：{desc, argv, cwd, timeout}
#:   files   离线骨架 {相对路径: 内容}
#:   check   完成后必须存在的文件（做不到就是没成功）
#:   hint    怎么跑起来
TEMPLATES = {
    "static": {
        "label": "纯静态站点（零依赖，永远可跑）",
        "mode": "files",
        "needs": (),
        "steps": [],
        "files": {
            "index.html": """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{name}</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header><h1>{name}</h1><p>由 PASM Studio 生成 · 右键用浏览器打开即可</p></header>
  <main id="app"></main>
  <script src="app.js"></script>
</body>
</html>
""",
            "style.css": """* { box-sizing: border-box; }
body { margin: 0; font: 16px/1.7 system-ui, "Microsoft YaHei", sans-serif;
       color: #1f2328; background: #f6f8fa; }
header { padding: 48px 24px; text-align: center; }
h1 { margin: 0 0 8px; font-size: 28px; }
main { max-width: 720px; margin: 0 auto; padding: 0 24px 64px; }
""",
            "app.js": """// 最小可跑逻辑：把当前时间渲染出来，验证 JS 真的在跑
document.getElementById("app").textContent =
  "页面已加载 · " + new Date().toLocaleString("zh-CN");
""",
        },
        "check": ("index.html",),
        "hint": "直接双击 index.html，或 python -m http.server 8000",
    },
    "vite-vue": {
        "label": "Vue 3 + Vite 前端",
        "mode": "cli",
        "needs": ("npm",),
        "steps": [
            {"desc": "用 Vite 官方脚手架创建工程", "timeout": T_CREATE,
             "argv": ["{npm}", "create", "vite@latest", "{name}", "--",
                      "--template", "vue"], "cwd": "{parent}"},
            {"desc": "安装依赖（真装 node_modules）", "timeout": T_INSTALL,
             "argv": ["{npm}", "install"], "cwd": "{dir}"},
        ],
        "files": {
            "package.json": """{
  "name": "{slug}",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },
  "dependencies": { "vue": "^3.4.0" },
  "devDependencies": { "vite": "^5.0.0", "@vitejs/plugin-vue": "^5.0.0" }
}
""",
            "vite.config.js": """import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({ plugins: [vue()] });
""",
            "index.html": """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{name}</title></head>
<body><div id="app"></div><script type="module" src="/src/main.js"></script></body>
</html>
""",
            "src/main.js": """import { createApp } from "vue";
import App from "./App.vue";
import "./style.css";

createApp(App).mount("#app");
""",
            "src/App.vue": """<script setup>
import { ref } from "vue";
const n = ref(0);
</script>

<template>
  <h1>{name}</h1>
  <button @click="n++">点了 {{ n }} 次</button>
</template>
""",
            "src/style.css": """body { font-family: system-ui, "Microsoft YaHei", sans-serif;
       display: grid; place-items: center; min-height: 100vh; margin: 0; }
""",
        },
        "check": ("package.json",),
        "hint": "npm run dev",
    },
    "vite-react": {
        "label": "React + Vite 前端",
        "mode": "cli",
        "needs": ("npm",),
        "steps": [
            {"desc": "用 Vite 官方脚手架创建工程", "timeout": T_CREATE,
             "argv": ["{npm}", "create", "vite@latest", "{name}", "--",
                      "--template", "react"], "cwd": "{parent}"},
            {"desc": "安装依赖（真装 node_modules）", "timeout": T_INSTALL,
             "argv": ["{npm}", "install"], "cwd": "{dir}"},
        ],
        "files": {
            "package.json": """{
  "name": "{slug}",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": { "dev": "vite", "build": "vite build", "preview": "vite preview" },
  "dependencies": { "react": "^18.2.0", "react-dom": "^18.2.0" },
  "devDependencies": { "vite": "^5.0.0", "@vitejs/plugin-react": "^4.2.0" }
}
""",
            "vite.config.js": """import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({ plugins: [react()] });
""",
            "index.html": """<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>{name}</title></head>
<body><div id="root"></div><script type="module" src="/src/main.jsx"></script></body>
</html>
""",
            "src/main.jsx": """import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App.jsx";

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
""",
            "src/App.jsx": """import { useState } from "react";

export default function App() {
  const [n, setN] = useState(0);
  return (
    <main>
      <h1>{name}</h1>
      <button onClick={() => setN(n + 1)}>点了 {n} 次</button>
    </main>
  );
}
""",
        },
        "check": ("package.json",),
        "hint": "npm run dev",
    },
    "node-express": {
        "label": "Node.js + Express 后端 API",
        "mode": "files",
        "needs": ("npm",),
        "steps": [
            {"desc": "安装依赖（express 真装进 node_modules）", "timeout": T_INSTALL,
             "argv": ["{npm}", "install", "express", "cors"], "cwd": "{dir}"},
        ],
        "files": {
            "package.json": """{
  "name": "{slug}",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": { "start": "node server.js" },
  "dependencies": { "express": "^4.19.0", "cors": "^2.8.5" }
}
""",
            "server.js": """import express from "express";
import cors from "cors";

const app = express();
app.use(cors());
app.use(express.json());

const items = [{ id: 1, name: "示例条目" }];

app.get("/api/health", (req, res) => res.json({ ok: true, ts: Date.now() }));
app.get("/api/items", (req, res) => res.json(items));
app.post("/api/items", (req, res) => {
  const it = { id: items.length + 1, name: req.body?.name || "未命名" };
  items.push(it);
  res.status(201).json(it);
});

app.listen(3000, () => console.log("http://127.0.0.1:3000/api/health"));
""",
        },
        "check": ("package.json", "server.js"),
        "hint": "npm start → http://127.0.0.1:3000/api/health",
    },
    "fastapi": {
        "label": "Python + FastAPI 后端",
        "mode": "files",
        "needs": ("python",),
        "steps": [
            {"desc": "建独立虚拟环境 .venv", "timeout": T_CREATE,
             "argv": ["{python}", "-m", "venv", ".venv"], "cwd": "{dir}"},
            {"desc": "装依赖（fastapi / uvicorn 真装进 .venv）", "timeout": T_INSTALL,
             "argv": ["{venvpy}", "-m", "pip", "install", "--disable-pip-version-check",
                      "-q", "fastapi", "uvicorn[standard]"], "cwd": "{dir}"},
        ],
        "files": {
            "requirements.txt": "fastapi\nuvicorn[standard]\n",
            "main.py": '''# -*- coding: utf-8 -*-
"""{name} —— FastAPI 服务骨架。"""
from fastapi import FastAPI

app = FastAPI(title="{name}")


@app.get("/api/health")
def health():
    return {{"ok": True}}


@app.get("/api/items")
def items():
    return [{{"id": 1, "name": "示例条目"}}]
''',
            "run.bat": "@echo off\r\n.venv\\Scripts\\python.exe -m uvicorn main:app --reload\r\n",
            "README.md": "# {name}\n\n```\n.venv\\Scripts\\python.exe -m uvicorn main:app --reload\n```\n\n文档：http://127.0.0.1:8000/docs\n",
        },
        "check": ("main.py", "requirements.txt"),
        "hint": ".venv\\Scripts\\python.exe -m uvicorn main:app --reload",
    },
    "flask": {
        "label": "Python + Flask 后端",
        "mode": "files",
        "needs": ("python",),
        "steps": [
            {"desc": "建独立虚拟环境 .venv", "timeout": T_CREATE,
             "argv": ["{python}", "-m", "venv", ".venv"], "cwd": "{dir}"},
            {"desc": "装依赖（flask 真装进 .venv）", "timeout": T_INSTALL,
             "argv": ["{venvpy}", "-m", "pip", "install", "--disable-pip-version-check",
                      "-q", "flask"], "cwd": "{dir}"},
        ],
        "files": {
            "requirements.txt": "flask\n",
            "app.py": '''# -*- coding: utf-8 -*-
"""{name} —— Flask 服务骨架。"""
from flask import Flask, jsonify

app = Flask(__name__)


@app.get("/api/health")
def health():
    return jsonify(ok=True)


@app.get("/api/items")
def items():
    return jsonify([{{"id": 1, "name": "示例条目"}}])


if __name__ == "__main__":
    app.run(debug=True, port=5000)
''',
            "README.md": "# {name}\n\n```\n.venv\\Scripts\\python.exe app.py\n```\n",
        },
        "check": ("app.py", "requirements.txt"),
        "hint": ".venv\\Scripts\\python.exe app.py",
    },
    "python-script": {
        "label": "Python 工具项目（venv + 入口脚本）",
        "mode": "files",
        "needs": ("python",),
        "steps": [
            {"desc": "建独立虚拟环境 .venv", "timeout": T_CREATE,
             "argv": ["{python}", "-m", "venv", ".venv"], "cwd": "{dir}"},
        ],
        "files": {
            "requirements.txt": "# 按需添加依赖，例如：requests\n",
            "main.py": '''# -*- coding: utf-8 -*-
"""{name} —— 入口脚本。"""


def main():
    print("hello from {name}")


if __name__ == "__main__":
    main()
''',
            "README.md": "# {name}\n\n```\n.venv\\Scripts\\python.exe main.py\n```\n",
        },
        "check": ("main.py",),
        "hint": ".venv\\Scripts\\python.exe main.py",
    },
}

#: 识别用的关键词（顺序 = 优先级，具体框架优先于泛化"网站"）
_HINTS = (
    ("fastapi", ("fastapi",)),
    ("flask", ("flask",)),
    ("node-express", ("express", "node 后端", "node后端", "node api", "node服务")),
    ("vite-react", ("react",)),
    ("vite-vue", ("vue", "vite")),
    ("static", ("静态网页", "静态网站", "静态页面", "纯前端页面")),
    ("python-script", ("python 脚本", "python脚本", "爬虫脚本", "批量处理脚本")),
)

#: 泛化"做网站/后台"的兜底（放在具体框架之后判断）
_WEB_WORDS = ("网站", "网页", "前端", "后台管理", "管理系统", "仪表盘",
              "dashboard", "入驻页", "官网", "落地页", "单页")


def detect(text: str) -> str:
    """从用户原话里挑模板；挑不出来返回 ""（表示不用脚手架，只写代码）。"""
    t = (text or "").lower()
    for stack, kws in _HINTS:
        if any(k in t for k in kws):
            return stack
    if any(w in t for w in _WEB_WORDS):
        return "vite-vue"
    return ""


# ==========================================================================
# 计划（先给用户看）
# ==========================================================================
_TOOL_RE = re.compile(r"\{(\w+)\}")


def _venv_py(path_dir: str, rel: bool = True) -> str:
    """虚拟环境里的解释器路径（rel=True 给人类看的相对写法）。"""
    if rel:
        return (".venv\\Scripts\\python.exe" if os.name == "nt"
                else ".venv/bin/python")
    return os.path.join(path_dir, ".venv",
                        "Scripts" if os.name == "nt" else "bin",
                        "python.exe" if os.name == "nt" else "python")


def _subst(a: str, name: str, path_dir: str, parent: str) -> str:
    """替换与"本机装没装"无关的占位符（名字/路径）。"""
    return (a.replace("{name}", name).replace("{dir}", path_dir)
             .replace("{parent}", parent).replace("{venvpy}", _venv_py(path_dir)))


def _resolve(argv, name, path_dir, parent, rts):
    """执行用：把 {npm}/{python}/... 换成**真实全路径**；缺运行器返回 None。

    必须全路径 —— Windows 下裸 "npm" 会 FileNotFoundError（见模块头）。
    """
    out = []
    for raw in argv:
        a = _subst(raw, name, path_dir, parent)
        m = _TOOL_RE.fullmatch(a)
        if m:
            rt = rts.get(m.group(1), "")
            if not rt:
                return None
            out.append(rt)
        else:
            out.append(a)
    return out


def _display_cmd(argv, name, rts) -> str:
    """给人看的命令单：运行器只显示名字（`npm`），缺了就写 `<本机缺 npm>`。

    第一版直接把模板原样显示，用户看到的是 `$ {npm} install express` ——
    等于把内部占位符糊到脸上，必须解析。
    """
    out = []
    for raw in argv:
        a = (raw.replace("{name}", name).replace("{venvpy}", _venv_py(""))
              .replace("{dir}", ".").replace("{parent}", ".."))
        m = _TOOL_RE.fullmatch(a)
        if m:
            rt = rts.get(m.group(1), "")
            out.append(os.path.basename(rt) if rt else "<本机缺 %s>" % m.group(1))
        else:
            out.append(a)
    return " ".join(out)


def plan(text: str = "", name: str = "", root: str = "",
         stack: str = "") -> dict:
    """出施工计划（**不执行任何命令**）。

    返回 {stack, label, dir, name, slug, mode, needs, missing, steps, hint,
          files, check}
    其中 steps[i] = {desc, argv(占位符形式), cmd(可读单行), timeout}；
    `missing` 列出本机缺的运行器，供界面提前告知。
    """
    st = stack or detect(text or name)
    if st not in TEMPLATES:
        st = "static"
    tpl = TEMPLATES[st]
    nm = safe_name(name or text or tpl["label"])
    # root 语义 = **工作根**（与 executors 传 ctx["ws_dir"] 一致）；
    # 分类目录（project/）由 proj_base 经唯一权威解析，不在这里自己拼 ——
    # 自己拼会绕过 PASM_WORK_ROOT / PASM_STUDIO_DIR 的隔离优先级。
    pdir = os.path.join(proj_base(root), nm)
    rts = {t: which(t) for t in ("npm", "node", "python", "py")}
    if not rts.get("python"):
        rts["python"] = rts.get("py") or ""
    missing = [t for t in tpl["needs"] if not rts.get(t)]
    steps = []
    for s in tpl["steps"]:
        steps.append({
            "desc": s["desc"],
            "argv": list(s["argv"]),
            "cmd": _display_cmd(s["argv"], nm, rts),
            "timeout": s.get("timeout", T_CREATE),
        })
    return {"stack": st, "label": tpl["label"], "dir": pdir, "name": nm,
            "slug": re.sub(r"[^\w\-]", "-", nm).lower() or "app",
            "mode": tpl["mode"], "needs": list(tpl["needs"]),
            "missing": missing, "steps": steps,
            "hint": (tpl["hint"] or "").replace("{name}", nm),
            "files": tpl["files"], "check": tuple(tpl["check"]),
            "input": (text or name or "").strip()}


def format_plan(p: dict) -> str:
    """计划 → 给右侧栏看的中文清单（分步骤，每条命令写清干什么）。"""
    L = ["🧱 **真装框架**：%s" % p["label"],
         "📁 落点：`%s`" % p["dir"]]
    if p.get("missing"):
        L.append("⚠️ 本机缺：%s —— 会落到「离线骨架」，依赖装不上会如实说明"
                 % "、".join(p["missing"]))
    if not p["steps"]:
        L.append("① 直接生成可运行工程文件（本模板零依赖）")
    for i, s in enumerate(p["steps"], 1):
        L.append("%s %s" % ("①②③④⑤⑥⑦⑧⑨"[i - 1] if i <= 9 else "(%d)" % i,
                            s["desc"]))
        L.append("     `$ %s`" % s["cmd"])
    if p.get("hint"):
        L.append("▶ 跑起来：`%s`" % p["hint"])
    return "\n".join(L)


# ==========================================================================
# 执行
# ==========================================================================
def _write_files(p: dict) -> list:
    """落盘骨架文件，返回相对路径列表。越界路径直接跳过。"""
    written = []
    for rel, content in (p.get("files") or {}).items():
        rel = (rel or "").lstrip("/ ").replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            continue
        full = os.path.join(p["dir"], *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as f:
                f.write(content.replace("{name}", p["name"]).replace(
                    "{slug}", p["slug"]))
            written.append(rel)
        except Exception:  # noqa: BLE001  单个文件写不动不影响其余
            continue
    return written


def run(p: dict, log=None, install: bool = True, dry: bool = False) -> dict:
    """按计划施工。返回 {ok, reason?, degraded?, stack, dir, steps, artifacts,
    log, hint}。

    ok=True 的**唯一**条件：工程文件真在盘上，且（依赖真装好了 或 本模板
    本来就不需要依赖）。装不上 → ok=False + reason 说明卡在哪，**绝不假绿**。
    """
    def emit(s):
        if log:
            try:
                log(s)
            except Exception:  # noqa: BLE001
                pass

    out = {"ok": False, "reason": "", "degraded": False,
           "stack": p.get("stack", ""), "label": p.get("label", ""),
           "dir": p.get("dir", ""), "steps": [], "artifacts": [],
           "log": "", "hint": p.get("hint", "")}

    if dry:
        out.update(ok=True, dry=True, reason="dry_run")
        out["log"] = format_plan(p)
        return out

    pdir, name = p["dir"], p["name"]
    lines = [format_plan(p), ""]
    rts = {t: which(t) for t in ("npm", "node", "python", "py")}
    if not rts.get("python"):
        rts["python"] = rts.get("py") or ""
    # 运行期重新核对运行器：计划可能是别处早先建的，拿旧账当准会在
    # 缺运行器时**悄悄当成"装好了"**（自检当场抓到过），也可能让
    # 占位符解析返回 None 而崩在 join 上。
    missing = [t for t in p["needs"] if not rts.get(t)]
    for t in p.get("missing") or []:
        if t not in missing:
            missing.append(t)

    needs_deps = bool(p["steps"])
    wrote = []
    create_ok = True

    # ---------- ① cli 模式：先让官方脚手架建工程 ----------
    if p["mode"] == "cli":
        if missing:
            create_ok = False
            out["degraded"] = True
            out["reason"] = "no_runtime:" + ",".join(missing)
            lines.append("⚠️ 缺 %s，跳过脚手架，改为落盘离线骨架"
                         % "、".join(missing))
        else:
            os.makedirs(os.path.dirname(pdir) or ".", exist_ok=True)
            s = p["steps"][0]
            argv = _resolve(s["argv"], name, pdir, os.path.dirname(pdir), rts)
            if argv is None:
                create_ok = False
                out["degraded"] = True
                out["reason"] = out["reason"] or "no_runtime"
                lines.append("  ✗ 运行器路径没解析出来（缺 %s），改用离线骨架"
                             % "、".join(missing))
            else:
                lines.append("▶ %s" % s["desc"])
                lines.append("  $ %s" % " ".join(argv))
                t0 = time.time()
                rc, txt = _sp(argv, cwd=os.path.dirname(pdir),
                              timeout=s["timeout"])
                out["steps"].append({"desc": s["desc"], "argv": argv, "rc": rc,
                                     "sec": round(time.time() - t0, 1),
                                     "out": _tail(txt, 400)})
                lines.append("  → rc=%s（%.1fs）" % (rc, time.time() - t0))
                if txt:
                    lines.append("  " + _tail(txt, 300).replace("\n", "\n  "))
                if rc != 0:
                    create_ok = False
                    out["reason"] = "create_failed"
                    out["degraded"] = True
                    lines.append("  ✗ 脚手架没建成，改为落盘离线骨架（可手动重试）")

    # ---------- ② 落盘骨架 ----------
    # ⚠️ cli 模式**脚手架成功时绝不写**：那会把官方生成的 package.json /
    #    index.html 覆盖成我们的简化版（真踩过的写法，第一版就写错了）。
    if p["mode"] == "files" or not create_ok:
        os.makedirs(pdir, exist_ok=True)
        wrote = _write_files(p)
    if wrote:
        lines.append("• 落盘文件 %d 个：%s" % (len(wrote), "、".join(wrote[:8])))
        out["artifacts"] += [os.path.join(pdir, *r.split("/")) for r in wrote]

    # ---------- ③ 真装依赖（cli 的第 1 步已在上头跑过） ----------
    rest = p["steps"][1:] if p["mode"] == "cli" else p["steps"]
    if p["mode"] == "cli" and not create_ok and rest:
        # 脚手架都没建成，后面 npm install 也只会白等超时 —— 如实停下
        lines.append("⏭ 跳过装依赖：工程骨架没建成，装了也没意义")
        rest = []
    if not install and rest:
        out["reason"] = out["reason"] or "install_skipped"
        out["degraded"] = True
        lines.append("⏸ 你选了「只落盘、不装依赖」——依赖没装，工程还不能直接跑")
    else:
        for s in rest:
            argv = _resolve(s["argv"], name, pdir, os.path.dirname(pdir), rts)
            if argv is None:
                out["reason"] = out["reason"] or ("no_runtime:" + ",".join(missing))
                out["degraded"] = True
                lines.append("⚠️ 缺运行器，跳过：%s" % s["desc"])
                continue
            lines.append("▶ %s" % s["desc"])
            lines.append("  $ %s" % " ".join(argv))
            t0 = time.time()
            rc, txt = _sp(argv, cwd=pdir, timeout=s["timeout"])
            out["steps"].append({"desc": s["desc"], "argv": argv, "rc": rc,
                                 "sec": round(time.time() - t0, 1),
                                 "out": _tail(txt, 400)})
            lines.append("  → rc=%s（%.1fs）" % (rc, time.time() - t0))
            if txt:
                lines.append("  " + _tail(txt, 300).replace("\n", "\n  "))
            if rc != 0:
                out["reason"] = out["reason"] or "install_failed"
                out["degraded"] = True

    # ---------- ④ 验收：check 里的文件必须真在盘上 ----------
    missing_files = [r for r in p["check"]
                     if not os.path.exists(os.path.join(pdir, *r.split("/")))]
    have_files = not missing_files
    if not have_files:
        out["reason"] = out["reason"] or "files_missing"

    # ok 的唯一口径：文件真在盘上 + 所有跑过的命令都 rc=0 + 没有任何降级
    ran_ok = all(s.get("rc") == 0 for s in out["steps"]) if out["steps"] else True
    out["ok"] = bool(have_files and ran_ok and not out["degraded"])

    lines.append("")
    if out["ok"]:
        lines.append("✅ 完成：工程真在盘上%s"
                     % ("，依赖也装好了" if needs_deps else "（本模板零依赖）"))
    else:
        why = {"create_failed": "脚手架没建成",
               "install_failed": "依赖没装上",
               "install_skipped": "依赖未安装（你选了只落盘）",
               "files_missing": "关键文件没落盘：%s" % "、".join(missing_files)}.get(
                   out["reason"].split(":")[0], out["reason"] or "未完成")
        lines.append("⚠️ 未完成：%s。工程结构已在 `%s`，但**还不能直接跑**"
                     % (why, pdir))
        if out["degraded"]:
            lines.append("  装好运行器/网络后，在 `%s` 里重跑计划里的命令即可。" % pdir)
    out["log"] = "\n".join(lines)
    return out


def executor(step: dict, ctx: dict = None) -> tuple:
    """workflow_engine 的 `code` 工种执行器：真装框架 + 真落盘。

    返回 (ok, progress_text, artifacts) —— 与其它执行器同签名。
    """
    ctx = ctx or {}
    goal = ctx.get("goal") or ctx.get("title") or (step or {}).get("title") or ""
    p = plan(text=goal, name=ctx.get("name") or goal,
             root=ctx.get("root") or "")
    res = run(p, install=bool(ctx.get("install", True)))
    return res["ok"], res["log"] + (
        "" if res["ok"] else "\n（未标完成：%s）" % (res["reason"] or "未知")), \
        res["artifacts"]


# ==========================================================================
# 自检（全离线：不装任何东西、不碰网络、不写用户真实目录）
# ==========================================================================
def selftest() -> int:
    import tempfile

    passed = failed = 0

    def ck(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  ok   %s" % name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, extra))

    print("== scaffold 自检 ==")
    old_env = os.environ.get("PASM_STUDIO_DIR")
    tmp = tempfile.mkdtemp(prefix="pasm_scaffold_")
    AT = None
    try:
        os.environ["PASM_STUDIO_DIR"] = tmp
        try:
            import agent_tools as AT
            AT.set_workspace(tmp)          # 注意 API 叫 set_workspace（不是 set_data_dir）
        except Exception:  # noqa: BLE001
            AT = None

        # ---------- detect 正例 ----------
        pos = [("用 vue 做个后台管理系统", "vite-vue"),
               ("帮我搭一个 react 项目", "vite-react"),
               ("用 fastapi 写个接口服务", "fastapi"),
               ("flask 做个接口", "flask"),
               ("用 express 做个 node 后端 api", "node-express"),
               ("做个静态网页", "static"),
               ("写个 python 脚本做批量处理", "python-script"),
               ("帮我做一个公司的官网", "vite-vue")]
        for text, want in pos:
            ck("detect(%r) -> %s" % (text[:14], want), detect(text) == want,
               detect(text))
        # ---------- detect 反例：聊天不该触发脚手架 ----------
        for text in ("今天天气怎么样", "你好呀", "帮我把这句话翻译成英文",
                     "我有点累"):
            ck("闲聊 %r 不触发脚手架" % text[:8], detect(text) == "", detect(text))

        # ---------- 清洗：拒绝越界名 ----------
        ck("safe_name 去掉 ..", ".." not in safe_name("../../etc/passwd"))
        ck("safe_name 去掉分隔符", "/" not in safe_name("a/b\\c") and
           "\\" not in safe_name("a/b\\c"))
        ck("safe_name 空 -> 兜底", safe_name("") == "project")

        # ---------- plan 结构 ----------
        p = plan(text="用 vue 做个后台", name="unit demo")
        ck("落点被隔离在 PASM_STUDIO_DIR 下（绝不写用户真实目录）",
           os.path.normpath(p["dir"]).startswith(os.path.normpath(tmp)),
           p["dir"])
        ck("plan 有可读命令单", all(s.get("cmd") for s in p["steps"]))
        ck("命令单里没有裸露的占位符（第一版把 {npm} 直接糊给用户）",
           "{npm}" not in format_plan(p) and "{name}" not in format_plan(p)
           and "{python}" not in format_plan(p))
        ck("plan 不含 shell 拼接（argv 是列表）",
           all(isinstance(s["argv"], list) for s in p["steps"]))
        ck("format_plan 是中文分步清单",
           "真装框架" in format_plan(p) and "$ " in format_plan(p))

        # ---------- dry run 无副作用 ----------
        d = run(p, dry=True)
        ck("dry run ok 且不落盘",
           d["ok"] and d.get("dry") and not os.path.exists(p["dir"]))

        # ---------- 真落盘：static 零依赖必须 ok ----------
        ps = plan(text="做个静态网页", name="site1")
        rs = run(ps)
        ck("static 真落盘且 ok=True", rs["ok"], rs.get("reason"))
        ck("static 的 artifacts 都真实存在",
           rs["artifacts"] and all(os.path.exists(a) for a in rs["artifacts"]))
        ck("static 内容含项目名（不是空壳）",
           "site1" in open(os.path.join(ps["dir"], "index.html"),
                           encoding="utf-8").read())

        # ---------- 缺运行器：必须回退骨架 + ok=False + 说明缺谁 ----------
        saved = which
        try:
            globals()["which"] = lambda t: ""      # 模拟裸机
            pv = plan(text="用 vue 做个后台", name="vueoff")
            rv = run(pv)
            ck("缺 npm 时 ok=False（不许假装装好）", rv["ok"] is False, rv.get("reason"))
            ck("缺 npm 时仍落盘离线骨架",
               os.path.exists(os.path.join(pv["dir"], "package.json")))
            ck("缺 npm 时日志明说缺什么",
               "缺" in rv["log"] and ("npm" in rv["log"]))
            ck("骨架是真的最小可跑工程（有入口文件）",
               os.path.exists(os.path.join(pv["dir"], "src", "App.vue")))

            # 旧账失效：计划是运行器还在时建的，run 时已消失 —— 必须当场发现，
            # 不能拿计划里的 missing=[] 当准（自检抓到的真 bug）
            globals()["which"] = saved                # 先还原，让计划在"有 npm"时生成
            pv2 = plan(text="用 vue 做个后台", name="vueoff2")
            ck("（前置）该计划里 missing 为空", pv2["missing"] == [])
            globals()["which"] = lambda t: ""         # 运行前运行器消失
            rv2 = run(pv2)
            ck("运行期运行器消失也必须 ok=False", rv2["ok"] is False, rv2.get("reason"))
        finally:
            globals()["which"] = saved

        # ---------- install=False：如实标未完成 ----------
        pi = plan(text="做个静态网页", name="noinst")
        ri = run(pi, install=False)
        ck("零依赖模板 install=False 仍 ok", ri["ok"])

        # ---------- 命令安全：argv 里绝不出现用户原话 ----------
        pe = plan(text="用 vue 做个后台; rm -rf /", name="safe1")
        joined = " ".join(" ".join(s["argv"]) for s in pe["steps"])
        ck("用户原话没被拼进命令", "rm" not in joined and ";" not in joined)
    finally:
        os.environ.pop("PASM_STUDIO_DIR", None)
        if old_env is not None:
            os.environ["PASM_STUDIO_DIR"] = old_env
        try:
            if AT is not None:
                AT.set_workspace(None)     # 还原工作空间，别把隔离状态留给后续
        except Exception:  # noqa: BLE001
            pass
        import shutil as _sh
        _sh.rmtree(tmp, ignore_errors=True)

    print("scaffold 自检：%d 通过 / %d 失败" % (passed, failed))
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if selftest() else 0)
