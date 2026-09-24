#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""repo_metrics —— 目录/仓库的**事实卡**（量化 + 风险素材），v0.31.7。

为什么要有它（真机观察，2026-09-24）：
  让小 U 分析 `D:\Code副\business` 时，它能得出**正确**结论（"骨架级重构"），但
  · 给不出任何量化（文件数？模块数？代码行数？表数？全是形容词）；
  · 不主动提风险（未达生产可用、依赖未接、数据层未迁）。
  根因不是"模型不会说话"，而是**我们只喂了原始清单，让它自己去数** —— 4B 模型
  数不准、也懒得数。正确做法：**工具先把数字算准，作为"事实"喂进提示词**，
  并要求回答必须引用这些数字 + 必须给风险小节。这样量化与风险**不依赖模型自觉**。

产出三件：
  scan(path)        → 指标 dict（文件数/按类型/代码行/目录数/最大文件/依赖/SQL 建表/缺失项）
  facts_block(m)    → 给提示词用的"事实卡"文本（模型直接引用，不需自己数）
  risk_hints(m)     → 基于事实的风险素材（"没看到 Nacos 依赖""测试文件 0 个"…）

约束：零 Qt、零第三方依赖；只读；大文件/二进制跳过；扫描上限防止卡死。

自检：python repo_metrics.py --selftest
"""
from __future__ import annotations

import json
import os
import re
import sys

__all__ = ["scan", "facts_block", "risk_hints", "selftest"]

_TEXT_EXT = {".py", ".php", ".java", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".vue",
             ".go", ".rs", ".c", ".h", ".cpp", ".cs", ".rb", ".kt", ".swift",
             ".sql", ".html", ".css", ".scss", ".less", ".sh", ".bat", ".ps1",
             ".yml", ".yaml", ".json", ".xml", ".toml", ".ini", ".cfg", ".conf",
             ".md", ".txt", ".env", ".properties"}
_SKIP_DIRS = {".git", "node_modules", "vendor", "__pycache__", "dist", "build",
              ".venv", "venv", "target", ".idea", ".vscode", "storage", "runtime"}
_MAX_FILE_BYTES = 2 * 1024 * 1024          # 单文件最大计入行数（2MB）
_MAX_SCAN_FILES = 20000                    # 扫描上限（防卡死）
# v0.31.7：**内容级统计（行数/占位标记/SQL 建表）只采样前 N 个文本文件** ——
#   真机上 business 有 17407 个文件，全文读要几十秒，会把"分析"变成"卡住"。
#   采样值足以支撑量化结论，且会在事实卡里标明是近似值（诚实优先）。
_CONTENT_SAMPLE = 800
_PLACEHOLDER = re.compile(r"TODO|FIXME|XXX|占位|待接|待实现|未实现|stub|not implemented",
                          re.I)


def _read(p: str, limit: int = 400000) -> str:
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read(limit)
    except Exception:
        return ""


def scan(path: str, max_files: int = _MAX_SCAN_FILES) -> dict:
    """扫一个目录，产出量化事实。只读、带上限，绝不抛。"""
    m = {"path": os.path.abspath(path), "exists": os.path.isdir(path),
         "files": 0, "dirs": 0, "bytes": 0, "by_ext": {}, "loc": 0,
         "largest": [], "sql_tables": 0, "todos": 0, "tests": 0,
         "deps": {}, "markers": {}, "truncated": False}
    if not m["exists"]:
        return m
    stack = [m["path"]]
    seen = 0
    _content = {"n": 0}          # 已做内容级统计的文本文件数（采样上限 _CONTENT_SAMPLE）
    while stack:
        d = stack.pop()
        try:
            with os.scandir(d) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        if e.name in _SKIP_DIRS:
                            continue
                        m["dirs"] += 1
                        stack.append(e.path)
                        continue
                    if not e.is_file(follow_symlinks=False):
                        continue
                    seen += 1
                    if seen > max_files:
                        m["truncated"] = True
                        stack = []
                        break
                    m["files"] += 1
                    ext = os.path.splitext(e.name)[1].lower()
                    m["by_ext"][ext] = m["by_ext"].get(ext, 0) + 1
                    try:
                        sz = e.stat().st_size
                    except Exception:
                        sz = 0
                    m["bytes"] += sz
                    m["largest"].append((sz, e.path))
                    if ext in _TEXT_EXT and 0 < sz <= _MAX_FILE_BYTES \
                            and _content["n"] < _CONTENT_SAMPLE:
                        _content["n"] += 1
                        txt = _read(e.path)
                        m["loc"] += txt.count("\n") + (1 if txt and not txt.endswith("\n") else 0)
                        m["todos"] += len(_PLACEHOLDER.findall(txt))
                        if ext == ".sql":
                            m["sql_tables"] += len(re.findall(r"create\s+table", txt, re.I))
                    base = e.name.lower()
                    if ("test" in base or "spec" in base) and ext in _TEXT_EXT:
                        m["tests"] += 1
                    for mk in ("readme.md", "docker-compose.yml", "dockerfile",
                               ".gitlab-ci.yml", "pom.xml", "composer.json",
                               "package.json", "requirements.txt"):
                        if base == mk:
                            m["markers"][mk] = True
        except Exception:
            continue
    m["largest"] = sorted(m["largest"], reverse=True)[:5]
    m["largest"] = [(s, os.path.relpath(p, m["path"])) for s, p in m["largest"]]
    m["by_ext"] = dict(sorted(m["by_ext"].items(), key=lambda kv: -kv[1])[:12])
    m["sampled"] = _content["n"]
    m["sampled_partial"] = bool(_content["n"] >= _CONTENT_SAMPLE)
    # 依赖清单（只取结构化字段，避免把 lock 文件读进来）
    for name, keys in (("composer.json", ("require",)),
                       ("package.json", ("dependencies",)),
                       ("requirements.txt", None)):
        fp = os.path.join(m["path"], name)
        if not os.path.isfile(fp):
            continue
        if keys:
            try:
                d = json.loads(_read(fp, 200000) or "{}")
                got = {}
                for k in keys:
                    if isinstance(d.get(k), dict):
                        got.update({str(a): str(b) for a, b in list(d[k].items())[:40]})
                if got:
                    m["deps"][name] = got
            except Exception:
                pass
        else:
            lines = [ln.strip() for ln in _read(fp, 100000).splitlines()
                     if ln.strip() and not ln.startswith("#")]
            if lines:
                m["deps"][name] = {ln.split("=")[0].split(">")[0].strip(): "" for ln in lines[:40]}
    return m


def facts_block(m: dict, note: str = "") -> str:
    """把量化事实渲染成"事实卡"（直接拼进提示词，模型只需引用、不需自己数）。"""
    if not m.get("exists"):
        return "【事实卡】目标目录不存在或不可读。"
    out = ["【事实卡（工具实测，可直接引用；不要再自己估）】"]
    out.append("· 路径：%s" % m["path"])
    out.append("· 文件 %d 个｜目录 %d 个｜磁盘占用 %.2f MB%s"
               % (m["files"], m["dirs"], m["bytes"] / 1048576.0,
                  "（已截断，实际更多）" if m.get("truncated") else ""))
    _smp = ("（按前 %d 个文本文件采样，属近似值）" % m.get("sampled", 0)) \
        if m.get("sampled_partial") else ""
    out.append("· 文本代码总行数（近似）：%d 行%s" % (m.get("loc", 0), _smp))
    if m.get("sampled_partial") and m.get("sql_tables"):
        out.append("　（其中 SQL 建表 %d 张，同样来自采样，实际可能更多）" % m["sql_tables"])
    if m.get("by_ext"):
        out.append("· 文件类型（前 12）：" + "、".join(
            "%s %d" % (k or "(无扩展名)", v) for k, v in m["by_ext"].items()))
    if m.get("sql_tables"):
        out.append("· SQL 建表语句：%d 张表（来自 .sql 文件）" % m["sql_tables"])
    if m.get("tests"):
        out.append("· 测试文件：%d 个" % m["tests"])
    if m.get("todos"):
        out.append("· 占位/未完成标记（TODO/FIXME/占位 等）：出现 %d 次" % m["todos"])
    if m.get("largest"):
        out.append("· 最大文件：" + "、".join(
            "%s %.1f MB" % (p, s / 1048576.0) for s, p in m["largest"]))
    if m.get("deps"):
        for name, d in m["deps"].items():
            keys = list(d.keys())[:15]
            out.append("· 依赖清单 %s（%d 项，前 15）：%s"
                       % (name, len(d), "、".join(keys)))
    have = [k for k in ("readme.md", "docker-compose.yml", "dockerfile",
                        "pom.xml", "composer.json", "package.json",
                        "requirements.txt", ".gitlab-ci.yml")
            if m["markers"].get(k)]
    miss = [k for k in ("readme.md", "docker-compose.yml", "dockerfile",
                        "pom.xml", "composer.json", "package.json",
                        "requirements.txt", ".gitlab-ci.yml")
            if not m["markers"].get(k)]
    if have:
        out.append("· 顶层已有：" + "、".join(have))
    if miss:
        out.append("· 顶层未发现：" + "、".join(miss))
    if note:
        out.append("· 备注：" + note)
    return "\n".join(out)


def risk_hints(m: dict) -> list:
    """基于事实生成"风险/差距"素材（**按项目类型条件触发，宁缺勿滥**）。

    ⚠️ v0.31.7 修正：早先版本对 Laravel 单体也报"缺 Nacos/Sentinel/网关"——
    那是**误报**（单体项目本就不该有）。微服务类缺口只在"确实像 JVM/微服务项目"
    时才提（有 pom.xml / 依赖里出现 spring-cloud、nacos、dubbo、gateway 等）。
    """
    hints = []
    if not m.get("exists"):
        return ["目标目录不可读，无法评估"]
    deps_all = " ".join(k for d in m.get("deps", {}).values() for k in d).lower()
    files = max(1, m.get("files", 0))

    # —— 通用风险（任何项目都成立）——
    if m.get("tests", 0) == 0:
        hints.append("未发现任何测试文件（test/spec）—— 回归风险无法量化")
    if not m.get("markers", {}).get("readme.md"):
        hints.append("顶层未发现 README —— 上手/交接成本高")
    if m.get("todos", 0) > 0:
        hints.append("发现 %d 处 TODO/FIXME/占位标记 —— 存在未完成实现" % m["todos"])
    if m.get("todos", 0) and m["todos"] / files > 0.2:
        hints.append("占位标记相对文件数偏高（%d/%d）—— 疑似骨架/半成品"
                     % (m["todos"], m["files"]))
    if m.get("sql_tables", 0) > 0:
        hints.append("SQL 里有 %d 张表定义 —— 需核对实体/迁移是否与之一致"
                     % m["sql_tables"])
    # 大体积二进制资产（真机实录：business 里 1761 张 jpg + 698 张 png）
    if m.get("bytes", 0) > 200 * 1048576:
        hints.append("目录已达 %.0f MB —— 注意大体积二进制资产（图片/上传件）"
                     "会拖慢仓库与备份，建议不入库或走对象存储/LFS"
                     % (m["bytes"] / 1048576.0))

    # —— 微服务类缺口：只在"像 JVM/微服务项目"时才提 ——
    _ms_dep = any(k in deps_all for k in ("spring-cloud", "spring-boot", "nacos",
                                          "sentinel", "dubbo", "gateway"))
    if m.get("markers", {}).get("pom.xml") or _ms_dep:
        for kw, label in (("nacos", "Nacos 注册/配置中心"),
                          ("sentinel", "Sentinel 熔断限流"),
                          ("gateway", "网关"),
                          ("mysql", "MySQL 驱动")):
            if kw not in deps_all:
                hints.append("依赖清单里未看到 %s —— 若定位是生产级微服务，属缺口" % label)
    elif "laravel" in deps_all or "php" in deps_all:
        hints.append("这是 PHP/Laravel 单体项目（依赖清单可证）—— 不存在注册中心/网关这类"
                     "微服务组件，属正常，不要按微服务标准挑缺")
    return hints


# ---------------------------------------------------------------- 自检
def selftest() -> int:
    import tempfile
    ok = fail = 0

    def chk(name, cond, extra=""):
        nonlocal ok, fail
        if cond:
            ok += 1
        else:
            fail += 1
            print("  ✗ %s %s" % (name, extra))

    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "src"))
        os.makedirs(os.path.join(td, "node_modules"))
        with open(os.path.join(td, "src", "a.py"), "w", encoding="utf-8") as f:
            f.write("print('x')\n# TODO: 待实现\n")
        with open(os.path.join(td, "src", "b.sql"), "w", encoding="utf-8") as f:
            f.write("CREATE TABLE t1 (id int);\ncreate table t2(id int);\n")
        with open(os.path.join(td, "composer.json"), "w", encoding="utf-8") as f:
            json.dump({"require": {"laravel/framework": "5.5.*", "php": ">=7.0"}}, f)
        with open(os.path.join(td, "node_modules", "junk.js"), "w", encoding="utf-8") as f:
            f.write("x" * 10)
        with open(os.path.join(td, "README.md"), "w", encoding="utf-8") as f:
            f.write("# hi\n")
        m = scan(td)
        chk("扫到文件", m["files"] == 4, m["files"])
        chk("跳过 node_modules", all("node_modules" not in p for _s, p in m["largest"]))
        chk("SQL 建表 2 张", m["sql_tables"] == 2, m["sql_tables"])
        chk("TODO 计数 ≥1", m["todos"] >= 1, m["todos"])
        chk("composer 依赖读到", "laravel/framework" in " ".join(
            m["deps"].get("composer.json", {}).keys()))
        chk("识别 README", bool(m["markers"].get("readme.md")))
        fb = facts_block(m)
        chk("事实卡含文件数", "文件 4 个" in fb, fb[:80])
        chk("事实卡含建表数", "2 张表" in fb)
        chk("事实卡含依赖", "composer.json" in fb)
        rh = risk_hints(m)
        chk("风险-无测试", any("测试" in x for x in rh), rh)
        chk("风险-占位", any("TODO" in x or "未完成" in x for x in rh))
        chk("风险-单体不误报微服务(composer+php 无 pom)", not any("Nacos" in x for x in rh), rh)
        chk("风险-单体已声明", any("Laravel" in x or "单体" in x for x in rh), rh)
        chk("不存在的目录", scan(os.path.join(td, "nope"))["exists"] is False)
        chk("空输入不炸", facts_block({}) == "【事实卡】目标目录不存在或不可读。")

    print("repo_metrics 自检：%d/%d 通过" % (ok, ok + fail))
    return 0 if not fail else 1


if __name__ == "__main__":
    raise SystemExit(selftest())
