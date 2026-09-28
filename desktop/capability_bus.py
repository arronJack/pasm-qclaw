# -*- coding: utf-8 -*-
"""能力协作总线（v0.31.9）—— 让所有栏目/模块「按需关联、不混乱」的中枢。

解决的核心问题：
  · 此前聊天 / 语音 / 远程 / 团队 各自有一套意图识别，能力散落在 `_agent_run`
    的几十个 `if kind ==` 分支里，彼此不知道对方能干什么 → 系统"死"的。
  · 本模块把**每一种能力**登记成一张目录（CAPABILITIES），并提供一个统一的
    `route()`（单意图路由）+ `fast_route()`（语音免界面快指令）+ `orchestrate()`
    （多意图编排）+ `catalog_text()`（自我描述"我会什么"）。
  · 任何入口（唤醒语音 / 聊天 / 手机远程 / 团队）都通过这里发现与调用能力，
    于是"打开音乐→查天气→提醒我开会"这种跨模块协作就能一句话串起来，
    且只在意图明确时才激活对应模块，**不混乱**。

设计守则（避免把系统搞乱）：
  · 只做"按需激活"：单意图用 route 命中即执行；多意图才编排，且每个子能力
    必须各自命中才串，命中不了的就如实说不会，绝不硬凑。
  · fast_route 只放行**安全、秒级、非破坏性**的能力（开应用 / 查天气 / 看邮箱 /
    搜索 / 提醒 / 看电脑状态），用于"关掉聊天框也能语音直接办"。
  · 重活（开发 / 出图 / 出视频 / 写文档）不进 fast_route，仍走正常管线带确认墙。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

# 尽量轻量：回忆路由（自我成长经验）可用就用，不可用就退化为关键词。
# 桌面运行时 memory_layers 是顶层模块；打包/子包环境再退到 pasm.cognitive。
try:
    import memory_layers as _ML
except Exception:                                   # noqa: BLE001
    try:
        import pasm.cognitive.memory_layers as _ML
    except Exception:                               # noqa: BLE001
        _ML = None

try:
    import agent_tools as AT
except Exception:                                   # noqa: BLE001
    AT = None


# ----------------------------------------------------------------------
# 1) 能力目录（单一事实来源）
# ----------------------------------------------------------------------
@dataclass
class Capability:
    kind: str
    label: str
    domain: str
    desc: str
    keywords: Tuple[str, ...] = ()
    fast: bool = False          # 是否允许"语音免界面直接办"（安全、秒级、非破坏）
    page: Optional[str] = None  # 重活需要切到的栏目；None=留在当前/聊天
    read_only: bool = False     # 只读（查），不会改电脑


# domain 分组，便于 catalog_text 自我描述。
CAPABILITIES: List[Capability] = [
    # —— 系统动作 / 快捷（fast）——
    Capability("openapp", "打开应用", "系统控制",
               "打开电脑上的应用/软件（浏览器、微信、网易云、记事本…）",
               ("打开", "启动", "运行", "开一下", "开个"), fast=True, read_only=False),
    Capability("openpath", "打开文件/文件夹", "系统控制",
               "用系统默认方式打开某个文件或文件夹",
               ("打开文件夹", "打开目录", "打开文件", "打开盘"), fast=True),
    Capability("websearch", "联网搜索", "信息获取",
               "打开浏览器搜索某个问题/内容",
               ("搜索", "搜一下", "搜一", "查一下", "百度", "谷歌"), fast=True),
    Capability("weather", "查天气", "信息获取",
               "查实时天气（免 key，直接给答案）",
               ("天气", "气温", "几度", "温度", "下雨", "降温"), fast=True, read_only=True),
    Capability("mail_check", "查看邮箱", "信息获取",
               "查看/收取邮件",
               ("邮箱", "邮件", "收件箱", "查邮件"), fast=True, read_only=True),
    Capability("mail_search", "搜索邮件", "信息获取",
               "在邮件里搜索某个关键词",
               ("搜邮件", "找邮件"), fast=True, read_only=True),
    Capability("remind_add", "设提醒/闹钟", "个人助理",
               "设定一个提醒、闹钟或定时任务",
               ("提醒", "备忘", "闹钟", "定时"), fast=True),
    Capability("cal_list", "看日历", "个人助理",
               "查看日历安排", ("日历", "日程", "安排"), fast=True, read_only=True),
    Capability("sysinfo", "看电脑配置", "系统查看",
               "查看本机配置与现状（只读）",
               ("电脑配置", "系统信息", "什么配置", "电脑现状"), fast=True, read_only=True),
    Capability("procs", "看进程占用", "系统查看",
               "查看当前进程与资源占用（只读）",
               ("进程", "什么程序在跑", "占用", "卡"), fast=True, read_only=True),
    Capability("secchk", "安全体检", "系统查看",
               "做一次安全状况体检（只读）",
               ("安全", "体检", "漏洞", "扫描安全"), fast=True, read_only=True),
    Capability("sysops_report", "操作台账", "系统查看",
               "查看我最近真实执行过的系统操作记录",
               ("操作台账", "你做过什么", "执行记录", "干了什么"), fast=True, read_only=True),
    Capability("connector_status", "接入状态", "系统查看",
               "查看第三方接入（四个通道）状态",
               ("接入状态", "连接状态", "通道"), fast=True, read_only=True),
    Capability("mcp_status", "MCP 状态", "系统查看",
               "查看 MCP 连接器与工具清单",
               ("mcp", "工具清单"), fast=True, read_only=True),
    Capability("remote_status", "远程状态", "系统查看",
               "查看手机等远程端状态", ("远程", "手机端"), fast=True, read_only=True),

    # —— 重活 / 需界面（不进 fast_route）——
    Capability("readfolder", "分析文件夹/项目", "生产创作",
               "深入分析一个代码/项目文件夹，给出量化报告",
               ("分析", "研究", "看看", "体检"), read_only=True),
    Capability("readfile", "读文件", "生产创作",
               "读取并理解一个文件的内容", ("读", "看文件"), read_only=True),
    Capability("project", "开发/写代码/做系统", "生产创作",
               "真正动手做项目：写代码、建系统、做网站/程序",
               ("开发", "写代码", "做网站", "做系统", "做程序", "搭一个", "建一个",
                "编程", "脚本", "项目"), page="work"),
    Capability("image", "生成图片", "生产创作",
               "用文生图生成图片", ("图", "画", "配图"), page="work"),
    Capability("video", "生成视频", "生产创作",
               "生成一段视频", ("视频", "短片"), page="work"),
    Capability("manga", "生成漫剧", "生产创作",
               "生成一部漫剧", ("漫剧", "漫画"), page="work"),
    Capability("ad", "做广告/海报", "生产创作",
               "做一张广告主视觉 / 海报 + 方案",
               ("广告", "海报", "主视觉", "宣传图", "设计海报"), page="work"),
    Capability("doc", "写文档/报告", "生产创作",
               "写一份文档、报告或 PPT", ("文档", "报告", "ppt", "pptx", "总结"), page="work"),
    Capability("tableana", "分析表格", "生产创作",
               "分析一份 Excel/CSV 表格", ("表格", "excel", "csv"), read_only=True),
    Capability("skill", "运行技能", "智能体",
               "运行一个已安装的技能", ("技能", "运行技能"), page="skill"),
    Capability("team_auto", "团队自发协作", "智能体",
               "让团队智能体自发协作完成一件事", ("团队", "协作", "分配"), page="team"),
    Capability("sysops_clean", "清理垃圾", "系统控制",
               "扫描并清理电脑垃圾（先确认）", ("清垃圾", "清理", "打扫"), page="task"),
    Capability("evolve_save", "沉淀技能", "智能体",
               "把刚做完的活沉淀成一个可复用技能", ("沉淀", "存成技能"), page="auto"),
]

_KIND2CAP = {c.kind: c for c in CAPABILITIES}
# fast_route 仅放行这张白名单（安全、秒级、非破坏、不弹确认墙）。
_FAST_KINDS = {c.kind for c in CAPABILITIES if c.fast}

# 多意图切分连词（用于 orchestrate）。
_SPLIT_RE = re.compile(r"[，,。；;]|并且|而且|然后|再|顺便|之后|接着|以及|和|顺便|另外|还有")


# ----------------------------------------------------------------------
# 2) 快指令规则（fast_route / route 共用）—— 有序，首命中即收
# ----------------------------------------------------------------------
# 每条：(正则, kind, 提取 payload 的函数)
def _after(text: str, pat: re.Pattern) -> str:
    m = pat.search(text)
    return text[m.end():].strip(" ，,。；;：:") if m else ""


def _clean_q(s: str) -> str:
    """清洗搜索词：去掉引导废词（一下/一个/查/帮我/看看）。"""
    return re.sub(r"^(一下|一个|下|查|帮我|看看|我想|我要|搜)\s*", "", (s or "")).strip()


_FAST_RULES: List[Tuple[re.Pattern, str, Callable[[str], str]]] = [
    # 天气（先于"查一下"，避免被 websearch 抢）
    (re.compile(r"天气|气温|几度|温度|下雨|降温|升温|空气质量"),
     "weather", lambda t: ""),
    # 邮箱
    (re.compile(r"邮箱|邮件|收件箱"), "mail_check", lambda t: ""),
    # 搜索（"查一下X"但 X 不是天气/邮件 → 搜索）
    (re.compile(r"搜索|搜一下|搜一|百度一下|谷歌一下|搜下"),
     "websearch", lambda t: _clean_q(_after(t, re.compile(r"搜索|搜一下|搜一|百度一下|谷歌一下|搜下")))),
    # 提醒
    (re.compile(r"提醒我|提醒一下|设个?提醒|备忘|闹钟|定时(?!任务)"),
     "remind_add", lambda t: t),
    # 日历
    (re.compile(r"日历|我的日程|今天有?什么安排|安排"),
     "cal_list", lambda t: ""),
    # 系统查看类
    (re.compile(r"电脑配置|系统信息|什么配置|电脑现状|本机"),
     "sysinfo", lambda t: ""),
    (re.compile(r"进程|什么程序在跑|资源占用|哪个程序卡|卡死了"),
     "procs", lambda t: ""),
    (re.compile(r"安全体检|漏洞|扫一下安全|安全状况"),
     "secchk", lambda t: ""),
    (re.compile(r"操作台账|你(最近)?做过什么|执行记录|你干了什么"),
     "sysops_report", lambda t: ""),
    (re.compile(r"接入状态|连接状态|通道状态"),
     "connector_status", lambda t: ""),
    (re.compile(r"mcp|工具清单|连接器状态"),
     "mcp_status", lambda t: ""),
    # 打开文件/文件夹（先于"打开应用"）
    (re.compile(r"打开(文件夹|目录|文件)|打开[盘符]|打开\s+[A-Za-z]:"),
     "openpath", lambda t: _extract_path(t)),
    # 打开应用（含"放音乐/听歌"→ 音乐）
    (re.compile(r"放.{0,3}音乐|听.{0,3}音乐|听歌|来首歌|放首歌|播放音乐"),
     "openapp", lambda t: "音乐"),
    (re.compile(r"打开|启动|运行|开一下|开个"),
     "openapp", lambda t: _extract_open_target(t)),
]


def _extract_path(text: str) -> str:
    m = re.search(r"[A-Za-z]:[\\/][^\s，。；;：:]*", text)
    if m:
        return m.group(0).rstrip("。；;：:")
    if AT is not None:
        p = AT.resolve_path(text)
        if p:
            return p
    return ""


def _extract_open_target(text: str) -> str:
    # 取动词后的名词（应用名）。去掉引导词。
    m = re.search(r"(?:打开|启动|运行|开一下|开个)\s*[:：]?\s*([一-龥A-Za-z0-9_．.\s]{1,12})",
                  text)
    if m:
        return m.group(1).strip(" 的了个吗呢吧。，,；;：:")
    return text.strip()


# ----------------------------------------------------------------------
# 3) 路由
# ----------------------------------------------------------------------
def fast_route(text: str) -> Optional[Tuple[str, str]]:
    """语音免界面快指令：命中安全能力就返回 (kind, payload)，否则 None。

    用于"关掉聊天框也能直接办"——调用方拿到后直接 `_agent_run` + 朗读回话，
    不依赖聊天输入框。
    """
    t = (text or "").strip()
    if not t:
        return None
    for pat, kind, extract in _FAST_RULES:
        if pat.search(t):
            payload = extract(t) or ""
            # 打开类必须能抽出目标，否则不算命中（避免"打开"误触发）
            if kind in ("openapp", "openpath") and not payload:
                continue
            if kind in _FAST_KINDS:
                return (kind, payload)
    return None


def route(text: str) -> Optional[Tuple[str, str, float]]:
    """单意图路由：返回 (kind, payload, 置信度)，命中不了返回 None。

    顺序：① 自我成长经验（recall_route，已学过的"办事→手段"优先）
         ② fast 规则（含天气/邮箱/打开…）
         ③ 关键词目录兜底。
    """
    t = (text or "").strip()
    if not t:
        return None

    # ① 自我成长：分析类经验
    if _ML is not None:
        try:
            rec = _ML.recall_route(t)
            if rec in ("readfolder", "readfile"):
                p = _extract_path(t)
                if p:
                    return (rec, p, 0.9)
        except Exception:                               # noqa: BLE001
            pass

    # ② fast 规则
    fr = fast_route(t)
    if fr:
        return (fr[0], fr[1], 0.8)

    # ②-b 路径 / 文件后缀：明确针对某个文件/文件夹 → 读文件 / 分析文件夹。
    # 放在关键词兜底之前，避免被路径里的"文档"等词误判成"写文档"。
    _path = re.search(r"[A-Za-z]:[\\/][^\s，。；;：:]*", t)
    _ext = re.search(r"\.(md|txt|py|json|csv|xlsx?|docx?|html?|xml|log|pdf|yaml|yml)\b", t)
    if _path or _ext:
        p = _path.group(0).rstrip("。；;：:") if _path else _extract_path(t)
        if re.search(r"分析|研究|看看|体检|审查|梳理", t):
            return ("readfolder", p, 0.92)
        return ("readfile", p, 0.92)

    # ③ 关键词目录兜底（重活/创作类）—— 大小写不敏感，避免 "PPT" 漏匹配
    tl = t.lower()
    best = None
    for c in CAPABILITIES:
        for kw in c.keywords:
            if kw and kw.lower() in tl:
                score = 0.5 + min(len(kw), 6) / 20.0
                if best is None or score > best[2]:
                    best = (c.kind, t, score)
    return best


def orchestrate(text: str) -> List[Tuple[str, str]]:
    """多意图编排：把"查天气然后提醒我开会"拆成 [天气, 提醒] 两步。

    守则：每个子句必须各自被 route 命中才算；命中不了的就丢弃（不硬凑），
    最后由调用方对未命中的原句如实说不会。返回有序、去重的 (kind, payload)。
    """
    t = (text or "").strip()
    if not t:
        return []
    clauses = [c.strip() for c in _SPLIT_RE.split(t) if c.strip()]
    # 单句也试着直接 route，命中即用（避免"打开网易云"被逗号规则误切）
    if not clauses:
        clauses = [t]
    out: List[Tuple[str, str]] = []
    seen = set()
    for cl in clauses:
        r = route(cl)
        if r and r[0] not in seen:
            out.append((r[0], r[1]))
            seen.add(r[0])
    # 整句能直接命中却被切碎没命中时，补一次整句
    if not out:
        r = route(t)
        if r:
            out.append((r[0], r[1]))
    return out


def needs_page(kind: str) -> Optional[str]:
    """该能力对应的栏目（重活切过去让用户看见进度）；fast 返回 None。"""
    c = _KIND2CAP.get(kind)
    return c.page if c else None


def catalog_text() -> str:
    """自我描述：我会什么——按领域分组，供聊天"你能做什么"与语音帮助使用。"""
    groups: dict = {}
    for c in CAPABILITIES:
        groups.setdefault(c.domain, []).append(c)
    lines = ["我是你的智能助手，按需调用下面这些能力（说一声就能用）："]
    for domain, caps in groups.items():
        lines.append("\n【%s】" % domain)
        for c in caps:
            tag = "·" if c.fast else "◈"   # ·=语音免界面快办  ◈=需界面/确认
            lines.append("  %s %s：%s" % (tag, c.label, c.desc))
    lines.append("\n（◈ 类会在对应栏目里做、可能要先确认；· 类可以直接语音办好并念给你听。）")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 4) 自检
# ----------------------------------------------------------------------
def selftest() -> int:
    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            failed += 1
            print("FAIL %s %s" % (name, extra))

    # fast_route：应命中
    check("打开浏览器", fast_route("打开浏览器") == ("openapp", "浏览器"))
    check("打开音乐", fast_route("放点音乐") == ("openapp", "音乐"))
    check("查天气", fast_route("查一下明天天气") == ("weather", ""))
    check("查看邮箱", fast_route("帮我看看邮箱") == ("mail_check", ""))
    check("搜索X", fast_route("搜索一下 python 教程")[0] == "websearch")
    check("提醒", fast_route("提醒我下午三点开会")[0] == "remind_add")
    # fast_route：不应命中重活
    check("开发不在快指令", fast_route("帮我开发一个记账系统") is None)
    check("出图不在快指令", fast_route("画一张日落图") is None)
    # route：重活要走 route
    r = route("帮我开发一个记账系统")
    check("开发走route", r is not None and r[0] == "project", repr(r))
    r = route("分析 D:\\Code副\\business")
    check("分析走route", r is not None and r[0] == "readfolder", repr(r))
    r = route("读一下 D:\\文档\\方案.md")
    check("读方案走readfile", r is not None and r[0] == "readfile", repr(r))
    r = route("写一份周报PPT")
    check("周报PPT走doc", r is not None and r[0] == "doc", repr(r))
    fr = fast_route("搜索一下 python 教程")
    check("搜索清洗参数", fr == ("websearch", "python 教程"), repr(fr))
    # orchestrate：多意图
    o = orchestrate("查一下天气，然后提醒我三点开会")
    kinds = [k for k, _ in o]
    check("编排含天气", "weather" in kinds, repr(o))
    check("编排含提醒", "remind_add" in kinds, repr(o))
    # catalog 非空
    check("catalog 有内容", "打开应用" in catalog_text())
    # needs_page：重活有栏目
    check("开发有栏目", needs_page("project") == "work")
    check("天气无栏目", needs_page("weather") is None)

    print("\n%d 项，%s" % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(selftest())
