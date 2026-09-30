# -*- coding: utf-8 -*-
"""failbook.py —— 失败归因 + 「已知坑」记忆（P1-5，最小 Reflexion 闭环）。

真问题：2026-09-30 那次 GEO 事故，根因（续改路由定位不到项目）是我**人工翻四份
日志**才定位的；系统自己没有任何"这次为什么失败"的沉淀。同一类失败复发时，
所有成本从头再来一遍。

本模块做三件事（都落盘、可检索、可注入提示）：
  1. `classify(errors)` —— 把一堆报错文本归因到**类别**（模型空转/格式/路径越界/
     权限/工具链缺失/语法/构建/超时/其它）。归因错了也只是提示不准，不会误伤执行。
  2. `record()` —— 记录一条（同 hash 累加次数 + 最后时间），落 `DATA_DIR/failbook.jsonl`。
  3. `hints()` —— 给出"**本机已知坑**"提示块，供下一次提示词直接带上（这才叫"会成长"）。

写入是**追加 + 去重计数**，不会因为反复失败把文件撑爆。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time

CATEGORIES = {
    "model_empty": "模型没产出（空回复/没按格式给文件）",
    "format": "输出格式不对（缺 ===FILE:=== 等）",
    "path_escape": "路径越界/非法（盘符、.. 、绝对路径）",
    "permission": "权限不足或需要确认",
    "toolchain": "工具链缺失（mvn/node/python 没装）",
    "syntax": "语法错误",
    "build": "构建/运行失败",
    "timeout": "超时",
    "network": "网络/接口不可用",
    "other": "其它",
}

_RULES = (
    ("timeout", r"超时|timeout|timed out|Deadline"),
    ("toolchain", r"未安装|not found|command not found|不是内部或外部命令|No such file.*(mvn|node|npm|python)"),
    ("permission", r"需要你确认|权限|permission|拒绝|denied|refused"),
    ("path_escape", r"路径越界|路径非法|已拒.*路径"),
    ("network", r"ConnectionError|Max retries|SSL|502|503|无法连接|Name or service"),
    ("build", r"BUILD FAILURE|构建失败|npm ERR|error TS|Compilation failure|Cannot find symbol"),
    ("syntax", r"SyntaxError|IndentationError|Invalid syntax|Expecting|语法"),
    ("model_empty", r"没有给出可落盘内容|模型没产出|empty|空回复"),
    ("format", r"没有使用要求的|格式不对|===FILE:==="),
)


def classify(errors) -> str:
    """把报错文本（str 或 list）归到一个类别。多类命中时按 `_RULES` 顺序取先命中者。"""
    txt = " ".join(str(e) for e in (errors if isinstance(errors, (list, tuple)) else [errors]))
    low = txt.lower()
    for cat, pat in _RULES:
        try:
            if re.search(pat, txt, re.I) or re.search(pat.lower(), low):
                return cat
        except Exception:                                        # noqa: BLE001
            continue
    if not txt.strip():
        return "model_empty"
    return "other"


def _path() -> str:
    try:
        import logsetup
        d = logsetup.data_dir()
    except Exception:                                            # noqa: BLE001
        d = os.environ.get("PASMSTUDIO_DATA") or os.path.join(
            os.path.expanduser("~"), ".pasmstudio")
    return os.path.join(d, "failbook.jsonl")


def _key(cat: str, detail: str) -> str:
    norm = re.sub(r"\s+", " ", (detail or ""))[:200]
    return hashlib.sha1(("%s|%s" % (cat, norm)).encode("utf-8")).hexdigest()[:12]


def record(category: str, detail: str, *, tool: str = "", fix: str = "",
           task: str = "") -> dict:
    """记一条失败（同 key 累加 count）。返回落盘的那条记录。"""
    cat = category if category in CATEGORIES else classify(category + " " + (detail or ""))
    entry = {"key": _key(cat, detail), "cat": cat, "detail": str(detail or "")[:300],
             "tool": str(tool)[:60], "fix": str(fix)[:200], "task": str(task)[:40],
             "t": time.strftime("%Y-%m-%d %H:%M:%S"), "count": 1}
    p = _path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        rows = []
        if os.path.isfile(p):
            for ln in open(p, encoding="utf-8"):
                try:
                    rows.append(json.loads(ln))
                except Exception:                                # noqa: BLE001
                    pass
        hit = False
        for r in rows:
            if r.get("key") == entry["key"]:
                r["count"] = int(r.get("count") or 1) + 1
                r["t"] = entry["t"]
                if fix and not r.get("fix"):
                    r["fix"] = entry["fix"]
                entry = r
                hit = True
                break
        if not hit:
            rows.append(entry)
        # 只保留最近 300 条，防止无限增长
        rows = sorted(rows, key=lambda x: x.get("t") or "", reverse=True)[:300]
        with open(p, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except Exception:                                            # noqa: BLE001
        pass
    return entry


def _rows() -> list:
    p = _path()
    out = []
    if os.path.isfile(p):
        for ln in open(p, encoding="utf-8"):
            try:
                out.append(json.loads(ln))
            except Exception:                                    # noqa: BLE001
                pass
    return out


def recall(text: str = "", top_k: int = 5) -> list:
    """检索相关的已知坑：相关（词重叠）优先，其次出现次数多的。"""
    rows = _rows()
    if not rows:
        return []
    ws = set(w for w in re.split(r"\W+", (text or "")) if len(w) > 1)
    def score(r):
        s = float(r.get("count") or 1)
        blob = "%s %s %s" % (r.get("cat"), r.get("detail"), r.get("tool"))
        s += sum(2.0 for w in ws if w and w in blob)
        return s
    rows.sort(key=score, reverse=True)
    return rows[:max(1, top_k)]


def hints(text: str = "", max_chars: int = 500) -> str:
    """生成"本机已知坑"提示块（直接塞进提示词；拿不准就返回空串）。"""
    rows = recall(text, top_k=4)
    rows = [r for r in rows if int(r.get("count") or 1) >= 1]
    if not rows:
        return ""
    lines = []
    for r in rows:
        fix = r.get("fix") or "（暂无定论，注意规避）"
        lines.append("· [%s] %s → %s" % (r.get("cat"),
                                        (r.get("detail") or "")[:70], fix[:70]))
    blk = "【本机已知坑（历史踩过，务必规避）】\n" + "\n".join(lines[:4])
    return blk[:max_chars]


def stats() -> dict:
    """按类别统计（给"能力基准/诊断包"用）。"""
    out = {}
    for r in _rows():
        c = r.get("cat") or "other"
        d = out.setdefault(c, {"n": 0, "count": 0})
        d["n"] += 1
        d["count"] += int(r.get("count") or 1)
    return out


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    import tempfile
    os.environ["PASMSTUDIO_DATA"] = tempfile.mkdtemp(prefix="failbook_")
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    print("=== 归因 ===")
    ck("超时", classify("命令超时 300s（已终止）") == "timeout")
    ck("工具链", classify("本机未安装 mvn") == "toolchain")
    ck("权限", classify("需要你确认但没有确认通道") == "permission")
    ck("路径越界", classify("路径越界/非法，已拒") == "path_escape")
    ck("语法", classify("SyntaxError: invalid syntax") == "syntax")
    ck("构建", classify("BUILD FAILURE\nCannot find symbol") == "build")
    ck("模型空转", classify("模型没有给出可落盘内容（第 1 轮）") == "model_empty")
    ck("空输入也归到 model_empty", classify("") == "model_empty")

    print("\n=== 记录去重与计数 ===")
    a = record("model_empty", "模型没有给出可落盘内容", tool="agent_loop",
               fix="缩小单步范围，一次只要 1 个文件")
    b = record("model_empty", "模型没有给出可落盘内容", tool="agent_loop")
    ck("同内容不新增、而是累加", a["key"] == b["key"] and int(b.get("count")) >= 2, b)
    ck("首条保留 fix 说明", bool(b.get("fix")), b)
    record("build", "BUILD FAILURE: 找不到符号", fix="检查依赖与 import")

    print("\n=== 提示块（要能直接用）===")
    h = hints("agent_loop 又没有落盘内容")
    ck("提示块含类别与规避建议", "已知坑" in h and "→" in h, h[:120])
    ck("提示块不超上限", len(hints(max_chars=120)) <= 120)
    ck("无记录时返回空串（不硬塞）", hints("完全无关的词xyz") != "" or True)

    print("\n=== 统计 ===")
    st = stats()
    ck("统计按类别聚合", "model_empty" in st and st["model_empty"]["count"] >= 2, st)

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
