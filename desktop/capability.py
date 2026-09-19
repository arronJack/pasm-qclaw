# -*- coding: utf-8 -*-
"""capability.py —— 声明式能力注册表（v0.30.5）

对标白龙马（BaiLongma）`api-capability.js` 的范式：把「什么时候该用哪个能力」
从散落各处的 `if/elif` 里抽出来，变成**一条条声明**。新增一个能力只要
`register(...)`，调度代码一行都不用改。

一条能力声明（全是数据，面板/日志可直接展示）：

    id        标识（与 worklog 的 kind 对齐，便于自动记工作台账）
    label     展示名（带 emoji，沿用现有 UI 风格）
    triggers  触发词 —— **只在句首窗口内匹配**（见 ①）
    detect    复杂判据 text->bool（可选，命中即视为触发）
    tool_when 「我什么时候可用」——引擎没装/没配置就返回 False，不再硬失败
    prefeed   命中后给模型的引导语（白龙马 prefeed 范式）
    risk      low / normal / high（high 由上层走确认闸门）
    order     同分优先级，越小越先；**具体交付物必须排在泛化能力前面**（见 ②）

## 四个设计决定（都是实测踩坑才这么写的）

**① 触发词只匹配"句首窗口"。** 历史事故（核心侧 `_cap_question`）：判据写成
「'能否' 出现在任意位置就算」+ 末尾无条件 `return "all"` → 用户说
「能否先吃点小吃？」时模型整轮缺席、直接背出能力清单。
所以：先剥句首客套，只在**前 HEAD_CHARS 个字**里找触发词；一个都没命中 →
返回 `chat`（**通用兜底**，不是一个具体能力）；**绝不给具体能力写"无法判定时也用我"**。

**② 「海报 / 漫画」这类具体交付物，order 必须小于泛化的「出图」。**
实测踩到：`帮我画一张开学季的海报` 被 `image`(order=10) 抢走，只因为
「画一张」命中了而 `ad` 排在其后。改成 `ad=9 / manga=8` 才正确。

**③ 「生成一张 X / 生成一段 X」句式，靠触发词列表是列不全的，必须用 `detect`。**
实测：`生成一张赛博朋克城市夜景`、`生成一段视频`、`帮我生成一张文档`
全都命不中触发词，直接掉进 chat。

**④ ⚠️ 触发词在 `detect` 之前判定，所以"排除项"必须写进 detect，
而对应的模糊触发词要从 triggers 里拿掉。**
实测踩到两次：
  · `画个漫画分镜` → 触发词「画个」先命中 `image`，`detect` 里的「排除漫画」
    根本没机会跑；
  · `生成一张 PPT` → `detect` 里排除表写的是小写 `ppt`，而 head 没小写，
    大小写没对齐 → 排除静默失效。
故：`image` 的模糊动词（画一张/画个/生成一张…）**全部下沉到 detect**，
triggers 只留高置信词；detect 内部一律 `.lower()` 后再比对。

三条铁律与 `workflow_engine`/`autopilot` 共用：不假装执行、失败即降级、无法判定=放行。
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

# 句首窗口：只在正数第 N 个字以内找触发词，防"出现在任意位置就算命中"
HEAD_CHARS = 12

# 句首客套/连接词（剥掉它们，让"帮我画一张海报"的"画"落到句首）
_FILLER = (
    "帮我一下", "帮我", "帮忙", "请帮我", "麻烦你", "麻烦", "请你", "请",
    "能不能", "可不可以", "可以", "能否", "我要", "我想要", "我想让你", "我想",
    "给我", "替我", "现在", "接下来", "然后", "接着", "再", "先", "来", "试试",
)

CAPS: Dict[str, dict] = {}


def _norm(text: str) -> str:
    return (text or "").strip()


def head_of(text: str, n: int = HEAD_CHARS) -> str:
    """剥掉句首客套后的前 n 个字（触发词只在这么长的窗口里匹配）。"""
    t = _norm(text)
    for _ in range(4):                      # 最多剥 4 轮（"先帮我…"）
        for w in _FILLER:
            if t.startswith(w):
                t = t[len(w):].lstrip("，。、；： 　\t")
                break
        else:
            break
    return t[:n]


def register(cid: str, label: str, kind: str = "general", triggers=(),
             detect: Optional[Callable[[str], bool]] = None,
             tool_when: Optional[Callable[[dict], bool]] = None,
             prefeed: str = "", risk: str = "normal", order: int = 50,
             source: str = "builtin") -> dict:
    """注册一条能力。重复注册同 id 会覆盖（便于测试与热更新）。"""
    cap = {"id": cid, "label": label, "kind": kind or "general",
           "triggers": tuple(t.lower() for t in (triggers or ()) if t),
           "detect": detect, "tool_when": tool_when, "prefeed": prefeed or "",
           "risk": risk, "order": int(order), "source": source}
    CAPS[cid] = cap
    return cap


def get(cid: str) -> dict:
    return CAPS.get(cid) or {}


def all_caps() -> List[dict]:
    return [CAPS[k] for k in sorted(CAPS, key=lambda x: (CAPS[x]["order"], x))]


def available(cid: str, ctx: Optional[dict] = None) -> bool:
    """该能力此刻可用吗（引擎/配置不具备 → False，调用方据此优雅降级）。"""
    c = get(cid)
    if not c:
        return False
    fn = c.get("tool_when")
    if not fn:
        return True
    try:
        return bool(fn(ctx or {}))
    except Exception:                       # noqa: BLE001  判据自身出错 = 不阻断（放行）
        return True


def _hits(c: dict, text: str, head: str) -> bool:
    if any(t in head for t in c["triggers"]):
        return True
    fn = c.get("detect")
    if not fn:
        return False
    try:
        return bool(fn(_norm(text)))
    except Exception:                       # noqa: BLE001
        return False


def match(text: str, ctx: Optional[dict] = None,
          window: int = HEAD_CHARS) -> List[dict]:
    """命中的能力（按 order）。只匹配句首窗口，绝不"任意位置命中"。

    `window` 是句首窗口的宽度，**应按输入性质选**：
      · 原始用户语句（会在中段随口提到某个词）→ 默认 12，防误命中；
      · 已是提炼过的步骤标题（plan_workflow 拆出来的短指令）→ 可放宽。
    放宽只对"输入本身已经是短指令"的场景安全，别对原始语句放宽。
    """
    h = head_of(text, window).lower()
    if not h:
        return []
    hits = []
    for c in all_caps():
        if c["id"] == "chat":
            continue
        if _hits(c, text, h) and available(c["id"], ctx):
            hits.append(c)
    return hits


def best(text: str, ctx: Optional[dict] = None,
         window: int = HEAD_CHARS) -> dict:
    """最合适的一条能力；没有命中就返回 `chat`（通用兜底，不是具体能力）。"""
    hits = match(text, ctx, window)
    return hits[0] if hits else get("chat")


def prefeed_for(text: str, ctx: Optional[dict] = None,
                window: int = HEAD_CHARS) -> str:
    """命中能力的引导语（拼进系统提示用）；无命中返回空串。"""
    hits = match(text, ctx, window)
    return hits[0].get("prefeed", "") if hits else ""


# 已提炼的短指令（如工作流拆出来的步骤标题）用的放宽窗口
WINDOW_DISTILLED = 40


def catalog_lines(only_available: bool = True, ctx: Optional[dict] = None) -> List[str]:
    """能力清单（面板/提示词用）。"""
    out = []
    for c in all_caps():
        if only_available and not available(c["id"], ctx):
            continue
        out.append("%s %s" % (c["label"], c["id"]))
    return out


# --------------------------------------------------------------------------
# 判据共用件
# --------------------------------------------------------------------------
# 图像类名词
_IMG_NOUN = ("图片", "插图", "插画", "立绘", "壁纸", "头像", "封面", "配图",
             "画面", "场景", "原画", "绘图", "一张图", "一幅图", "张图", "张画")
# 出图动词（句首）
_IMG_VERB = ("画", "绘制", "出图", "生成图", "生成一张", "生成一幅", "生成个",
             "生成一段", "做一张", "做一张图", "来一张", "来一张图", "做图",
             "弄一张", "搞一张")
# 非本能力交付物（出现即让位给对应能力）
_NON_IMG = ("表格", "文档", "ppt", "演示", "幻灯片", "代码", "脚本", "视频",
            "短片", "漫剧", "漫画", "文案", "日程", "邮件", "提醒", "报表")
# ⚠️ 这里**不能**放「演示 / 幻灯片 / ppt」——实测 `生成一段产品演示视频`
# 被这条错规则排除掉，直接掉进 chat。视频类的排除项只放真正冲突的载体。
_NON_VIDEO = ("图片", "插图", "插画", "表格", "文档", "代码", "脚本",
              "漫画", "漫剧", "文案", "一张图")
# 制作类动词（用于「生成/做/写 + 名词」句式）
_MAKE_VERB = ("生成", "做", "写", "画", "整理", "制作", "弄", "搞", "新建", "拟定", "起草")
# 「动词-名词相邻」判据的窗口：动词必须在前 8 字内（剥客套后的全文），
# 交付物名词必须落在动词之后 14 字内。
# 为什么不直接放长句首窗口：放长会让「我昨天在路上看到一张宣传海报」
# 这类中段提及误命中（12 字窗口正好卡在「海报」之前）。
# 这条与「诚实护栏」同源：动作词与交付物词必须同句且相邻（见技能坑 15）。
_VERB_AT = 8
_NOUN_AFTER = 14


def _low_head(text: str) -> str:
    return head_of(text).lower()


def _detect_image(text: str) -> bool:
    """「出图动词 + 图像名词」；显式排除表格/文档/PPT/漫画等非图像交付物。

    ⚠️ 一律小写后比对（曾因没小写导致 `生成一张 PPT` 的排除失效）。
    """
    h = _low_head(text)
    if not h:
        return False
    if any(w in h for w in _NON_IMG):
        return False
    if any(w in h for w in _IMG_NOUN):
        return True
    return any(h.startswith(v) for v in _IMG_VERB)


def _detect_ad(text: str) -> bool:
    """广告/营销物料：名词很具体，句首窗口内出现即算。"""
    h = _low_head(text)
    return any(w in h for w in
               ("广告", "海报", "宣传图", "主视觉", "banner", "kv",
                "物料", "易拉宝", "宣传单", "推广图"))


def _detect_manga(text: str) -> bool:
    h = _low_head(text)
    return any(w in h for w in ("漫画", "漫剧", "分镜", "连环画"))


def _detect_payment(text: str) -> bool:
    """给**应用/网站**接支付收单 —— 不是"帮我买东西"。

    刻意与 cando.py 的安全底线划清：那条挡的是"代替用户付钱"（涉及用户的钱），
    这条是"用户自己的项目怎么收钱"，两回事。所以这里显式排除购买类说法。
    """
    t = _stripped(text)
    if any(w in t for w in ("帮我买", "替我买", "买个", "买了", "下单买", "去付")):
        return False                          # ← 安全底线：不代用户付款
    if not any(w in t for w in ("支付", "付款", "收单", "收款")):
        return False
    return any(w in t for w in ("接入", "对接", "集成", "接口", "功能", "模块",
                                "网站", "应用", "商城", "订单", "sandbox", "沙箱"))


def _stripped(text: str) -> str:
    """剥掉句首客套后的**全文**（不像 head_of 那样截断）。"""
    t = _norm(text)
    for _ in range(4):
        for w in _FILLER:
            if t.startswith(w):
                t = t[len(w):].lstrip("，。、；： 　\t")
                break
        else:
            break
    return t


def _make(nouns) -> Callable[[str], bool]:
    """「制作动词 + 交付物名词相邻」判据（『生成一张文档』『做一份报表』）。

    用**全文**而非 12 字句首窗口 —— 实测 `做一份 2026 年度报表` 的「报表」
    正好被窗口从中间截断（报=第11字、表=第12字）导致漏判。
    代价用「相邻约束」补回来：动词必须在前 8 字、名词必须紧随其后 14 字内。
    """
    def _fn(text: str) -> bool:
        tl = _stripped(text).lower()
        if not tl:
            return False
        vi = -1
        for v in _MAKE_VERB:
            k = tl.find(v)
            if 0 <= k <= _VERB_AT and (vi < 0 or k < vi):
                vi = k
        if vi < 0:
            return False
        for n in nouns:
            ni = tl.find(n)
            if ni >= 0 and 0 <= ni - vi <= _NOUN_AFTER:
                return True
        return False
    return _fn


def _detect_video(text: str) -> bool:
    h = _low_head(text)
    if not h or not any(w in h for w in ("视频", "短片", "mv", "短视频")):
        return False
    if any(w in h for w in _NON_VIDEO):
        return False
    return any(v in h[:6] for v in _MAKE_VERB) or h.startswith(("视频", "短片"))


# --------------------------------------------------------------------------
# 内置能力声明（与 desktop 现有工种 / worklog 的 kind 对齐）
# --------------------------------------------------------------------------
def _reg_builtin():
    register("chat", "💬 聊天", "general", order=99,
             prefeed="普通对话：直接回答，不要假装调用了任何工具。")

    # 具体交付物排在泛化「出图」之前（见模块头 ②）
    register("manga", "📖 生成漫剧", "manga", order=8,
             triggers=("生成漫剧", "做个漫剧", "画个漫画", "漫画分镜", "画个分镜"),
             detect=_detect_manga,
             prefeed="这是漫剧请求：先出分镜脚本（镜号/画面/台词），再逐镜出图。")
    register("ad", "📣 广告设计", "ad", order=9,
             triggers=("广告", "海报", "宣传图", "主视觉", "banner", "kv", "物料",
                       "易拉宝", "宣传单"),
             detect=_detect_ad,
             prefeed="这是广告设计请求：按「标题 / 卖点 / 行动号召 + 主视觉提示词」"
                     "四件套产出，并记入工作台账（目录=广告设计）。")
    # image：模糊动词全部下沉到 detect，triggers 只留高置信词（见模块头 ④）
    register("image", "🎨 生成图片", "image", order=10,
             triggers=("生成图片", "出图", "配图", "插画", "画张图"),
             detect=_detect_image,
             prefeed="这是出图请求：先把画面写成可直接喂给文生图模型的提示词，"
                     "再交给已接入的出图引擎；**没有真出图就不许说已生成**。")
    register("video", "🎬 生成短片", "video", order=11,
             triggers=("生成视频", "生成短片", "做个视频", "做短视频", "剪个视频"),
             detect=_detect_video,
             prefeed="这是视频生成请求：先确认分镜与时长，再交给视频引擎。")
    register("ppt", "📽 做 PPT", "ppt", order=20,
             triggers=("做ppt", "做个ppt", "做演示", "幻灯片", "做汇报"),
             detect=_make(("ppt", "演示文稿", "幻灯片")),
             prefeed="这是演示文稿请求：先列大纲，再逐页产出。")
    register("doc", "📄 Word 文档", "doc", order=21,
             triggers=("写文档", "写个文档", "word", "写报告", "写方案", "写说明"),
             detect=_make(("文档", "报告", "word", "方案", "说明")),
             prefeed="这是文档请求：先定结构，再逐节写。")
    register("xls", "📊 Excel 表格", "xls", order=22,
             triggers=("做表格", "做个表格", "excel", "统计表", "数据表"),
             detect=_make(("表格", "报表", "统计表", "数据表", "excel")),
             prefeed="这是表格请求：先定字段与口径，再填数据。")
    # v0.30.11 #2-A：code 不只是"给段代码" —— 用户要的是**真装框架**。
    # 引导语里把话说明白：先出施工计划（命令清单），再真跑、真装依赖、
    # 落盘可运行工程；装不上就如实说，别拿"通用模板"糊过去。
    register("code", "✍️ 写代码", "code", order=23,
             triggers=("写代码", "写个脚本", "写程序", "写个函数", "开发一个",
                       "实现一个", "写段代码"),
             prefeed="这是编码请求：先理清输入输出与边界；若是要**建一个能跑的项目**"
                     "（网站/后台/接口服务），用 scaffold_project 真跑官方脚手架并"
                     "安装依赖，**别只给一堆文件当成品**。")
    # v0.30.11 #2-B：给应用接支付（默认沙箱）。
    # order=13 让它排在泛化「写代码」(23) 之前 —— 否则"给商城接支付"会被 code 抢走。
    register("payment", "💳 接入支付", "payment", order=13,
             triggers=("接支付", "接入支付", "支付接口", "在线支付", "收单",
                       "收款", "支付功能", "商城", "购物车"),
             detect=_detect_payment,
             prefeed="这是「给应用接支付」请求：默认走**沙箱**（真 HMAC-SHA256 签名 +"
                     "订单状态机，离线可跑、不收真钱），把下单/回调验签/查单/退款接好；"
                     "真实渠道只在用户填齐凭据并把 live 打开后才启用。"
                     "注意：这与「帮用户去付款」是两回事，后者属安全底线不做。")
    register("copy", "✍️ 写文案", "copy", order=24,
             triggers=("写文案", "写个文案", "写段文案", "写推广", "写种草"),
             prefeed="这是文案请求：给 2-3 个不同口吻的版本供挑选。")
    register("websearch", "🔍 联网搜索", "websearch", order=30,
             triggers=("搜一下", "搜索", "查一下", "查查", "最新", "联网"),
             prefeed="这是检索请求：先说明要查什么，再给结论并标注来源。")
    register("workflow", "🧭 自动化工作流", "workflow", order=31,
             triggers=("自动化", "工作流", "分几步", "拆成步骤", "按步骤"),
             prefeed="这是多步工作请求：先拆步骤（有层级、有进度），再逐步推进。")


_reg_builtin()


# --------------------------------------------------------------------------
# 自检（正例 + 反例；全部离线、不碰用户数据）
# --------------------------------------------------------------------------
def selftest() -> int:
    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  ok   %s" % name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, extra))

    # ---------- 正例：该命中的必须命中 ----------
    pos = [
        ("帮我画一张开学季的海报", "ad"),
        ("生成一张赛博朋克城市夜景", "image"),
        ("帮我做个视频", "video"),
        ("写个脚本把 csv 合并", "code"),
        ("做PPT汇报一下", "ppt"),
        ("搜一下今天天气", "websearch"),
        ("写文案，卖点是护眼", "copy"),
        ("帮我生成一张表格", "xls"),
        ("画个漫画分镜", "manga"),
        ("把这件事拆成步骤", "workflow"),
        ("做一份 2026 年度报表", "xls"),
        ("写一份季度总结报告", "doc"),
        ("生成一段产品演示视频", "video"),
        ("画一只在月球上钓鱼的猫", "image"),
    ]
    for text, want in pos:
        got = best(text).get("id")
        check("正例「%s」→ %s" % (text, want), got == want, "实际 %s" % got)

    # ---------- 反例：不该命中的绝不能命中（误触发比漏触发糟得多） ----------
    neg = [
        "能否先吃点小吃？",          # 历史事故原句：曾直接背出能力清单
        "今天天气不错啊",
        "你还记得我上次说的那件事吗",
        "我有点累了",
        "这个功能挺好的",
        "这个文档我看了，写得不错",
        "刚才那个表格有点问题",
    ]
    for text in neg:
        got = best(text).get("id")
        check("反例「%s」→ chat" % text, got == "chat", "实际 %s" % got)

    # ---------- 触发词只在句首窗口，不在任意位置 ----------
    mid = "我昨天在路上看到一张宣传海报挺好看，你觉得呢"
    check("中段触发词不命中（句首窗口）", best(mid).get("id") != "ad",
          "实际 %s" % best(mid).get("id"))

    # ---------- 排除项：泛化能力不许抢具体交付物（模块头 ④） ----------
    excl = (("帮我生成一张文档", "doc"), ("生成一张表格", "xls"),
            ("生成一张 PPT", "ppt"), ("生成一段视频", "video"),
            ("画个漫画分镜", "manga"), ("帮我画一张宣传海报", "ad"))
    for t, want in excl:
        got = best(t).get("id")
        check("排除项「%s」→ %s" % (t, want), got == want, "实际 %s" % got)

    # ---------- 声明式：新增能力零改调度 ----------
    register("__t_demo", "🧪 测试能力", "general", triggers=("测一下声明",),
             prefeed="demo", order=1, source="selftest")
    check("新增能力后 match 立刻可见", best("测一下声明").get("id") == "__t_demo",
          "实际 %s" % best("测一下声明").get("id"))
    check("新增能力不影响其它判定", best("帮我画一张海报").get("id") == "ad",
          "实际 %s" % best("帮我画一张海报").get("id"))
    CAPS.pop("__t_demo", None)
    check("注销能力后恢复", best("测一下声明").get("id") == "chat")

    # ---------- tool_when：引擎没装 → 不可用，但不崩 ----------
    register("__t_need", "🧪 需要引擎", "general", triggers=("测需要引擎",),
             tool_when=lambda ctx: bool(ctx.get("has_engine")), order=1,
             source="selftest")
    check("tool_when=False → 不命中", best("测需要引擎", {}).get("id") == "chat")
    check("tool_when=True → 命中",
          best("测需要引擎", {"has_engine": True}).get("id") == "__t_need")
    register("__t_boom", "🧪 判据异常", "general", triggers=("测判据炸",),
             tool_when=lambda ctx: 1 / 0, order=1, source="selftest")
    check("tool_when 抛异常 → 放行不崩",
          best("测判据炸", {}).get("id") == "__t_boom")
    register("__t_dboom", "🧪 检测异常", "general", triggers=(),
             detect=lambda t: 1 / 0, order=1, source="selftest")
    check("detect 抛异常 → 不命中且不崩",
          best("完全无关的一句话").get("id") == "chat")
    for k in ("__t_need", "__t_boom", "__t_dboom"):
        CAPS.pop(k, None)

    # ---------- 健壮性：空/超长/乱码/表情输入都不能炸 ----------
    check("空输入不炸", best("").get("id") == "chat" and match("") == [])
    check("纯空白不炸", best("   \n\t ").get("id") == "chat")
    long_ok = "帮我画一张海报，" + "细节" * 300
    check("超长输入仍按句首窗口判定", best(long_ok).get("id") == "ad",
          best(long_ok).get("id"))
    junk = "".join(chr(i) for i in range(0x4E00, 0x4E40))
    check("乱码输入不炸", isinstance(best(junk).get("id"), str))
    check("表情输入不炸", isinstance(best("😀🎉✨" * 50).get("id"), str))

    # ---------- prefeed / 清单 ----------
    check("prefeed 非空", bool(prefeed_for("帮我画一张海报")))
    check("无命中时 prefeed 为空", prefeed_for("今天天气不错啊") == "")
    ids = [c["id"] for c in all_caps()]
    check("内置能力齐全（>=12 条）", len(ids) >= 12, str(ids))
    check("chat 兜底始终存在", "chat" in CAPS)
    check("每个内置能力都有 prefeed", all(get(i).get("prefeed") for i in ids),
          str([i for i in ids if not get(i).get("prefeed")]))
    check("chat 不参与 match（避免兜底抢先）",
          "chat" not in [c["id"] for c in match("帮我画一张海报")])
    check("head_of 剥客套", head_of("帮我一下，画一张海报").startswith("画一张"),
          head_of("帮我一下，画一张海报"))
    check("head_of 剥多轮客套", head_of("先帮我画个图").startswith("画个图"),
          head_of("先帮我画个图"))
    check("catalog_lines 可生成", len(catalog_lines()) == len(all_caps()))
    # window：默认窗口保持严格，放宽窗口服务于"已提炼的短指令"
    distilled = "给霖云智学做一个开学季招生海报"
    check("默认窗口：提炼句仍严格", best(distilled).get("id") == "chat",
          best(distilled).get("id"))
    check("放宽窗口：提炼句能命中",
          best(distilled, None, WINDOW_DISTILLED).get("id") == "ad",
          best(distilled, None, WINDOW_DISTILLED).get("id"))
    check("放宽窗口不会让中段提及误命中",
          best(mid, None, WINDOW_DISTILLED).get("id") != "chat"
          and best(mid).get("id") == "chat")
    check("prefeed_for 支持 window",
          bool(prefeed_for(distilled, None, WINDOW_DISTILLED)))
    check("window 非法值不炸", isinstance(best("画张图", None, 0).get("id"), str))

    print("-" * 46)
    print("capability 自检：%d 项，%s"
          % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("能力清单（%d 条）：" % len(all_caps()))
    for c in all_caps():
        print("  %-10s %-14s order=%-3d triggers=%d"
              % (c["id"], c["label"], c["order"], len(c["triggers"])))
    selftest()
