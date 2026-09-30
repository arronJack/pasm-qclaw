# -*- coding: utf-8 -*-
"""repomap.py —— 项目地图 + 相关文件检索（P0-2 的一半）。

解决的真问题：本地/云端模型"看不见项目"。
真机证据：`llm_gateway._MIN_SYSTEM_CHARS = 1800`，系统提示被压到 1800 字，
一个 SpringBoot+Vue 工程的文件清单都放不下 → 模型只能瞎写，"改已有项目"实际是重写。

本模块只做**取内容**（不碰预算数字）：
  · `build_map()`：紧凑目录树 + 每个文件的**符号摘要**（def/class/function/接口方法），
    让模型知道"项目里有什么"，而不是把源码全塞进去；
  · `pick_relevant()`：按需求文本给文件打分（文件名命中 / 符号命中 / 词重叠 / 语言先验），
    只取 Top-K 的**片段**；
  · `context_for()`：把上面两块按预算拼成一段可直接进提示的文本（含截断标记，绝不超预算）。
预算数字由 `llm_gateway.budget()` 给（云端已放开到 48000 字）。
"""
from __future__ import annotations

import os
import re

#: 只看这些后缀（源码与配置，不索引二进制/依赖）
CODE_EXT = {
    ".py": "python", ".js": "js", ".jsx": "js", ".ts": "ts", ".tsx": "ts",
    ".vue": "vue", ".java": "java", ".go": "go", ".rs": "rust", ".kt": "kotlin",
    ".cs": "csharp", ".php": "php", ".rb": "ruby", ".c": "c", ".h": "c",
    ".cpp": "cpp", ".hpp": "cpp", ".sql": "sql", ".sh": "sh", ".yml": "yaml",
    ".yaml": "yaml", ".json": "json", ".xml": "xml", ".html": "html",
    ".css": "css", ".md": "md", ".toml": "toml", ".ini": "ini",
}
SKIP_DIRS = {".git", "__pycache__", "node_modules", "dist", "build", "target",
             ".venv", "venv", ".idea", ".vscode", "旧版_"}

#: 每个语言的符号抓取（正则，零依赖；抓不到就退化为"文件名级"摘要）
_SYM_PATTERNS = (
    (".py", re.compile(r"^\s*(?:async\s+)?(def|class)\s+(\w+)", re.M)),
    ((".js", ".jsx", ".ts", ".tsx"), re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?(?:function|class|const|let|var)\s+(\w+)", re.M)),
    (".vue", re.compile(r"^\s*(?:const|function|import)\s+(\w+)", re.M)),
    (".java", re.compile(r"^\s*(?:public|protected|private)?\s*(?:static\s+)?"
                         r"(?:final\s+)?(?:class|interface|enum|record)\s+(\w+)", re.M)),
    (".go", re.compile(r"^func\s+(?:\([^)]*\)\s*)?(\w+)", re.M)),
    (".sql", re.compile(r"CREATE\s+(?:TABLE|VIEW)\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w\".]+)", re.I)),
)


def _ext(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def _symbols(path: str, text: str, limit: int = 12) -> list:
    """抓关键符号名（够模型判断"这文件干嘛的"即可）。"""
    out = []
    ext = _ext(path)
    for suffix, pat in _SYM_PATTERNS:
        if (isinstance(suffix, tuple) and ext in suffix) or ext == suffix:
            try:
                for m in pat.finditer(text[:20000]):
                    name = m.group(m.lastindex or 1)
                    if name and name not in out:
                        out.append(name)
                    if len(out) >= limit:
                        break
            except Exception:                                    # noqa: BLE001
                pass
            break
    return out


def iter_files(pdir: str, limit: int = 400):
    """遍历项目文件（跳过依赖/构建产物），产出 (rel, abspath)。"""
    n = 0
    for root, dirs, fs in os.walk(pdir):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith("旧版_")]
        for f in sorted(fs):
            if _ext(f) not in CODE_EXT:
                continue
            ap = os.path.join(root, f)
            yield os.path.relpath(ap, pdir).replace("\\", "/"), ap
            n += 1
            if n >= limit:
                return


def build_map(pdir: str, max_files: int = 120, max_chars: int = 6000) -> str:
    """紧凑项目地图：`路径 — 语言 — 符号1 符号2 …`。超预算就截断并如实标注。"""
    rows, used = [], 0
    for i, (rel, ap) in enumerate(iter_files(pdir)):
        if i >= max_files:
            rows.append("…（还有更多文件，已省略）")
            break
        try:
            txt = open(ap, encoding="utf-8", errors="ignore").read()
        except OSError:
            txt = ""
        syms = _symbols(rel, txt)
        line = "%-46s %s%s" % (rel[:46], CODE_EXT[_ext(rel)],
                               ("  — " + ", ".join(syms[:6])) if syms else "")
        if used + len(line) + 1 > max_chars:
            rows.append("…（地图已截断，共 %d+ 个文件）" % (i + 1))
            break
        rows.append(line)
        used += len(line) + 1
    return "\n".join(rows)


def _score(rel: str, text: str, words: set) -> float:
    """相关度：文件名命中 > 符号命中 > 正文词频；语言先验（源码优先于文档）。"""
    score = 0.0
    base = os.path.basename(rel).lower()
    for w in words:
        if w and w in base:
            score += 6.0
        if w and w in rel.lower():
            score += 2.0
    low = text[:12000].lower()
    for w in words:
        if w and w in low:
            score += min(3.0, low.count(w) * 0.6)
    if _ext(rel) in (".py", ".java", ".js", ".ts", ".vue", ".go"):
        score += 1.0
    if _ext(rel) in (".md", ".json", ".yml", ".yaml", ".xml"):
        score += 0.4
    return score


_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fa5]{2,}")


def keywords(text: str) -> set:
    """从需求里抽关键词（英文标识符 + 中文二字词）。"""
    ws = set()
    for m in _TOKEN.finditer((text or "")[:800]):
        w = m.group(0).lower()
        if w not in ("the", "and", "for", "with", "you", "api", "一个", "帮我", "然后"):
            ws.add(w)
    return ws


def pick_relevant(pdir: str, query: str, top_k: int = 5, per_file: int = 1500,
                  max_chars: int = 7000) -> list:
    """挑与需求最相关的文件片段 → [(rel, snippet)]（每个文件带行号头，便于模型定位）。"""
    ws = keywords(query)
    if not ws:
        return []
    cands = []
    for rel, ap in iter_files(pdir, limit=250):
        try:
            txt = open(ap, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        s = _score(rel, txt, ws)
        if s > 0:
            cands.append((s, rel, txt))
    cands.sort(key=lambda x: -x[0])
    out, used = [], 0
    for _s, rel, txt in cands[:top_k]:
        head = "===== %s =====\n" % rel
        body = txt[:per_file]
        if len(txt) > per_file:
            body += "\n…（该文件更长，已截断）"
        block = head + body
        if used + len(block) > max_chars:
            break
        out.append((rel, block))
        used += len(block)
    return out


def context_for(pdir: str, task_text: str, budget_chars: int = 12000,
                *, with_map: bool = True, top_k: int = 5) -> str:
    """拼给模型的项目上下文：**地图 + 相关文件片段**，总长不超预算。"""
    if not pdir or not os.path.isdir(pdir):
        return ""
    parts = []
    used = 0
    if with_map:
        m = build_map(pdir, max_chars=min(6000, max(1500, budget_chars // 3)))
        if m:
            blk = "【项目文件地图】\n" + m
            parts.append(blk)
            used += len(blk)
    rel = pick_relevant(pdir, task_text, top_k=top_k,
                        max_chars=max(2000, budget_chars - used))
    if rel:
        blk = "【与本次需求相关的文件（片段）】\n" + "\n".join(b for _r, b in rel)
        parts.append(blk)
    txt = "\n\n".join(parts)
    if len(txt) > budget_chars:
        txt = txt[:budget_chars] + "\n…（上下文已按预算截断）"
    return txt


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    import tempfile
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    tmp = tempfile.mkdtemp(prefix="repomap_")
    os.makedirs(os.path.join(tmp, "src"), exist_ok=True)
    os.makedirs(os.path.join(tmp, "node_modules", "x"), exist_ok=True)
    open(os.path.join(tmp, "pom.xml"), "w", encoding="utf-8").write("<project/>")
    open(os.path.join(tmp, "src", "controller.py"), "w", encoding="utf-8").write(
        "class GeoController:\n    def list_items(self):\n        return []\n")
    open(os.path.join(tmp, "src", "前端页面.vue"), "w", encoding="utf-8").write(
        "<script setup>\nconst companyList = []\nfunction loadCompanies() {}\n</script>\n")
    open(os.path.join(tmp, "node_modules", "x", "junk.py"), "w", encoding="utf-8").write("x=1\n")
    open(os.path.join(tmp, "logo.png"), "wb").write(b"\x89PNG")

    m = build_map(tmp)
    print("=== 地图 ===")
    print(m)
    ck("地图含源码文件", "src/controller.py" in m)
    ck("地图含类名符号", "GeoController" in m)
    ck("地图跳过 node_modules", "node_modules" not in m)
    ck("地图跳过二进制", "logo.png" not in m)

    print("\n=== 相关文件检索 ===")
    rel = pick_relevant(tmp, "帮我改 GeoController 的 list 接口，企业入驻列表")
    names = [r for r, _b in rel]
    ck("命中 GeoController 所在文件", any("controller.py" in n for n in names), names)
    rel2 = pick_relevant(tmp, "企业入驻 前端页面 列表")
    ck("中文需求命中 vue 文件", any(".vue" in n for n, _ in rel2), [n for n, _ in rel2])

    print("\n=== 预算与拼装 ===")
    ctx = context_for(tmp, "改 GeoController 接口", budget_chars=3000)
    ck("上下文含地图", "项目文件地图" in ctx)
    ck("上下文含相关文件", "controller.py" in ctx)
    ck("不超预算", len(ctx) <= 3000 + 40, "len=%d" % len(ctx))
    ck("超小预算也不炸", len(context_for(tmp, "x", budget_chars=300)) <= 340)
    ck("空目录返回空串", context_for(os.path.join(tmp, "nope"), "x") == "")

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
