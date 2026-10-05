"""coder —— PASM 多语言开发引擎（v0.26，真文件真目录真冒烟）
================================================================
修正 v0.25 之前"写代码只回文本/文件落在应用数据目录"的问题：
  1. 由用户选定**目标目录**（选目录或既定工作区），代码真实写入 <目录>/<项目名>/
  2. 语言不预设：用户说"用 php/vue/java/go/c#/c++"就按该语言脚手架；没说要 = python
  3. 流程：LLM 规划文件树 → 写脚手架骨架 → 逐文件生成完整内容 → 落盘 →
     按语言跑静态冒烟（python -m py_compile / node --check / php -l 等，缺工具则明确跳过）
  4. 每次产出把 文件清单+冒烟结果 记入 workctx 台账 → 下次"改这个项目"精准续写
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional

if os.environ.get("PASM_STUDIO_DIR"):
    DATA_DIR = os.environ["PASM_STUDIO_DIR"]
else:
    try:
        from pasm_companion import DATA_DIR  # noqa: F401
    except Exception:
        DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                                "PASMStudio")

# 语言 → (描述, 扩展, 主文件, 冒烟命令模板占位)
LANGS: Dict[str, dict] = {
    "python":  {"ext": "py",  "main": "main.py",     "smoke": ("python", "-m", "py_compile"),
                "hint": "Python（FastAPI/CLI/爬虫等，若提 Flask/Django/FastAPI 自动识别）"},
    "vue":     {"ext": "vue", "main": "src/main.js", "smoke": ("node", "--check"),
                "hint": "Vue3 前端（Vite 风格：index.html + src/main.js + App.vue）"},
    "js":      {"ext": "js",  "main": "index.js",    "smoke": ("node", "--check"),
                "hint": "Node.js"},
    "php":     {"ext": "php", "main": "index.php",   "smoke": ("php", "-l"),
                "hint": "PHP（可含简单 Web 入口）"},
    "java":    {"ext": "java", "main": "Main.java",  "smoke": None,
                "hint": "Java（单文件 Main.java 起步）"},
    "go":      {"ext": "go",  "main": "main.go",     "smoke": None,
                "hint": "Go（main.go + go.mod）"},
    "csharp":  {"ext": "cs",  "main": "Program.cs",  "smoke": None,
                "hint": "C#（Program.cs + .csproj）"},
    "cpp":     {"ext": "cpp", "main": "main.cpp",    "smoke": None,
                "hint": "C++（main.cpp + CMakeLists.txt）"},
}
_ALIAS = {"py": "python", "python3": "python", "node": "js", "nodejs": "js",
          "typescript": "js", "ts": "js", "cs": "csharp", "c#": "csharp",
          "csharp": "csharp", "c++": "cpp", "cxx": "cpp", "golang": "go",
          "vue3": "vue", "react": "js"}


def detect_lang(text: str, prefer: str = "") -> str:
    """从用户消息/偏好猜语言；猜不到回 python。"""
    if prefer:
        p = prefer.strip().lower()
        if p in LANGS or p in _ALIAS:
            return _ALIAS.get(p, p)
    t = (text or "").lower()
    for kw, lang in (("c#", "csharp"), ("c++", "cpp"), ("golang", "go"),
                     ("typescript", "js"), ("javascript", "js"), ("vue", "vue"),
                     ("node", "js"), ("python", "python"), ("php", "php"),
                     ("java", "java")):
        if kw in t:
            return _ALIAS.get(lang, lang)
    return "python"


def probe_jdk() -> dict:
    """探测本机 JDK：返回 {major, home, raw, ok}。major 为 None 表示未探测到。

    major 解析规则：javac 1.8.0_321 → 8；javac 17.0.2 → 17；javac 21 → 21。"""
    out = {"major": None, "home": os.environ.get("JAVA_HOME", ""), "raw": "", "ok": False}
    exe = shutil.which("javac") or shutil.which("java")
    if not exe:
        return out
    try:
        r = subprocess.run([exe, "-version"], capture_output=True, text=True, timeout=15)
        raw = (r.stdout or "") + (r.stderr or "")
        out["raw"] = raw.strip()
        m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", raw)
        if m:
            a = int(m.group(1))
            b = int(m.group(2))
            out["major"] = a if a != 1 else b       # 1.8 -> 8
            out["ok"] = True
    except Exception:                                       # noqa: BLE001
        pass
    return out


def lang_label(lang: str) -> str:
    return LANGS.get(lang, LANGS["python"])["hint"]


def safe_name(name: str) -> str:
    return re.sub(r"[^\w\u4e00-\u9fff-]+", "_", str(name or "app"))[:40].strip("_") or "app"


def _manifest_path(pdir: str) -> str:
    return os.path.join(pdir, ".pasm_dev.json")


def save_manifest(pdir: str, name: str, lang: str, spec: str,
                  files: List[dict]) -> None:
    """把项目底盘写进目录自带 .pasm_dev.json（随目录走，续改时读它对齐）。"""
    try:
        data = {"name": name, "lang": lang, "spec": (spec or "")[:3000],
                "files": [{"path": f.get("path"), "smoke": f.get("smoke") or ""}
                          for f in files if f.get("path")],
                "updated": time.strftime("%Y-%m-%d %H:%M")}
        with open(_manifest_path(pdir) + ".tmp", "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(_manifest_path(pdir) + ".tmp", _manifest_path(pdir))
    except Exception:
        pass


def load_manifest(pdir: str) -> Optional[dict]:
    try:
        with open(_manifest_path(pdir), encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _scaffold(lang: str, name: str) -> Dict[str, str]:
    """每种语言给一份最小骨架（保证永远有可运行起点，再让 LLM 填肉）。"""
    ext = LANGS.get(lang, LANGS["python"])["ext"]
    n = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", name)[:30] or "app"
    if lang == "python":
        return {"main.py": '"""%s - PASM 生成的项目入口"""\n\n\ndef main():\n    print("你好，%s！")\n\n\nif __name__ == "__main__":\n    main()\n' % (n, n),
                "requirements.txt": "# 依赖请按需添加\n",
                "README.md": "# %s\n\n由 PASM Studio 生成（Python）。\n" % n}
    if lang == "vue":
        return {"index.html": "<!DOCTYPE html>\n<html lang=\"zh\">\n<head>\n"
                               "<meta charset=\"UTF-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
                               "<title>%s</title>\n</head>\n<body>\n<div id=\"app\"></div>\n"
                               "<script type=\"module\" src=\"/src/main.js\"></script>\n</body>\n</html>\n" % n,
                "src/main.js": "import { createApp } from 'vue'\nimport App from './App.vue'\n\n"
                               "createApp(App).mount('#app')\n",
                "src/App.vue": "<template>\n  <h1>%s</h1>\n  <p>由 PASM Studio 生成（Vue3）。</p>\n</template>\n\n"
                               "<script setup>\n// 业务逻辑写这里\nexport default { name: 'App' }\n</script>\n" % n,
                "package.json": '{\n  "name": "%s",\n  "scripts": {"dev": "vite", "build": "vite build"},\n'
                                '  "dependencies": {"vue": "^3.4.0"},\n  "devDependencies": {"vite": "^5.0.0"}\n}\n' % n,
                "README.md": "# %s (Vue3)\n" % n}
    if lang == "php":
        return {"index.php": '<?php\n// %s - PASM 生成\necho "你好，%s！";\n' % (n, n),
                "README.md": "# %s (PHP)\n" % n}
    if lang == "java":
        return {"Main.java": 'public class Main {\n    public static void main(String[] args) {\n'
                             '        System.out.println("你好，%s！");\n    }\n}\n' % n,
                "README.md": "# %s (Java)\n" % n}
    if lang == "go":
        return {"main.go": "package main\n\nimport \"fmt\"\n\nfunc main() {\n"
                           "\tfmt.Println(\"你好，%s！\")\n}\n" % n,
                "go.mod": "module %s\n\ngo 1.21\n" % n,
                "README.md": "# %s (Go)\n" % n}
    if lang == "csharp":
        return {"Program.cs": "using System;\n\nclass Program\n{\n    static void Main()\n    {\n"
                              "        Console.WriteLine(\"你好，%s！\");\n    }\n}\n" % n,
                "%s.csproj" % n: '<Project Sdk="Microsoft.NET.Sdk">\n  <PropertyGroup>\n'
                                 '    <OutputType>Exe</OutputType>\n    <TargetFramework>net8.0</TargetFramework>\n'
                                 '  </PropertyGroup>\n</Project>\n',
                "README.md": "# %s (C#)\n" % n}
    if lang == "cpp":
        return {"main.cpp": '#include <iostream>\nint main() {\n    std::cout << "你好，%s！" << std::endl;\n'
                            '    return 0;\n}\n' % n,
                "CMakeLists.txt": "cmake_minimum_required(VERSION 3.10)\nproject(%s)\n"
                                  "add_executable(%s main.cpp)\n" % (n, n),
                "README.md": "# %s (C++)\n" % n}
    if lang == "js":
        # v0.30.5：LANGS 一直声明 js 的入口是 index.js，但脚手架漏了这一支 ——
        # 模型不可用时 js 项目会"零文件却报成功"（见 gen_project 的兜底说明）。
        pkg = re.sub(r"[^A-Za-z0-9._-]+", "-", n).strip("-").lower() or "app"
        return {"index.js": "// %s - PASM 生成（Node.js）\n\n"
                            "function main() {\n"
                            '  console.log("你好，%s！");\n'
                            "}\n\nmain();\n" % (n, n),
                "package.json": ('{\n  "name": "%s",\n  "version": "1.0.0",\n'
                                 '  "private": true,\n'
                                 '  "scripts": {"start": "node index.js"}\n}\n' % pkg),
                "README.md": "# %s (Node.js)\n\n运行：`npm start`\n" % n}
    return {}


_CLOSE_TAGS = ("final|output|answer|result|file|code|response|reply|text|"
               "content|think|thought")


def _strip_xml_tail(t: str) -> str:
    """清掉模型在代码尾部多加的 XML 风格闭合标签（真机实测踩过 4 种形态）。

    v0.31.22 实证：即使提示词已要求"只输出文件内容"，模型仍会加收尾标签：
      ```python ... </final>       → SyntaxError line 276
      ```python ... </output>```
      ```python ... </｜           → 全角竖线（!! 全角 U+FF5C）
      print(1) ... </final>          → 压根不加围栏，只在末尾加标签
    """
    if not t:
        return t
    # 全角/半角竖线都匹配（模型会混用全角标点）
    t = re.sub(r"\n?</(?:%s)\s*>\s*$" % _CLOSE_TAGS, "", t)
    t = re.sub(r"\n?</(?:%s)?\s*[｜|]\s*$" % _CLOSE_TAGS, "", t)
    t = re.sub(r"\n?</(?:%s)?\s*[｜|]\s*>\s*$" % _CLOSE_TAGS, "", t)
    # 连续多个标签（</final></output> 这种）
    for _ in range(3):
        new = re.sub(r"\n?</(?:%s)?\s*>\s*$" % _CLOSE_TAGS, "", t)
        if new == t:
            break
        t = new
    return t.rstrip()


def _strip_fence(text: str) -> str:
    """剥离 markdown 代码围栏（只在"整段就是代码"时才剥）。

    ★ v0.31.22 修正：原实现用非贪婪 ``.*?`` 找首个成对围栏，遇到
    「有开头围栏但结尾被 max_tokens 截断」的长输出时匹配失败，
    围栏原样留在文件首行 → py_compile 报
    ``File "main.py", line 1 ```python  SyntaxError: invalid syntax``。

    ★ 第二次修正：不能对任何带围栏的文本都剥 —— README 这类
    「# 标题 + 说明 + ```bash 代码块```」是**文档**，剥掉会连标题一起吞掉。
    因此只在**首行就是围栏**（即模型整段只回了代码）时才剥，
    其余保持原样。

    ★ 第三次修正：XML 闭合尾巴（`</final>` 等）在**两条路径**都要清 ——
    模型有时压根不加围栏直接给代码，若只在围栏分支清洗就会漏掉。
    """
    t = (text or "").strip()
    if not t:
        return t
    # ★ 先无条件清 XML 尾巴（两种路径都要覆盖）
    t = _strip_xml_tail(t)
    lines = t.splitlines()
    # 只有首行就是 ```lang 时，才认为"整段是代码"，进入剥离逻辑
    if not (lines and re.match(r"^```\w*\s*$", lines[0].strip())):
        return t
    body = "\n".join(lines[1:])
    # 去掉尾部闭合围栏（可能只有一个 ``` 或带尾随文本）
    body = re.sub(r"\n?```\s*$", "", body).rstrip()
    # 若正文里还含 ```，取最后一个作为真正结束（代码内部可能有示例块）
    if "```" in body:
        idx = body.rfind("```")
        body = body[:idx].rstrip()
    # 剥完必须还剩下像样的代码，否则说明剥错了 → 保守返回原文
    out = body.strip() if body.strip() else t
    # 再清一次（围栏内侧可能藏着尾巴）
    out = _strip_xml_tail(out)
    return out or t


# ★ v0.31.22：模型不可用识别 ——
#   0.31.21 最大的假成功来源：模型 401/无 key 时，gen_project 逐文件 except 把
#   「# 生成失败（401 ...）」当正文写进 main.py，还记 ok=true、报「✅ 落盘 N 个文件」。
#   根因是把"模型挂了"和"模型答得不好"当成同一件事，都走了脚手架兜底。
#   这里把两者分开：**模型不可用 → 立即中止、零写盘、如实报错**。
_MODEL_DOWN_PAT = re.compile(
    r"没有可用模型|未配置.{0,6}模型|无可用模型"
    r"|api[\s_-]?key|apikey|401|403|401|Unauthorized|unauthorized"
    r"|authentication|invalid_request_error|permission.?denied"
    r"|余额不足|欠费|quota|rate.?limit|too many requests|429"
    r"|connection|连接失败|超时|timeout|ssl|certificate|network|unreachable"
    r"|name or service not known|ollama|traceback",
    re.I)


# ★ v0.31.22：错误文本冒充源码的检测 ——
#   0.31.21 的 `len(content) < 8` 长度判据被长错误文本绕过（401 报错 >8 字符），
#   导致脚手架也不兜底、报错原文被原样写盘。这里改为按**内容**判有效。
_ERROR_CONTENT_PAT = re.compile(
    r"^\s*#\s*生成失败|生成失败（|^\s*#\s*更新失败|更新失败（"
    r"|Traceback \(most recent|Authentication Fails|authentication_error"
    r"|invalid_request_error|APIConnectionError|ReadTimeout"
    r"|^\s*\{\s*[\"']error[\"']\s*:",
    re.I)


def _is_model_down(err) -> bool:
    """判断异常/文本是否属于"模型不可用"。是 → 不可兜底，必须中止并如实报错。"""
    return bool(_MODEL_DOWN_PAT.search(str(err or "")))


def _looks_like_error(text: str) -> bool:
    """内容是否是「错误/异常」文本（区别于"内容过短"）。

    内容过短 = 模型答得潦草 → 用脚手架兜底是**设计好的正常降级**；
    内容是错误文本 = 模型/链路故障 → 必须中止，不许写盘。
    """
    return bool(_ERROR_CONTENT_PAT.search((text or "").strip()[:500]))


def _is_bad_content(text: str) -> bool:
    """内容是否为「错误/占位」而非真源码。是 → 不得写盘、不得记成功。

    判据：过短（<8 字符）或命中错误特征。"""
    t = (text or "").strip()
    if len(t) < 8:
        return True
    return _looks_like_error(t)


def plan_files(llm_fn: Callable[..., str], spec: str, lang: str,
               name: str) -> tuple:
    """第一步：让 LLM 规划文件树。返回 (ok, [{path, purpose}] 或错误文本)。
    规划失败时退回语言脚手架默认清单，保证流程不断。"""
    sys_p = ("你是软件架构师。为一个「%s」项目规划最小但完整的文件清单。"
             "只输出 JSON 数组（不要多余文字）：[{\"path\":\"相对路径\",\"purpose\":\"用途\"}]。"
             "purpose 用中文写清楚该文件职责。要求：≤12 个文件；不要列 node_modules/dist/__pycache__；"
             "主入口用 %s。" % (name, LANGS.get(lang, LANGS["python"])["main"]))
    try:
        raw = llm_fn(sys_p, "语言：" + lang_label(lang) + "\n需求：\n" + (spec or "")[:1200],
                     task="code")
        m = re.search(r"\[[\s\S]*\]", str(raw))
        if not m:
            return False, "规划失败（模型没返回文件清单）。"
        files = json.loads(m.group(0))
        files = [f for f in files if isinstance(f, dict)
                 and re.match(r"^[\w./-]+$", str(f.get("path", "")))]
        if not files:
            return False, "规划失败（清单为空）。"
        for f in files:
            f["purpose"] = str(f.get("purpose") or "")[:160]
        return True, files
    except Exception as ex:
        # ★ v0.31.22：模型不可用要如实上报（可被 gen_project 识别并中止），
        # 不能伪装成"规划失败"让上层退回脚手架继续假装能生成。
        if _is_model_down(ex):
            return False, "模型不可用（%s）" % ex
        return False, "规划异常（%s）" % ex


def _default_plan(lang: str) -> List[dict]:
    return [{"path": p, "purpose": ""} for p in _scaffold(lang, "app")]


def gen_project(llm_fn: Callable[..., str], spec: str, lang: str, name: str,
                target_dir: str,
                digest_extra: str = "",
                progress: Optional[Callable[[str], None]] = None,
                max_files: int = 12) -> dict:
    """完整开发一版：规划 → 骨架 → 逐文件生成 → 落盘 → 冒烟。
    返回 {ok, dir, files:[{path, ok, smoke?}], smoke, log}。"""
    def _say(m):
        if progress:
            try:
                progress(m)
            except Exception:
                pass

    name = (name or "app").strip()
    lang = detect_lang(spec, lang)
    # ★ 0.31.21：JDK 自适应 —— 本机 JDK < 17 却生成 Spring Boot 3（需 JDK17）会直接
    #   构建失败。探测后注入兼容指令，让模型产出可在本机跑通的代码（SB2.7/Java8/javax）。
    _jdk_directive = ""
    if lang == "java":
        _jdk = probe_jdk()
        if _jdk.get("ok") and isinstance(_jdk.get("major"), int) and _jdk["major"] < 17:
            _jdk_directive = (
                "\n⚠️ 本机 JDK 为 %d（<17），请生成**兼容 Java 8** 的代码："
                "Spring Boot 用 2.7.18、Java 源码级别 8、使用 javax 命名空间"
                "（**不要用 jakarta**）、pom 里**显式指定所有依赖版本**（含 "
                "mysql-connector-j 版本号），不要用 --release 17 之类的无效标志。"
                % _jdk["major"])
    base = os.path.abspath(target_dir)
    if not os.path.isdir(base):
        try:
            os.makedirs(base, exist_ok=True)
        except Exception:
            return {"ok": False, "dir": "", "files": [], "smoke": "", "log": "目标目录不可写：" + str(base)}
    pdir = os.path.join(base, safe_name(name))
    os.makedirs(pdir, exist_ok=True)

    _say("🧱 [1/4] 规划「%s」(%s) 的文件结构…" % (name, lang))
    okp, plan = plan_files(llm_fn, spec, lang, name)
    if not okp:
        # ★ v0.31.22：模型不可用 → 立即中止、零写盘、如实报错。
        #   0.31.21 在这里无条件退回脚手架，导致无模型也"生成成功"。
        if _is_model_down(plan):
            shutil.rmtree(pdir, ignore_errors=True)
            return {"ok": False, "dir": "", "lang": lang, "files": [],
                    "smoke": "", "model_down": True,
                    "log": "模型不可用，已中止生成（未写出任何文件）：%s\n"
                           "请配置有效的云端 API Key，或启动本地 Ollama 后重试。" % str(plan)[:300]}
        _say("规划失败，退回%s脚手架默认清单。" % lang)
        plan = _default_plan(lang)
    plan = [p for p in plan if p.get("path")][:max_files]

    # 逐文件生成并写盘（所有计划内文件都生成；失败回退脚手架骨架，绝不漏文件）
    files_done: List[dict] = []
    scaff = _scaffold(lang, name)
    ctx_head = "语言 %s · 项目名 %s" % (lang, name)
    if digest_extra:
        ctx_head += "\n" + digest_extra[:1200]
    total = len(plan)
    for i, p in enumerate(plan, 1):
        rel = p["path"].lstrip("/ ")
        if not rel:
            continue
        purpose = p.get("purpose", "")
        _say("✍️ [2/4] 生成 %s（%d/%d）…" % (rel, i, total))
        sys_p = ("你是严谨的软件工程师。为 %s 项目写文件 `%s`，只输出该文件完整内容（不要解释、"
                 "不要 markdown 代码块包裹），内容必须是可直接运行的成品，禁止「省略」「同理」。"
                 "代码必须完整、真实可运行：只用真实存在的库与 API，不要臆造函数/参数/依赖；"
                 "严格按需求与该文件用途实现，不跑题；注释用中文，需要向用户解释时一律用中文。"
                 % (lang, rel))
        hint = ("该文件用途：%s\n\n项目骨架里已有的文件：%s\n\n需求：\n%s%s"
                % (purpose, "、".join(f["path"] for f in files_done) or "（无）",
                   (spec or "")[:1500], _jdk_directive))
        content = ""
        gen_err = ""
        try:
            content = _strip_fence(str(llm_fn(sys_p, hint, task="code") or ""))
        except Exception as ex:
            gen_err = str(ex)
            # ★ v0.31.22：模型不可用 → 整个生成立即中止，绝不写盘。
            #   0.31.21 在这里把异常文本当正文写进源文件（真机 D:\geo\main.py
            #   内容就是一句 401 报错），还记 ok=true 报「✅ 落盘 3 个文件」。
            if _is_model_down(ex):
                shutil.rmtree(pdir, ignore_errors=True)
                return {"ok": False, "dir": "", "lang": lang, "files": [],
                        "smoke": "", "model_down": True,
                        "log": "模型不可用，已中止生成（未写出任何文件）：%s\n"
                               "请配置有效的云端 API Key，或启动本地 Ollama 后重试。"
                               % gen_err[:300]}
            content = ""
        # ★ v0.31.22：内容有效性判据（替代原来只看长度的 `len(content) < 8`）。
        #   错误文本长度远超 8，靠长度判据会被绕过；必须按内容判。
        #   注意区分：内容是「错误文本」→ 故障中止；内容只是「过短」→ 正常降级用脚手架。
        used_scaffold = False
        if _looks_like_error(content) or _is_model_down(gen_err):
            shutil.rmtree(pdir, ignore_errors=True)
            return {"ok": False, "dir": "", "lang": lang, "files": [],
                    "smoke": "", "model_down": True,
                    "log": "模型不可用，已中止生成（未写出任何文件）：%s\n"
                           "请配置有效的云端 API Key，或启动本地 Ollama 后重试。"
                           % ((gen_err or content)[:300])}
        if _is_bad_content(content):            # 仅内容过短 → 脚手架兜底（正常降级）
            content = scaff.get(rel, "")
            used_scaffold = True
            if not content:                      # 该语言无脚手架且内容无效 → 跳过，不写空文件
                continue
        full = os.path.join(pdir, *rel.split("/"))
        # ★ v0.31.22 语法自愈闸门：写盘前先校验，语法不过就把**真实报错**
        #   回灌给模型让它重写。这是 WorkBuddy 式「检测→再操作」闭环的关键一环。
        #   真机实测：单文件超长时 DeepSeek 会 finish_reason=length 硬截断，
        #   代码停在半路（如 `elif` 后无内容）→ SyntaxError。清洗治不了断句，
        #   只能让模型自己改。最多修 2 轮，仍不过则保留并如实标 ✗。
        for attempt in range(3):
            if not _looks_like_error(content) and not _is_bad_content(content):
                probe = _tmp_probe(content, rel)
                chk = _smoke_one(lang, probe) if probe else None
                if probe:                        # 清理临时文件与其 __pycache__
                    _cleanup_probe(probe)
                if chk is None or chk.startswith("✓"):
                    break                      # 语法OK（或该语言无自动检查）
                fix_sys = ("你上一步写的文件 `%s` **语法错误**，无法运行。请只输出修正后的"
                           "完整文件内容（不要代码块包裹、不要解释）。务必把语法彻底修好，"
                           "不要省略任何部分。\n\n【语法检查报错】\n%s" % (rel, chk[:600]))
                _say("🔧 %s 语法未通过，回灌报错让模型重写（第 %d 轮）…" % (rel, attempt + 1))
                try:
                    content = _strip_fence(str(llm_fn(fix_sys, hint, task="code") or ""))
                except Exception as ex2:
                    if _is_model_down(ex2):
                        shutil.rmtree(pdir, ignore_errors=True)
                        return {"ok": False, "dir": "", "lang": lang, "files": [],
                                "smoke": "", "model_down": True,
                                "log": "模型不可用，已中止生成：%s" % str(ex2)[:200]}
                    break                      # 非模型故障 → 保留原样，如实标 ✗
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as ex:
            return {"ok": False, "dir": pdir, "files": files_done,
                    "smoke": "", "log": "写入失败 %s：%s" % (rel, ex)}
        # ★ scaffold 标记只反映"这一次内容是否来自脚手架兜底"。
        #   0.31.22 修正：原先写成 `used_scaffold or rel in scaff`，导致
        #   python 脚手架里的 main.py/README.md 即使是模型真产出也被标成
        #   scaffold → degraded 恒为真 → 真正的降级被掩盖。
        files_done.append({"path": rel, "scaffold": used_scaffold})

    # 脚手架里计划外的文件（如 README）也补齐，保证树完整
    for rel, content in scaff.items():
        if not any(f["path"] == rel for f in files_done):
            full = os.path.join(pdir, *rel.split("/"))
            try:
                os.makedirs(os.path.dirname(full), exist_ok=True)
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass
            files_done.append({"path": rel, "scaffold": True})

    # 第三步：逐个静态冒烟
    smoke_lines: List[str] = []
    checked = 0
    for fd in files_done:
        full = os.path.join(pdir, *fd["path"].split("/"))
        r = _smoke_one(lang, full)
        if r is not None:
            checked += 1
            fd["smoke"] = r
            if r.startswith("✓"):
                smoke_lines.append("%s ✓" % fd["path"])
            else:
                smoke_lines.append("%s ✗ %s" % (fd["path"], r[:120]))
    smoke_txt = ("；".join(smoke_lines) if smoke_lines else
                 ("（本机缺 %s 运行环境，跳过自动冒烟——文件已全部落盘，可直接用对应工具运行）" % lang))
    # v0.30.5：零文件不许报成功。此前 js（脚手架缺失 + 模型不可用）会让
    # files_done 为空，而 all(空) 恒为 True → {"ok": True, "files": []}：
    # 界面显示"完成"、磁盘上一个源码文件都没有 —— 典型的假成功。
    if not files_done:
        return {"ok": False, "dir": pdir, "lang": lang, "files": [],
                "smoke": "",
                "log": "没有写出任何文件：文件规划为空，且该语言没有脚手架兜底。"
                       "请确认模型可用，或显式指定语言后重试。"}
    ok = all(not f.get("smoke") or f["smoke"].startswith("✓") for f in files_done)
    # ★ v0.31.22：全脚手架兜底 = **降级交付**（模型答不出但流程走完了），
    #   不再一刀切判失败（0.31.21 的假成功源头是"错误文本被当正文"，
    #   那个洞已由 _looks_like_error 堵住）；但必须 degraded=True 如实标记，
    #   由上层告知用户"这是骨架，不是你要的成品"。
    real = [f for f in files_done if not f.get("scaffold")]
    degraded = bool(files_done) and len(real) < len(files_done)
    # ★ v0.31.22：依赖一致性检查 + 自动补齐。
    #   真机实测：main.py `import typer` 但 requirements.txt 只写 fastapi/uvicorn
    #   → 装完依赖真跑 ModuleNotFoundError。语法检查抓不到"能编译但跑不起来"。
    #   这里把漏声明的第三方库补进 requirements.txt，并如实告知。
    missing = _missing_deps(pdir, lang)
    if missing:
        req = os.path.join(pdir, "requirements.txt")
        try:
            with open(req, "a", encoding="utf-8") as f:
                f.write("\n# v0.31.22 自动补齐：检测到代码 import 但未声明的依赖\n")
                for m in missing:
                    f.write("%s\n" % m)
            _say("🔧 自动补齐 requirements.txt 缺失依赖：%s" % "、".join(missing))
        except Exception as ex:
            _say("⚠️ 依赖补齐失败：%s" % ex)
    save_manifest(pdir, name, lang, spec, files_done)
    _say("✅ [4/4] 落盘 %d 个文件：%s" % (len(files_done), pdir))
    if degraded:
        _say("⚠️ 其中 %d 个文件由脚手架骨架兜底（模型未产出内容），非完整成品。"
             % (len(files_done) - len(real)))
    return {"ok": ok, "dir": pdir, "lang": lang,
            "files": files_done, "smoke": smoke_txt,
            "missing_deps": missing,
            "degraded": degraded,
            "log": "\n".join(smoke_lines)}


def _tmp_probe(content: str, rel: str) -> str:
    """把内容写到临时同名文件，供 _smoke_one 做语法检查（不碰项目目录）。

    用于 0.31.22 的「写盘前语法自愈闸门」：先在临时位置验证，
    通过了再写进项目，避免把语法错误的半成品落盘。
    """
    import tempfile
    ext = os.path.splitext(rel)[1] or ".txt"
    fd, path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        return path
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        return ""


def _cleanup_probe(path: str) -> None:
    """清理 _tmp_probe 留下的临时文件与其 __pycache__（py_compile 会生成）。"""
    if not path:
        return
    d = os.path.dirname(path)
    try:
        cache = os.path.join(d, "__pycache__")
        if os.path.isdir(cache):
            shutil.rmtree(cache, ignore_errors=True)
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass


_STDLIB = set(getattr(sys, "stdlib_module_names", ())) if hasattr(sys, "stdlib_module_names") else set()


def _missing_deps(pdir: str, lang: str) -> List[str]:
    """检测「import 的第三方库」是否都没写进 requirements.txt。

    ★ v0.31.22 真机实测踩到：main.py 用了 typer，但 requirements.txt 只写了
    fastapi/uvicorn → 装完依赖真跑直接 ModuleNotFoundError。
    语法检查抓不到这类"能编译但跑不起来"的问题，必须专门查依赖一致性。
    """
    if lang != "python":
        return []
    req = os.path.join(pdir, "requirements.txt")
    declared = ""
    if os.path.isfile(req):
        try:
            with open(req, encoding="utf-8", errors="replace") as f:
                declared = f.read().lower()
        except Exception:
            declared = ""
    imported = set()
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", ".git", "node_modules")]
        for fn in fs:
            if not fn.endswith(".py"):
                continue
            try:
                with open(os.path.join(root, fn), encoding="utf-8", errors="replace") as f:
                    src = f.read()
            except Exception:
                continue
            for m in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_][\w]*)", src, re.M):
                imported.add(m.group(1).lower())
    if _STDLIB:
        local = {os.path.splitext(x)[0].lower() for x in os.listdir(pdir)
                 if x.endswith(".py")} if os.path.isdir(pdir) else set()
        local |= {d.lower() for d in os.listdir(pdir) if os.path.isdir(os.path.join(pdir, d))} \
            if os.path.isdir(pdir) else set()
        candidates = {m for m in imported if m not in _STDLIB and m not in local
                      and m not in ("__future__",)}
    else:
        candidates = {m for m in imported if m not in ("os", "sys", "json", "re", "time",
                      "random", "argparse", "datetime", "typing", "pathlib", "shutil",
                      "subprocess", "math", "collections", "itertools", "functools")}
    missing = []
    for m in sorted(candidates):
        # requirements 里出现该名字（可能带版本/ extras）即视为已声明
        if m in declared or m.replace("_", "-") in declared:
            continue
        missing.append(m)
    return missing


def _smoke_one(lang: str, full: str) -> Optional[str]:
    """对单个源码文件跑静态冒烟（工具在才跑，5s 超时）。"""
    if lang == "python" and full.endswith(".py"):
        return _run_check(["python", "-m", "py_compile", full])
    if lang in ("js", "vue") and full.endswith((".js", ".mjs")):
        return _run_check(["node", "--check", full])
    if lang == "php" and full.endswith(".php"):
        return _run_check(["php", "-l", full])
    return None


def _run_check(cmd: List[str]) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return "✓ 语法通过" if r.returncode == 0 else "✗ " + (r.stderr or r.stdout or "").strip()[-200:]
    except FileNotFoundError:
        return ""
    except Exception:
        return ""


def update_project(llm_fn: Callable[..., str], spec: str, request: str,
                   lang: str, pdir: str, existing: List[dict],
                   digest_extra: str = "",
                   progress: Optional[Callable[[str], None]] = None) -> dict:
    """续写/修改：只让 LLM 决定改哪些文件，逐文件重写，旧的删掉已不存在的。
    existing=[{path, ok}] 当前文件清单（由 gen 或上次 update 返回）。"""
    def _say(m):
        if progress:
            try:
                progress(m)
            except Exception:
                pass
    lang = lang if lang in LANGS else detect_lang(request, "python")
    cur = [f["path"] for f in existing if f.get("path")]
    sys_p = ("你是严谨的软件工程师，负责对一个已存在的 %s 项目做精准修改。\n"
             "现在文件：%s\n\n用户的修改要求：\n%s\n\n"
             "请只输出 JSON 对象：{\"change\":[\"相对路径\",...],"
             "\"add\":[{\"path\":\"新文件路径\",\"purpose\":\"用途\"}],\"drop\":[\"要删除的旧路径\"]}。"
             "purpose 用中文。只列真正要动的文件；纯文本改动别整篇重写别加多余文件。"
             % (lang, "、".join(cur) or "（空）", (request or "")[:1200]))
    targets: Dict[str, str] = {}
    drops: List[str] = []
    model_down = False
    try:
        raw = str(llm_fn(sys_p, "项目原始需求：\n" + (spec or "")[:800]
                         + ("\n\n台账摘要：\n" + digest_extra[:1000] if digest_extra else ""),
                         task="code") or "")
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            obj = json.loads(m.group(0))
            for p in obj.get("change", []) or []:
                if isinstance(p, str):
                    targets[str(p)] = "修改：" + request[:100]
            for a in obj.get("add", []) or []:
                if isinstance(a, dict) and a.get("path"):
                    targets[str(a["path"])] = str(a.get("purpose") or "新增")
            drops = [str(p) for p in (obj.get("drop", []) or []) if isinstance(p, str)]
    except Exception as ex:
        # ★ v0.31.22：模型不可用 → 立即中止、零写盘（如实报错）。
        #   0.31.21 在这里 `except: pass` 把 401 整个吞掉，再"回退全量重写"，
        #   把每个旧文件都覆写成报错文本（真机 D:\geo 就是这么被写坏的）。
        if _is_model_down(ex):
            return {"ok": False, "dir": pdir, "files": [], "smoke": "",
                    "model_down": True,
                    "log": "模型不可用，已中止修改（未改动任何文件）：%s\n"
                           "请配置有效的云端 API Key，或启动本地 Ollama 后重试。"
                           % str(ex)[:300]}
        model_down = True
    if not targets:
        if model_down:
            # 模型调用异常且拿不到任何清单 → 不许"全量重写"（会毁掉现有项目）
            return {"ok": False, "dir": pdir, "files": [], "smoke": "",
                    "log": "模型调用失败且未能解析修改清单，为保护现有代码已中止修改。"
                           "请确认模型可用后重试。"}
        _say("修改清单解析失败，回退为全量重写现有文件…")
        for p in cur:
            targets[p] = "按最新要求重写"

    pdir = os.path.abspath(pdir)
    done: List[dict] = []
    i = 0
    for rel, why in targets.items():
        rel = rel.lstrip("/ ")
        if re.search(r"\.\.|^[/\\]", rel):
            continue
        i += 1
        _say("✍️ 更新 %s（%d/%d）…" % (rel, i, len(targets)))
        ctx_extra = (("要求：%s" % request[:400]) if not why or why.startswith("修改") else
                     ("新文件用途：%s" % why))
        sys_p = ("你是严谨的软件工程师。这是 %s 项目里的文件 `%s`。请按要求改写/编写该文件，"
                 "只输出文件完整内容，不要代码块包裹、不要解释。与它无关的部分保持原样或合理联动。"
                 "改动要精准：只实现用户要求的变更，不动无关逻辑；只用真实存在的库与 API，不臆造；"
                 "注释用中文，需要向用户解释时一律用中文。"
                 "禁止省略号占位。" % (lang, rel))
        try:
            content = _strip_fence(str(llm_fn(sys_p, ctx_extra, task="code") or ""))
        except Exception as ex:
            # ★ v0.31.22：模型不可用 → 立即中止，绝不覆写已有文件。
            #   续改路径下覆写尤其危险：会把用户现有的正常代码替换成一句报错。
            if _is_model_down(ex):
                return {"ok": False, "dir": pdir, "files": done, "smoke": "",
                        "model_down": True,
                        "log": "模型不可用，已中止修改（未改动任何文件）：%s\n"
                               "请配置有效的云端 API Key，或启动本地 Ollama 后重试。"
                               % str(ex)[:300]}
            content = ""
        # ★ v0.31.22：内容有效性判据（替代只看长度的 `len(content) < 8`）
        #   错误文本 → 故障中止；仅内容过短 → 脚手架兜底（正常降级）
        if _looks_like_error(content):
            return {"ok": False, "dir": pdir, "files": done, "smoke": "",
                    "model_down": True,
                    "log": "模型不可用，已中止修改（未改动任何文件）。\n"
                           "请配置有效的云端 API Key，或启动本地 Ollama 后重试。"}
        if _is_bad_content(content):
            content = _scaffold(lang, "app").get(rel, "")
            if not content:
                continue
        full = os.path.join(pdir, *rel.split("/"))
        try:
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8") as f:
                f.write(content)
            done.append({"path": rel})
        except Exception as ex:
            return {"ok": False, "dir": pdir, "files": done, "smoke": "",
                    "log": "写入失败 %s：%s" % (rel, ex)}
    for rel in drops:                      # 删除被裁掉的旧文件
        full = os.path.join(pdir, *rel.lstrip("/ ").split("/"))
        try:
            if rel.lstrip("/ ") and os.path.isfile(full) and os.path.realpath(full).startswith(
                    os.path.realpath(pdir)):
                os.remove(full)
        except Exception:
            pass
    # 冒烟
    smoke_lines = []
    for fd in done:
        r = _smoke_one(lang, os.path.join(pdir, *fd["path"].split("/")))
        if r:
            fd["smoke"] = r
            smoke_lines.append("%s %s" % (fd["path"], "✓" if r.startswith("✓") else r[:80]))
    # v0.30.5：与 gen_project 同一条底线 —— "更新了 0 个文件"不能算成功。
    if not done and not drops:
        return {"ok": False, "dir": pdir, "files": [], "smoke": "",
                "log": "这一轮没有产生任何文件改动：模型没给出可落盘的内容，"
                       "把要改的地方说具体一点再试。"}
    ok = all(not f.get("smoke") or f["smoke"].startswith("✓") for f in done)
    save_manifest(pdir, os.path.basename(pdir.rstrip("/\\")), lang,
                  (digest_extra or spec or ""), done)
    return {"ok": ok, "dir": pdir, "files": done, "smoke": "；".join(smoke_lines) or "（跳过）",
            "log": "更新了 %d 个文件%s" % (len(done), "，删除 " + str(len(drops)) if drops else "")}

def selftest() -> bool:
    """自检：语言识别 / 文件名净化 / 脚手架 / 清单往返 / 真落盘（含反例）。

    返回 True/False（供 pasm-skills 回归套件探测）。
    """
    fails = []
    CNT = [0]          # 真实断言条数（供结尾如实汇报）

    def check(name, cond, extra=""):
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        CNT[0] += 1
        if not cond:
            fails.append(name)

    import shutil
    import tempfile

    print("=== coder 自检 ===")

    check("detect_lang: python", detect_lang("写个 python 脚本") == "python")
    check("detect_lang: vue", detect_lang("做个 vue3 页面") == "vue")
    check("detect_lang: typescript 归 js", detect_lang("用 typescript 写") == "js")
    check("detect_lang: c++ 归 cpp", detect_lang("用 c++ 实现") == "cpp")
    check("detect_lang: 猜不到回 python（反例：乱猜）",
          detect_lang("帮我做点东西") == "python", detect_lang("帮我做点东西"))
    check("detect_lang: prefer 优先于正文",
          detect_lang("写个 python 脚本", "go") == "go")
    check("detect_lang: 空串不抛错", detect_lang("") == "python")

    check("safe_name 去非法字符", safe_name("我的 项目/v2") == "我的_项目_v2",
          safe_name("我的 项目/v2"))
    check("safe_name 挡住路径穿越（反例）",
          "/" not in safe_name("../../etc/passwd")
          and "\\" not in safe_name("..\\..\\win.ini"),
          safe_name("../../etc/passwd"))
    check("safe_name 空值兜底", safe_name("") == "app")
    check("safe_name 限长 40", len(safe_name("很" * 200)) <= 40,
          len(safe_name("很" * 200)))

    check("lang_label 可读", "Python" in lang_label("python"), lang_label("python"))

    for lg, main in (("python", "main.py"), ("js", "index.js"), ("go", "main.go"),
                     ("java", "Main.java"), ("php", "index.php")):
        sc = _scaffold(lg, "app")
        check("_scaffold(%s) 有入口 %s" % (lg, main), main in sc, sorted(sc))
    check("★每种已声明语言都有脚手架兜底（反例：声明了却没骨架）",
          all(_scaffold(lg, "app") for lg in LANGS),
          [lg for lg in LANGS if not _scaffold(lg, "app")])
    check("_scaffold(js) 的 package.json 名合法（无中文/空格）",
          re.match(r"^[a-z0-9._-]+$",
                   json.loads(_scaffold("js", "我的 App")["package.json"])["name"])
          is not None,
          _scaffold("js", "我的 App")["package.json"])

    check("_strip_fence 剥代码块",
          _strip_fence("```python\nprint(1)\n```") == "print(1)",
          _strip_fence("```python\nprint(1)\n```"))
    check("_strip_fence 无围栏时原样", _strip_fence("print(1)") == "print(1)")

    def fake_llm(*a, **k):
        return ""            # 模拟模型不可用 / 返回不可解析

    tmp = tempfile.mkdtemp(prefix="coder_selftest_")
    try:
        r = gen_project(fake_llm, "做一个打招呼的小程序", "python",
                        "自检小工具", os.path.join(tmp, "proj"))
        names = []
        for dp, _dn, fns in os.walk(os.path.join(tmp, "proj")):
            names += [os.path.relpath(os.path.join(dp, fn), os.path.join(tmp, "proj"))
                      for fn in fns]
        check("模型不可用时退回脚手架并真的落盘（不谎报）",
              any(x.endswith("main.py") for x in names), names)
        check("清单 .pasm_dev.json 已写", any(x.endswith(".pasm_dev.json")
                                              for x in names), names)
        mf = None
        for dp, _dn, fns in os.walk(os.path.join(tmp, "proj")):
            if ".pasm_dev.json" in fns:
                mf = load_manifest(dp)
        check("清单能读回且语言正确", (mf or {}).get("lang") == "python", mf)
        check("清单记下了文件列表", bool((mf or {}).get("files")), mf)

        # js 曾经"零文件却报成功"：_scaffold 漏了 js 分支，而 all(空) 恒为 True，
        # 于是返回 {"ok": True, "files": []}。修法有两层：① 每个已声明语言都补
        # 脚手架（上面那条断言钉住）；② gen_project 自己加"零文件不许成功"。
        # ②现在被①堵住了，正常路径走不到 —— 用 monkeypatch 把这条路重新打开，
        # 才能真的验到那条底线（否则只是"读了代码觉得对"）。
        _real_scaffold = _scaffold
        globals()["_scaffold"] = lambda lg, n: {}
        try:
            rjs = gen_project(fake_llm, "做一个网页", "js", "空计划项目",
                              os.path.join(tmp, "proj2"))
        finally:
            globals()["_scaffold"] = _real_scaffold
        check("★零文件时不许报成功（反例：假成功）",
              rjs.get("ok") is False and not rjs.get("files"),
              {k: rjs.get(k) for k in ("ok", "files", "log")})
        check("★零文件时给出可读原因",
              "没有写出任何文件" in str(rjs.get("log", "")), rjs.get("log"))

        # 反例：目标其实是个文件 → 如实报失败
        badp = os.path.join(tmp, "其实是个文件.txt")
        with open(badp, "w", encoding="utf-8") as f:
            f.write("x")
        r2 = gen_project(fake_llm, "做一个网页", "js", "坏目标", badp)
        check("目标不可写时如实报 ok=False（反例）",
              r2.get("ok") is False and "不可写" in str(r2.get("log", "")),
              str(r2)[:140])

        check("load_manifest 无清单返回 None（反例）",
              load_manifest(os.path.join(tmp, "nope")) is None)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if fails:
        print("-" * 46)
        print("自检失败 %d 项：%s" % (len(fails), "；".join(fails)))
        return False
    print("-" * 46)
    print("自检通过（%d 项断言）" % CNT[0])
    return True


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(0 if selftest() else 1)
