"""skillstore —— PASM 技能库（v0.16.2，对齐 SKILL.md 规范）。
=============================================================

技能 = 一份「该怎么做」的标准 SKILL.md，让 PASM 能按需自动检索并执行。
参考 WorkBuddy / QClaw 的技能与专家模式：技能独立成文件、可增删/导入/
查看，主程序不感知具体技能内容——它是 PASM 自我进化的「肌肉记忆外挂」。

技能文件两种布局（均支持）：
  1) 单文件：      <skills>/<name>.md
  2) 技能包：      <skills>/<name>/SKILL.md   （将来可携带 assets/scripts 等附件）

frontmatter（两个 --- 之间的 key: value 行，推荐字段）：
  name: video-script           # 技能名（简短：小写连字符或中文名）
  description: 一句话说明做什么 # 参与自动检索，尽量写清「何时用」
  keywords: 短视频,分镜脚本,口播 # 触发词，逗号分隔（中文/英文皆可）
  category: 创作                # 归类：创作 / 办公 / 开发 / 学习 / 效率 …
  version: 1.0.0               # 技能版本
  author: PASM                 # 作者
正文：给模型的「操作规程」：目标、输入约定、执行步骤、输出结构、质量红线。

两个来源（同名时用户技能覆盖内置）：
  - 内置技能：随程序发布（源码 skills/ 或 frozen 后的 _MEIPASS/skills）
  - 用户技能：DATA_DIR/skills/（运行时增删/导入，重启后仍在）
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from typing import List, Optional

try:
    from pasm_companion import DATA_DIR  # 复用同一数据目录
except Exception:                        # 独立导入兜底（测试用）
    DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".pasmstudio_dev")

USER_DIR = os.path.join(DATA_DIR, "skills")
os.makedirs(USER_DIR, exist_ok=True)

SKILL_VERSION = "1.0.0"
SKILL_AUTHOR = "PASM"

# 新技能标准模板（供「添加技能/技能开发」对话框展示与预填）
SKILL_TEMPLATE = """---
name: my-skill
description: 一句话说明这个技能做什么、什么时候该用它（这一句参与自动检索）
keywords: 触发词1,触发词2,触发词3
category: 通用
version: 1.0.0
author: 你
---

# 技能名

## 目标
（这个技能要达成什么结果）

## 何时使用
（在什么情况下才用本技能；什么情况下不该用，要明确写出，避免误触发）

## 执行步骤
1. （第一步…）
2. （第二步…）

## 输出结构
（成品长什么样：章节/表格/格式，缺一不可）

## 质量红线
- （不许做的事：编造、违规、假装已生成…）
"""


def _builtin_dir() -> str:
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(base, "skills"), os.path.join(os.path.dirname(base), "skills")):
        if os.path.isdir(p):
            return p
    return os.path.join(base, "skills")


# ---------------- 解析 ----------------
_FM_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.S)
_LINE_KV = re.compile(r"^([A-Za-z_][\w]*)\s*:\s*(.*)$")


def _parse_fm(text: str) -> dict:
    """解析 frontmatter（宽松：仅支持行式 key: value，支持逗号/顿号列表与简单引号）。"""
    meta: dict = {}
    m = _FM_RE.match(text)
    if not m:
        return meta, ""
    block = m.group(1)
    for ln in block.splitlines():
        kv = _LINE_KV.match(ln.strip())
        if not kv:
            continue
        k = kv.group(1).strip().lower()
        v = kv.group(2).strip()
        if not v:
            continue
        if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
            v = v[1:-1]
        meta[k] = v
    return meta, text[m.end():]


def _split_kw(v: str) -> List[str]:
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):      # yaml 列表 [a, b]
        v = v[1:-1]
    return [x.strip() for x in re.split(r"[,，、;；|]", v) if x.strip()]


def _parse(fn: str) -> Optional[dict]:
    """解析一个技能文件 → dict(name/description/keywords/category/version/author/
    when_to_use/body/source/path/is_pkg)。失败返回 None。"""
    try:
        text = open(fn, "r", encoding="utf-8").read()
    except Exception:
        return None
    meta, body = _parse_fm(text)
    if not body.strip():
        body = text.strip()
    name = (meta.get("name") or os.path.basename(fn)[:-3]).strip()
    if not name:
        return None
    src = "user" if fn.startswith(USER_DIR) else "builtin"
    # v0.25 三层类型：expert（专家域）/ skill（技能规程）/ connector（连接器·外部能力）
    stype = meta.get("type", "").strip().lower()
    if stype not in ("expert", "skill", "connector"):
        cat = meta.get("category", "")
        stype = ("expert" if "专家" in cat else
                 "connector" if "连接" in cat or "接口" in cat else "skill")
    return {
        "name": name,
        "description": meta.get("description", "").strip(),
        "keywords": _split_kw(meta.get("keywords", "")),
        "category": meta.get("category", "通用").strip() or "通用",
        "type": stype,
        "version": meta.get("version", "").strip(),
        "author": meta.get("author", "").strip(),
        "when_to_use": meta.get("when_to_use", meta.get("when", "")).strip(),
        "body": body.strip(),
        "path": fn,
        "source": src,
        "is_pkg": os.path.basename(fn) == "SKILL.md",
    }


def _files() -> List[str]:
    """扫描两个来源目录：*.md 与 <技能包>/SKILL.md（一层嵌套）。"""
    out: List[str] = []
    for d in (USER_DIR, _builtin_dir()):
        try:
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for f in names:
            p = os.path.join(d, f)
            if os.path.isfile(p) and f.endswith(".md"):
                out.append(p)
            elif os.path.isdir(p):
                sp = os.path.join(p, "SKILL.md")
                if os.path.isfile(sp):
                    out.append(sp)
    return out


def list_skills() -> List[dict]:
    """全部技能（用户技能按名覆盖内置；同名时保留用户版）。"""
    got: dict = {}
    for fn in _files():
        s = _parse(fn)
        if s:
            got[s["name"]] = s
    return [got[k] for k in sorted(got)]


def load(name: str) -> Optional[dict]:
    for s in list_skills():
        if s["name"] == name:
            return s
    return None


# ---------------- 增删 / 导入 ----------------
def _slug(name: str) -> str:
    s = re.sub(r"[^\w\u4e00-\u9fa5-]+", "-", name.strip()).strip("-") or "skill"
    return s[:48]


def add_skill(name: str, description: str, keywords: str = "", body: str = "",
              category: str = "用户添加", skill_type: str = "skill") -> str:
    """新增/覆盖一个用户技能（标准 SKILL.md frontmatter），返回文件路径。
    skill_type: expert（专家域）/ skill（技能规程）/ connector（连接器）。"""
    name = name.strip()[:48] or "未命名技能"
    slug = _slug(name)
    path = os.path.join(USER_DIR, slug + ".md")
    desc = (description.strip()[:160] or name)
    kw = "".join(k for k in (keywords.strip(),) if k)
    if not kw:
        # 从描述里拆出可检索词（2-4 字片段），保证新技能立即可被搜到
        cand = [w for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", desc)][:6]
        kw = ",".join(cand) if cand else name
    if not body.strip():
        body = f"请按用户需求把「{name}」做完，输出完整成品，不要只讲思路。"
    stype = skill_type if skill_type in ("expert", "skill", "connector") else "skill"
    content = (f"---\nname: {name}\ndescription: {desc}\n"
               f"keywords: {kw}\ncategory: {category.strip() or '用户添加'}\n"
               f"type: {stype}\n"
               f"version: {SKILL_VERSION}\nauthor: 你\n---\n\n"
               f"{body.strip()}\n")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def import_skill(path: str) -> tuple:
    """把一份外部 SKILL.md（或普通 md）导入用户技能库。
    返回 (ok, message)。技能名取自 frontmatter 的 name，否则用文件名。"""
    path = os.path.abspath(path)
    if not os.path.isfile(path):
        return False, "文件不存在。"
    try:
        text = open(path, "r", encoding="utf-8").read()
    except Exception:
        return False, "读取文件失败（请确认是 UTF-8 文本）。"
    meta, _ = _parse_fm(text)
    raw = (meta.get("name") or os.path.basename(path)[:-3]).strip()
    name = raw[:48] or "未命名技能"
    if not text.strip():
        return False, "文件是空的。"
    # 标准 SKILL.md 必须有 description（检索触发依赖它）
    if not meta.get("description"):
        return False, ("这不是一份标准技能文件：缺少 frontmatter 的 "
                       "description 字段。\n\n可先看「添加技能」对话框里的模板。")
    target = os.path.join(USER_DIR, _slug(name) + ".md")
    with open(target, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")
    return True, f"已导入技能《{name}》（{target}）。"


def export_text(name: str) -> Optional[str]:
    """技能完整原文（含 frontmatter）→ 用于「查看原文件/复制到别处」。"""
    s = load(name)
    if not s:
        return None
    try:
        return open(s["path"], "r", encoding="utf-8").read()
    except Exception:
        return None


def remove_skill(name: str) -> bool:
    s = load(name)
    if s and s["source"] == "user":
        try:
            os.remove(s["path"])
            return True
        except Exception:
            return False
    return False


# ---------------- 自动检索 ----------------
def search(text: str, top: int = 3) -> List[dict]:
    """按用户原话检索技能：命中 keywords/名称/描述/触发场景 → 加权打分。
    返回降序列表（score>0 才返回）。"""
    low = (text or "").lower().strip()
    if not low:
        return []

    def score(s: dict) -> float:
        sc = 0.0
        hay = (s.get("name", "") + " " + s.get("description", "")).lower()
        # 技能名出现 = 最强信号
        if s.get("name") and s["name"].lower() in low:
            sc += 10.0
        # 完整关键词命中
        for kw in s.get("keywords", []):
            k = (kw or "").lower().strip()
            if k and k in low:
                sc += 6.0
        # 描述里的 2-4 字词元命中（含触发场景 when_to_use）
        for w in re.findall(r"[\u4e00-\u9fa5]{2,4}|[A-Za-z]{3,}", hay):
            if len(w) >= 2 and w in low:
                sc += 1.2
                break
        when = (s.get("when_to_use") or "").lower()
        for w in re.findall(r"[\u4e00-\u9fa5]{2,4}", when):
            if w in low:
                sc += 1.0
                break
        return sc

    hits = [(s, score(s)) for s in list_skills()]
    hits = [(s, sc) for s, sc in hits if sc > 0]
    hits.sort(key=lambda x: (-x[1], x[0]["name"]))
    return [s for s, _ in hits[:top]]


def suggest(text: str) -> Optional[dict]:
    """高置信匹配：只有得分足够强才建议该技能（供干活模式自动执行）。"""
    for s in search(text, 1):
        # 关键词直接命中或技能名/短描述整词命中 → 视为可信
        low = (text or "").lower()
        if s["name"].lower() in low:
            return s
        for kw in s.get("keywords", []):
            if kw and kw.lower() in low:
                return s
    return None


def headline() -> str:
    """技能列表的一句话概览（用于能力介绍）。"""
    ss = list_skills()
    if not ss:
        return "（技能库为空：可在左侧「🎓 技能」里添加/导入技能）"
    return " · ".join(f"{s['name']}({s['category']})" for s in ss)


def meta_line(name: str) -> str:
    """技能的元信息行（版本/作者/归类/来源），用于查看面板。"""
    s = load(name)
    if not s:
        return ""
    parts = [f"📦 内置" if s["source"] == "builtin" else "🖊 用户自建"]
    if s.get("category"):
        parts.append(f"归类：{s['category']}")
    parts.append(f"版本：{s.get('version') or '—'}")
    if s.get("author"):
        parts.append(f"作者：{s['author']}")
    return " · ".join(parts)


def inspect(name: str) -> str:
    """把技能正文压缩成给模型看的形式（含依赖技能与触发说明）。"""
    s = load(name)
    if not s:
        return ""
    kws = "、".join(s.get("keywords", [])) or "无"
    when = s.get("when_to_use") or ""
    head = f"【技能 {name}】{s['description']}\n触发词：{kws}"
    if when:
        head += f"\n适用场景：{when}"
    parts = [head, s["body"]]
    # 正文里显式声明要调用的子技能 → 一并拼接（简单技能编排）
    for m in re.findall(r"调用技能[:：]?\s*([^\s，。,\n]+)", s["body"])[:3]:
        dep = load(m.strip())
        if dep and dep["name"] != name:
            parts.append(f"【配套技能 {dep['name']}】\n" + dep["body"])
    return "\n\n".join(parts)
