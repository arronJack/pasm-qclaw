# -*- coding: utf-8 -*-
"""聊天里的「多选项确认卡」。

用户要的是：遇到不确定的事，像 WorkBuddy 那样给出几个选项，让用户分步点选确认，
而不是替用户拍板。本模块只负责"选项请求"这一层的数据 / 渲染 / 判定，UI 落盘交给
pasm_companion。两条触发来源（与用户确认的方案一致 —— "两者结合"）：

  ① 模型在回复里用结构化标记 ``<<OPTIONS ... >>`` 主动请求弹选项；
  ② 应用侧在路由 / 执行前，对"破坏性操作"等清晰危险情形强制弹选项。

零 Qt 依赖：纯函数便于单元自检 + CI。
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field


# 标记：模型在正文里嵌一段 JSON 选项请求，应用把它从正文剥离、单独渲染成卡片。
_OPT_RE = re.compile(r"<<OPTIONS\s*(.*?)\s*>>", re.S | re.I)

# 提示词片段：告诉模型它可以在"真的拿不准"时主动弹选项卡（只加给聊天系统提示）。
OPTION_SCHEMA_HINT = (
    "当你确实拿不准该选哪条路、或下一步有好几个互斥的可行方案时，可以在回复里"
    "追加一段选项卡（不要每次都加，只在真的需要用户拍板时加）。格式：\n"
    "<<OPTIONS\n"
    '{"question": "你更想让我怎么处理？",\n'
    ' "options": [\n'
    '   {"key": "a", "label": "方案A：速成", "desc": "快但浅"},\n'
    '   {"key": "b", "label": "方案B：扎实", "desc": "慢但稳"}\n'
    " ]}\n"
    ">>\n"
    "卡片最多 4 个；label 简短、desc 一句说明；发出后这轮就等用户点选，不要再自己继续做。"
)


@dataclass
class OptionRequest:
    """一次选项请求：一个问题 + 2~6 个可点选项。"""
    question: str = ""
    options: list = field(default_factory=list)   # list[dict] {key,label,desc}

    def valid(self) -> bool:
        if not (self.question or "").strip():
            return False
        if len(self.options) < 2 or len(self.options) > 6:
            return False
        for o in self.options:
            if not (o.get("label") or "").strip():
                return False
        return True


def parse_options(text: str):
    """从模型正文里剥离选项请求。返回 ``(clean_text, OptionRequest | None)``。

    ``clean_text`` 已把 ``<<OPTIONS ...>>`` 整段去掉，可直接当正常回复上屏；
    解析失败 / 选项不合法 → fail-safe 当普通文本（返回 ``(text, None)``）。
    """
    if not text:
        return text, None
    m = _OPT_RE.search(text)
    if not m:
        return text, None
    raw = m.group(1).strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)      # 去掉可能残留的 ```json 围栏
    raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
    except Exception:
        return text, None                            # 坏 JSON → 当普通文本，不弹卡
    req = OptionRequest(
        question=str(obj.get("question", "")).strip(),
        options=[{"key": str(o.get("key", "")).strip(),
                  "label": str(o.get("label", "")).strip(),
                  "desc": str(o.get("desc", "")).strip()}
                 for o in (obj.get("options") or [])])
    if not req.valid():
        return text, None
    clean = (text[:m.start()] + text[m.end():]).strip()
    return clean, req


def render_cards(req: OptionRequest) -> str:
    """把选项请求渲染成聊天里可点击的卡片 HTML（``pasm://option/<i>``）。"""
    if not req.valid():
        return ""
    parts = ["<div style='margin:6px 0 2px;color:#0F172A;font-weight:bold;'>"
             + html.escape(req.question) + "</div>"]
    for i, o in enumerate(req.options):
        lbl = html.escape(o["label"])
        desc = html.escape(o["desc"])
        parts.append(
            "<div style='margin:4px 0;padding:8px 10px;border:1px solid #CBD5E1;"
            "border-radius:8px;background:#F8FAFC;'>"
            "<a href='pasm://option/%d' style='color:#185FA5;text-decoration:none;"
            "font-weight:bold;'>▸ %s</a>" % (i, lbl))
        if desc:
            parts.append("<div style='color:#475569;font-size:12px;margin-top:2px;'>%s</div>" % desc)
        parts.append("</div>")
    parts.append("<div style='color:#94A3B8;font-size:11px;margin-top:2px;'>"
                 "点上面的任意一项，我就按你选的继续。</div>")
    return "".join(parts)


# ---- 应用侧强制弹选项：只覆盖"最清晰、最安全"的危险情形，宁可少弹不要乱弹 ----

# 破坏性动词 + 明确对象（14 字内）：只认**具体文件/系统对象**名词，
# 不认"这/那/整个"等代词 —— 否则"删掉这句话里的错别字"会被误判成要删文件。
_RE_DESTRUCTIVE = re.compile(
    r"(删|清[空理]|格式化|卸载|销毁|永久删|彻底删|覆盖).{0,14}?"
    r"(文件|目录|文件夹|桌面|项目|仓库|工程|缓存|聊天记录|数据|磁盘|盘|回收站|垃圾"
    r"|账号|配置|设置|软件|应用|程序|系统|库)",
    re.I)


def should_offer(text: str, ctx: dict | None = None) -> OptionRequest | None:
    """应用侧判定：这句话要不要先弹选项再动手。

    只覆盖**最清晰、最安全**的情形（破坏性操作前强制确认范围 / 方式），
    返回 ``OptionRequest`` 表示"该弹"，``None`` 表示"直接照旧处理"。
    误弹比不弹更烦 —— 所以判据收得很紧（动词 + 明确对象）。
    """
    ctx = ctx or {}
    t = (text or "").strip()
    if not t:
        return None
    # ① 破坏性操作：先确认范围 / 方式，而不是一声不响去删。
    if _RE_DESTRUCTIVE.search(t):
        return OptionRequest(
            question="这是破坏性操作，确认一下你想怎么处理：",
            options=[
                {"key": "scope", "label": "先告诉我具体要删 / 清哪些",
                 "desc": "我列出候选清单，你再点；这步不会先动手"},
                {"key": "preview", "label": "先只预览，不真删",
                 "desc": "我先把会受影响的内容列出来给你看"},
                {"key": "ok", "label": "就按你说的来",
                 "desc": "我按你的原话执行（仍会走授权确认）"},
            ])
    return None


def selftest() -> int:
    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            print("  ✗ %s  %s" % (name, extra))

    # 解析：正常（模型嵌选项卡）
    txt = ("好的，下面有几种做法：\n<<OPTIONS\n"
           '{"question":"你想用哪种？",'
           '"options":[{"key":"a","label":"方案A：速成","desc":"快但浅"},'
           '{"key":"b","label":"方案B：扎实","desc":"慢但稳"}]}\n>>\n'
           "你挑一个。")
    clean, req = parse_options(txt)
    check("剥离后正文不含标记", "<<" not in clean and "OPTIONS" not in clean, repr(clean[:20]))
    check("解析出 2 个选项", req is not None and len(req.options) == 2, str(req))
    check("question 正确", req is not None and "哪种" in req.question)

    # 渲染
    h = render_cards(req)
    check("渲染含 pasm://option/0", "pasm://option/0" in h)
    check("渲染含 pasm://option/1", "pasm://option/1" in h)
    check("渲染不残留 <<OPTIONS", "<<OPTIONS" not in h and ">>" not in h)

    # 无标记
    c2, r2 = parse_options("普通回复，没有选项")
    check("无标记返回 None", r2 is None and c2 == "普通回复，没有选项")

    # 只有一个选项 → 不合法，不弹
    bad = parse_options("x\n<<OPTIONS\n{\"question\":\"?\","
                        '"options":[{"key":"a","label":"只有A"}]}\n>>')
    check("单选项不弹", bad[1] is None)

    # 坏 JSON → fail-safe 当普通文本
    bad2 = parse_options("y\n<<OPTIONS\n{坏 json\n>>")
    check("坏 JSON 不当卡", bad2[1] is None and "坏 json" in bad2[0])

    # 应用侧：破坏性 + 明确对象 → 触发
    d = should_offer("帮我删除桌面上的旧项目目录")
    check("破坏性+对象触发弹卡", d is not None and "破坏性" in d.question, str(d))
    d2 = should_offer("请清空回收站")
    check("清空回收站触发", d2 is not None)
    # 应用侧：破坏性但无明确对象（编辑文本里的"删"）→ 不误弹
    nd = should_offer("请删除这句话里多余的逗号")
    check("删错别字不误弹", nd is None)
    nd2 = should_offer("帮我写一首关于离别的诗")
    check("普通请求不触发", nd2 is None)
    # 应用侧：选"就按你说的来"的 key 可被识别
    check("破坏性卡片含 ok 项", d is not None and any(o["key"] == "ok" for o in d.options))

    print("option_prompt 自检：%d 项，%s"
          % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
