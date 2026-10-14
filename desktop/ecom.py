# -*- coding: utf-8 -*-
"""ecom.py —— 电商视觉套件（逻辑层，**零 Qt 依赖**，v0.31.27）
============================================================

一次喂图 + 一句话 → 一整套可直接交付的电商物料方案：

    淘宝 / 京东 · 主图 5 张（白底 / 场景 / 卖点 / 细节 / 参数）
    + 局部图 · 细节图（从**用户的实拍图**真裁切放大 + 编号标注）
    + 详情页屏序（banner / 痛点 / 卖点 / 细节 / 场景 / 参数表 / 服务承诺）
    + 详情文案（短描述 / 长描述 / 直播口播 / SEO 关键词串）
    + 标题优化（含核心关键词、平台字符上限校验）
    + 电商违禁词体检（广告法极限词 / 虚假承诺 / 医疗功效 / 金融收益）

## 三条设计决定（看过真事故才这么定）

**① 产品本体绝不用 AI 重画。** 主图/局部图/细节图必须是用户实拍图的**真实合成与裁切**
—— AI 重画会改变产品外形、颜色、细节，平台按「图文不符」处罚（轻则降权、重则下架）。
本模块只产出**方案与几何**（裁哪块、放哪、写什么字），真正的像素合成交给
`ecom_render`（QPainter 真实裁切/叠字）；AI 只可能用于"可选场景氛围底图"。
**没配图像引擎时如实标「待生成」，绝不假装出了图。**

**② 离线可出完整方案。** 没有大模型时，卖点/痛点/文案走**类目模板 + 卖点词表**，
并在返回值里用 `source` 标注「离线模板」；有 `llm_fn` 时优先用模型写，同样标注来源。
用户有权知道哪一段是模型写的、哪一段是模板拼的。

**③ 裁切区域用相对坐标（0~1），不写绝对像素。** 原图尺寸未知（800 / 1200 / 4000 都有），
写死像素必然错位。渲染层再按实际尺寸换算；`ecom_render.pick_detail_boxes()`
还会用图像高频能量**自动挑**信息量最大的区域来覆盖这里的默认值。

## 用法（无 UI 依赖，可独立单测）

    import ecom
    plan = ecom.build_plan("纯棉白色T恤，吸汗透气，克重220g", vision="一件白色圆领T恤",
                           photos=[r"D:\\实拍\\t.jpg"], platform="taobao")
    print(plan["title"], plan["selling_points"])
    open("方案.md", "w", encoding="utf-8").write(ecom.render_plan_md(plan))
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Callable, Dict, List, Optional

# --------------------------------------------------------------------------
# 平台规格（数值全部按各平台公开的图片规范；改这里即全链路生效）
# --------------------------------------------------------------------------
PLATFORMS: Dict[str, dict] = {
    "taobao": {
        "label": "淘宝 / 天猫",
        "main_size": (800, 800),        # 主图输出尺寸（平台建议 ≥700 才有放大镜）
        "main_min": 700,
        "main_count": 5,                # 主图建议张数（1~5，第 1 张进搜索位）
        "detail_width": 750,            # 详情页版心宽（淘宝标准）
        "title_limit_chars": 60,        # 标题字符上限（1 个汉字 = 2 字符 → 30 汉字）
        "title_limit_hanzi": 30,
        "crop_size": (800, 800),        # 局部图 / 细节图输出尺寸
        "first_white": True,            # 第 1 张主图建议纯白底
        "video": "主图视频 9~30 秒（1:1）",
        "spec_note": "主图第 1 张用纯白底，非白底图会降低搜索位点击；"
                     "主图尽量不放促销文字与拼接边框。",
    },
    "jd": {
        "label": "京东",
        "main_size": (800, 800),
        "main_min": 800,
        "main_count": 5,
        "detail_width": 790,            # 京东详情页版心宽
        "title_limit_chars": 60,
        "title_limit_hanzi": 30,
        "crop_size": (800, 800),
        "first_white": True,
        "video": "主图视频 9~30 秒",
        "spec_note": "京东主图 800×800 起；详情页版心 790px，长图单张建议 ≤15000px。",
    },
}
DEFAULT_PLATFORM = "taobao"

#: 平台别名（用户怎么说都能认）
_PLATFORM_ALIAS = {
    "淘宝": "taobao", "天猫": "taobao", "taobao": "taobao", "tmall": "taobao",
    "c店": "taobao", "集市": "taobao",
    "京东": "jd", "jd": "jd", "jingdong": "jd", "自营": "jd", "pop": "jd",
}

# --------------------------------------------------------------------------
# 类目模板（卖点维度 / 参数表字段 / 用户痛点）
# --------------------------------------------------------------------------
CATEGORIES: Dict[str, dict] = {
    "通用": {
        "dims": ["品质", "设计", "实用", "省心", "口碑"],
        "specs": ["品牌", "型号", "材质", "规格", "产地", "售后"],
        "pains": ["怕买回来和图片不一样", "担心质量没保障", "售后找不到人"],
    },
    "饰品": {
        "dims": ["材质", "工艺", "长度", "搭配", "包装"],
        "specs": ["品牌", "材质", "链长", "吊坠尺寸", "重量", "是否含证书", "包装方式"],
        "pains": ["怕戴着过敏发黑", "担心实物和图片有色差", "长度不合适显廉价"],
    },
    "服装": {
        "dims": ["面料", "版型", "做工", "尺码", "搭配"],
        "specs": ["品牌", "面料成分", "尺码", "颜色", "适用季节", "洗涤方式"],
        "pains": ["怕版型不合身", "担心面料扎人起球", "洗两次就变形"],
    },
    "食品": {
        "dims": ["原料", "口感", "工艺", "包装", "资质"],
        "specs": ["品牌", "净含量", "配料表", "保质期", "储存条件", "生产许可证"],
        "pains": ["怕不新鲜有添加", "担心口味踩雷", "包装漏气受潮"],
    },
    "数码": {
        "dims": ["性能", "续航", "做工", "兼容", "售后"],
        "specs": ["品牌", "型号", "接口", "尺寸", "重量", "质保"],
        "pains": ["怕买贵了不好用", "担心兼容性", "出问题找不到人"],
    },
    "家居": {
        "dims": ["材质", "尺寸", "承重", "清洁", "风格"],
        "specs": ["品牌", "材质", "尺寸", "颜色", "重量", "安装方式"],
        "pains": ["怕尺寸对不上", "担心材质不环保", "安装太麻烦"],
    },
    "美妆": {
        "dims": ["成分", "肤感", "功效", "容量", "资质"],
        "specs": ["品牌", "净含量", "主要成分", "适用肤质", "保质期", "备案号"],
        "pains": ["怕不适合自己肤质", "担心成分刺激", "用一次就闲置"],
    },
    "母婴": {
        "dims": ["安全", "材质", "舒适", "易用", "认证"],
        "specs": ["品牌", "适用年龄", "材质", "规格", "安全认证", "产地"],
        "pains": ["怕材质不安全", "担心宝宝过敏", "不好清洗"],
    },
    "宠物": {
        "dims": ["成分", "适口性", "安全", "清洁", "耐用"],
        "specs": ["品牌", "适用宠物", "规格", "配料", "保质期", "产地"],
        "pains": ["怕宠物不吃", "担心成分不安全", "用几天就坏"],
    },
}

#: 类目识别词（命中即判类目；越具体的词排前面）
_CAT_HINTS = (
    ("饰品", ("项链", "手链", "耳环", "耳钉", "戒指", "吊坠", "手镯", "脚链", "胸针",
             "锁骨链", "首饰", "珠宝", "饰品", "银饰", "镀金", "K金", "k金", "足金")),
    ("宠物", ("猫粮", "狗粮", "猫砂", "宠物", "猫窝", "狗窝", "牵引绳", "逗猫", "宠物用品")),
    ("母婴", ("婴儿", "宝宝", "母婴", "尿不湿", "奶瓶", "纸尿裤", "童装", "婴儿车")),
    ("美妆", ("护肤", "精华", "面膜", "口红", "粉底", "彩妆", "香水", "面霜", "洁面")),
    ("食品", ("零食", "坚果", "咖啡", "茶叶", "饼干", "饮料", "膏", "酱", "米", "油",
             "牛奶", "巧克力", "果干", "面包", "food")),
    ("数码", ("手机", "耳机", "充电", "蓝牙", "键盘", "鼠标", "相机", "音箱", "数据线",
             "移动电源", "硬盘", "显示器", "智能手表")),
    ("家居", ("收纳", "置物", "沙发", "桌", "椅", "床", "灯", "毛巾", "垃圾桶", "杯",
             "锅", "碗", "厨房", "浴室")),
    ("服装", ("t恤", "T恤", "衬衫", "裤", "裙", "外套", "卫衣", "毛衣", "袜", "鞋",
             "帽", "内衣", "羽绒", "西装")),
)

# --------------------------------------------------------------------------
# 卖点词表（离线生成卖点用；有模型时仅作提示词线索）
# --------------------------------------------------------------------------
_SELL_HINTS = (
    "加厚", "轻薄", "透气", "静音", "防水", "防滑", "防摔", "防尘", "大容量", "小巧",
    "便携", "亲肤", "柔软", "耐磨", "耐用", "易清洗", "易收纳", "环保", "可降解",
    "食品级", "304", "316", "纯棉", "真丝", "亚麻", "实木", "进口", "手工", "复古",
    "简约", "北欧", "抗菌", "保温", "保冷", "速干", "免安装", "可折叠", "无痕",
    "高弹", "多功能", "人体工学", "不沾", "不掉色", "无添加", "低糖", "高蛋白",
    "独立包装", "一物多用", "加宽", "加固", "无异味", "轻量",
    # v0.31.27：真机样例暴露的常见卖点词（"做工扎实/用料实在/经久耐用"这类
    # 中小卖家最常写的表达，以前一条都命中不了 → 卖点全成模板话）
    "扎实", "实在", "经久", "耐用", "易打理", "售后", "无忧", "省心", "好打理",
    "不易坏", "不起球", "不缩水", "不掉色", "耐脏", "好清洗", "超值", "实惠",
    "正品", "现货", "包邮", "加绒", "加厚底", "防臭", "去味", "低噪", "静音设计",
    "双面", "双层", "加长", "可机洗", "免手洗", "速热", "久坐不累",
)

# --------------------------------------------------------------------------
# 电商违禁词体检
# --------------------------------------------------------------------------
#: 分组词表。**只收多字词组，绝不用单字「最」** —— 单字会误伤
#: 「最近上新」「最后一批」「最多可装 20L」这类正常表达（有反例断言钉住）。
FORBIDDEN_GROUPS: Dict[str, tuple] = {
    "极限词（广告法）": (
        "最好", "最佳", "最优", "最强", "最低价", "最便宜", "最先进", "最流行",
        "第一品牌", "全国第一", "全网第一", "销量第一", "排名第一", "行业第一",
        "顶级", "极品", "极致", "绝无仅有", "史无前例", "独一无二", "唯一",
        "首个", "首创", "首选", "独家", "国家级", "世界级", "全球首发", "王牌",
        "万能", "百分百", "100%有效", "yyds",
    ),
    "虚假承诺": (
        "包治", "根治", "永不", "永久", "终身保修", "假一赔万", "绝对安全",
        "绝对有效", "保证治愈", "无效退款", "一次见效",
    ),
    "医疗功效": (
        "治疗", "治愈", "疗效", "药到病除", "消炎", "降血压", "降血糖",
        "丰胸", "减肥特效", "抗癌",
    ),
    "金融收益": (
        "稳赚", "保本", "无风险", "高收益", "躺赚", "包赚",
    ),
}

#: 建议替换（报告里给改稿方向，而不是只报"你违规了"）
REPLACE_HINTS = {
    "最好": "更好 / 优选", "最佳": "优选 / 理想", "最优": "更优",
    "最强": "更强", "最低价": "实惠价", "最便宜": "更实惠",
    "第一品牌": "知名品牌（或删去）", "全国第一": "口碑之选（或删去）",
    "全网第一": "热销（或删去）", "销量第一": "热销款（或删去）",
    "顶级": "高规格", "极品": "高品质", "极致": "出色",
    "独一无二": "有辨识度", "唯一": "少有", "首个": "新款 / 新上市",
    "首选": "推荐", "独家": "自有", "国家级": "高等级（需资质）",
    "世界级": "国际水准（需资质）", "万能": "多用途", "百分百": "高比例",
    "包治": "（删去，医疗宣称需资质）", "根治": "（删去，医疗宣称需资质）",
    "永不": "长久", "永久": "长期", "终身保修": "长期质保（按实际承诺写）",
    "治疗": "（删去，涉医疗宣称）", "治愈": "（删去，涉医疗宣称）",
    "疗效": "使用感受", "消炎": "（删去，涉医疗宣称）",
    "稳赚": "（删去，涉金融收益承诺）", "保本": "（删去，涉金融收益承诺）",
}

# --------------------------------------------------------------------------
# 小工具
# --------------------------------------------------------------------------
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")
_SPLIT = re.compile(r"[。；;！!？?\n\r]+|[，,、]\s*")


def norm_platform(name: str) -> str:
    """平台名 → 规范 id（认不出来回落到淘宝）。"""
    k = (name or "").strip().lower()
    if k in PLATFORMS:
        return k
    for alias, pid in _PLATFORM_ALIAS.items():
        if alias.lower() in k:
            return pid
    return DEFAULT_PLATFORM


def platform_spec(name: str) -> dict:
    """平台规格（永远返回一份可用配置，认不出就走淘宝）。"""
    return PLATFORMS.get(norm_platform(name), PLATFORMS[DEFAULT_PLATFORM])


def title_len(text: str) -> int:
    """平台口径的标题长度：**1 个汉字 = 2 字符**，其余 = 1。

    淘宝/京东的 60 都指"字符数"，所以「30 个汉字」刚好占满 60。
    只按 `len()` 数会算成 30（少一半），用户以为还有余量 —— 提交时才被截断。
    """
    return sum(2 if _CJK.match(ch) else 1 for ch in str(text or ""))


def hanzi_count(text: str) -> int:
    return len(_CJK.findall(str(text or "")))


def detect_category(text: str, vision: str = "") -> str:
    """从用户话 + 视觉描述里判类目（认不出 → 「通用」）。"""
    blob = ("%s %s" % (text or "", vision or "")).lower()
    for cat, hints in _CAT_HINTS:
        for h in hints:
            if h.lower() in blob:
                return cat
    return "通用"


def check_forbidden(text: str) -> List[dict]:
    """电商违禁词体检。返回 [{group, word, hint}]，按出现顺序、同词只报一次。

    设计取舍：**宁可少报也不误报** —— 广告法罚的是硬宣称，把「最近」判成违禁
    会让用户不再相信这个功能。所以词表全是多字词组，且单字「最」永不入库。
    """
    t = str(text or "")
    hits, seen = [], set()
    for group, words in FORBIDDEN_GROUPS.items():
        for w in words:
            if w in t and w not in seen:
                seen.add(w)
                hits.append({"group": group, "word": w,
                             "hint": REPLACE_HINTS.get(w, "考虑改写或删除")})
    return hits


def _split_phrases(*texts) -> List[str]:
    out: List[str] = []
    for t in texts:
        for seg in _SPLIT.split(str(t or "")):
            seg = seg.strip(" \t·-—:：")
            if 2 <= len(seg) <= 40:
                out.append(seg)
    return out


#: 结构化输入里的「字段名」——它们**不是**搜索词，抽关键词时必须排掉
_FIELD_WORDS = ("品牌", "产品", "产品名", "商品", "商品名", "品名", "卖点", "价格",
                "售价", "到手价", "规格", "型号", "材质", "尺码", "颜色", "类目",
                "重量", "产地", "售后", "服务", "优惠", "促销")


def extract_field(text: str, names) -> str:
    """从「字段：值」这种结构化输入里取值（值截到下一个字段或标点为止）。

    真机样例：用户写「品牌：自有品牌。产品：主推款单品。卖点：…」——
    旧版只认 `产品[:：]` 后紧跟的一段，遇到"产品：X"后面还有句号的情况会取错。
    """
    t = str(text or "")
    for nm in names:
        m = re.search(re.escape(nm) + r"\s*[:：]\s*([^\n。；;]{1,30})", t)
        if m:
            return m.group(1).strip(" ，,、。；;：:")
    return ""


#: 产品名里**不该往左扩**的虚词/指代字（命中即停，"上述的图中项链" → "项链"）
_NON_NAME_CHARS = set(
    "的地得了着过和与及或把被让使给为我你他她它们这那哪些什么请帮要有还想能可会做生成出"
    "一套张个只位件款图里中上下述边面幅该此原")


def pick_category_word(text: str) -> str:
    """从话里挑**品类词**（"上述的图中项链" → "项链"）。找不到返回 ""。

    为什么需要它：用户说话是「上述的图中项链，帮我出五张主图」—— 产品名在哪一段
    **看不出来**，但品类词是认得出的。先认品类词，再把它前面的修饰词带进来，
    比"取最长短句"稳得多。
    """
    t = str(text or "")
    best = ""
    for _cat, hints in _CAT_HINTS:
        for h in hints:
            if h in t and len(h) > len(best):
                best = h
    return best


def _noun_with_modifiers(text: str, noun: str) -> str:
    """给品类词补上它前面的修饰词（"加厚保暖外套"），但**不越过虚词** ——
    "上述的图中项链" 保持 "项链"，不会变成 "图中项链"。
    """
    t = str(text or "")
    i = t.find(noun)
    if i <= 0:
        return noun
    j = i
    while j > 0 and (i - j) < 4:
        ch = t[j - 1]
        if ch in _NON_NAME_CHARS or not re.match(r"[\u4e00-\u9fffA-Za-z0-9]", ch):
            break
        j -= 1
    return (t[j:i + len(noun)] or noun)[:24]


def _pick_product_name(text: str, category: str) -> str:
    """产品名：① 显式字段 → ② **品类词（带修饰）** → ③ 最短短句 → ④ 类目兜底。

    ⚠️ 为什么**不能**"取最长短句"（v0.31.27 真机教训，用户原话：
    「上述的图中项链，我需要你帮我出五张主图、包括副图和细节图，还有帮我出10张详情图」）：
    句子被切成 5 段后**最长的一段是「还有帮我出10张详情图」** —— 于是产品名、目录名、
    回复标题、参数表、资质清单全跟着错。改成"先认品类词"就稳了：认得出「项链」，
    取的就是「项链」。
    """
    got = extract_field(text, ("产品名", "商品名", "品名", "产品", "商品"))
    if got:
        return got[:24]
    noun = pick_category_word(text)
    if noun:
        return _noun_with_modifiers(text, noun)
    cands = []
    for p in _split_phrases(text):
        if "：" in p or ":" in p:
            continue
        if any(p.startswith(w) for w in _FIELD_WORDS):
            continue
        if re.match(r"^(帮我|请|做一?套|生成|给|要|我|我是|卖|上架|淘宝|京东|上述|上面)", p):
            continue
        if re.search(r"(出|生成|要|帮|请|张|套|图)", p):
            continue
        cands.append(p)
    # ★ 取**最短**的那段（长的多半是句子，不是产品名）
    if cands:
        return min(cands, key=len)[:24]
    return "%s商品" % category


#: 用户话里"要几张详情图"（真机原话：「还有帮我出**10张详情图**」）
_DETAIL_CNT_RE = re.compile(
    r"(\d{1,2})\s*张\s*(?:独立)?\s*(?:详情|细节|商品详情)\s*图?"
    r"|(?:详情|细节)\s*图\s*(\d{1,2})\s*张")


def parse_detail_count(text: str, lo: int = 2, hi: int = 30) -> Optional[int]:
    """从话里取"要几张详情图"；取不到返回 `None`（= 按屏自然切）。

    为什么要有它：用户说「10 张详情图」时，按屏自然切可能给 8 张或 12 张 ——
    他要的是**10 张**。取到了就照做（限制在 lo~hi，挡掉"要 999 张"这种）。
    """
    m = _DETAIL_CNT_RE.search(str(text or ""))
    if not m:
        return None
    raw = m.group(1) or m.group(2) or ""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def normalize_keywords(words) -> List[str]:
    """关键词规范化：去空白、去重、截长、最多 10 个。"""
    out, seen = [], set()
    for w in (words or []):
        w = re.sub(r"\s+", "", str(w or ""))[:12]
        if w and w not in seen:
            seen.add(w)
            out.append(w)
    return out[:10]


#: 关键词的模型提示词（要"能直接填进平台搜索框"的词，不是句子）
_KW_ASK = ("你是电商运营。根据下面的商品信息，给出 8~10 个买家会用来搜索的关键词"
           "（淘宝/京东搜索框里真会打的词，2~6 个字，包含品类词、属性词、"
           "送礼/人群/场景词）。只输出关键词，用空格分隔，不要编号、不要解释、不要标点。"
           "\n商品信息：%s")


def build_keywords(product: str, category: str, text: str = "", vision: str = "",
                   n: int = 8, llm_fn: Optional[Callable] = None):
    """关键词：**优先让模型给**，拿不到再用类目词表兜底。返回 `(words, source)`。

    ★ 铁律：绝不把用户原句机械切片当关键词（见 CATEGORY_KEYWORDS 的注释）。
    """
    if llm_fn:
        blob = "%s ｜ 类目：%s ｜ 用户描述：%s ｜ 画面：%s" % (
            product, category, str(text or "")[:300], str(vision or "")[:150])
        try:
            got = llm_fn(_KW_ASK % blob)
            words = [w for w in normalize_keywords(re.split(r"[\s,，、;；/|]+", str(got or "")))
                     if w and w != product]
            if len(words) >= 3:
                return normalize_keywords([product] + words)[:n], "llm"
        except Exception:                                    # noqa: BLE001
            pass
    out = [product] if product else []
    out += list(CATEGORY_KEYWORDS.get(category) or CATEGORY_KEYWORDS["通用"])
    out += [h for h in _SELL_HINTS if h in str(text or "")]
    return normalize_keywords(out)[:n], "offline"


#: 用户"指代上面那张图"的说法（真机原话：「上述的图中项链」）
_PRIOR_IMG_RE = re.compile(
    r"上述的?图|上面的?图|上面那[张幅个]?|刚(?:才)?(?:那|这)[张幅个]?图|"
    r"刚发(?:的|过)的?图|刚给(?:的|过)的?图|之前的?图|"
    r"我(?:发|给|上)(?:的|过)?(?:那|这)?[张幅个]?图|这[张幅个]图|这个图|"
    r"该图|此图|图中|上图|原图|这[张幅](?:照片|图片)|那[张幅](?:照片|图片)")


def refers_prior_image(text: str) -> bool:
    """用户是否在**指代"上面那张图"**（而不是重新喂一张）。

    真机场景：用户 15:12 喂过产品图，隔了两个多小时、应用重启之后回来说
    「**上述的图中**项链，帮我出五张主图」—— 这时必须去**找**那张图，
    而不是回一句"没有可用的产品实拍图"（用户的原话就是"我明明给过图"）。
    """
    return bool(_PRIOR_IMG_RE.search(str(text or "")))


def optimize_title(product: str, keywords=None, category: str = "通用",
                   brand: str = "", platform: str = "taobao",
                   core: str = "", llm_fn: Optional[Callable] = None) -> dict:
    """标题优化：核心词前置 + 属性词 + 类目词，并校验平台字符上限。

    返回 {title, len, limit, ok, trimmed, style, source, note}
    `trimmed` = 超限时按平台口径截断后的版本（**两个都给**，让用户自己挑）。
    """
    spec = platform_spec(platform)
    limit = int(spec["title_limit_chars"])
    kws = normalize_keywords(keywords)
    parts: List[str] = []
    if brand:
        parts.append(str(brand).strip())
    parts.append(str(product or "").strip())
    if core:
        parts.append(str(core).strip())
    parts += kws
    if category and category != "通用":
        parts.append(category)
    parts.append("正品")
    # 去重（保序）：品牌/产品同时也可能出现在关键词里，不去重就会"下沉了品牌"
    # 却还在标题里 → `dropped` 全是幽灵
    _seen, _uniq = set(), []
    for _p in parts:
        if _p and _p not in _seen:
            _seen.add(_p)
            _uniq.append(_p)
    parts = _uniq

    # ★ 平台字数控是**硬约束**，要在这里就做进去：从尾部（最弱的属性词）依次下沉，
    #   直到装得下。下沉的词如实记进 `dropped`，并留在 SEO 分层里继续用。
    dropped = []
    while len(parts) > 2 and title_len(" ".join(parts)) > limit:
        dropped.insert(0, parts.pop())
    title = re.sub(r"\s+", " ", " ".join(p for p in parts if p).strip())

    source = "offline"
    if callable(llm_fn):
        try:
            ask = ("你是电商标题优化师。按「核心词前置 + 属性词 + 类目词」，"
                   "写一条%s商品标题，**不超过 %d 个字符**（1 个汉字算 2 字符），"
                   "不要促销词、不要违禁词（最/第一/顶级等），只输出标题本身：\n"
                   "产品：%s\n关键词：%s\n品牌：%s" %
                   (spec["label"], limit, product, "、".join(kws), brand or "无"))
            r = re.sub(r"```[a-z]*|```", "", str(llm_fn(ask) or "")).strip()
            r = r.splitlines()[0].strip() if r else ""
            if len(r) >= 4:
                title, source = r, "llm"
        except Exception:                       # noqa: BLE001  模型不可用 ≠ 不出方案
            source = "offline"

    n = title_len(title)
    trimmed = title
    if n > limit:
        # 按平台口径**从左往右累加截断**（保住核心词前置的优势）
        acc, cut = 0, 0
        for i, ch in enumerate(title):
            acc += 2 if _CJK.match(ch) else 1
            if acc > limit:
                break
            cut = i + 1
        trimmed = title[:cut].rstrip(" ,，、-")
    return {"title": title, "len": n, "limit": limit, "ok": n <= limit,
            "trimmed": trimmed, "len_trimmed": title_len(trimmed),
            "hanzi": hanzi_count(title), "style": "%s 标题" % spec["label"],
            "source": source, "dropped": dropped,
            "note": (("已把 %d 个较弱的词下沉到详情页/SEO 长尾（%s），标题本身合规。"
                      % (len(dropped), "、".join(dropped))) if dropped else
                     ("" if n <= limit else
                      "核心词本身就超平台上限 %d 字符（产品名过长）——已给截断版 trimmed，"
                      "建议再精简产品名。" % (n - limit)))}


def build_selling_points(text: str, vision: str, category: str,
                         llm_fn: Optional[Callable] = None,
                         n: int = 5, facts: str = "") -> List[dict]:
    """5 条卖点。**有模型用模型，没有用「卖点词表 + 类目维度」模板**，并标来源。

    `facts`：产品档案摘要（"材质=S925银；链长=42cm"）。有它模型才写得具体，
    没有它只能写"材质方面做了针对性优化"这种空话（v0.31.30 真机教训）。
    """
    cat = CATEGORIES.get(category) or CATEGORIES["通用"]
    if callable(llm_fn):
        try:
            ask = ("你是电商资深文案。为下面的商品写 %d 条卖点，每条格式："
                   "`标题|一句话说明（20~40字，具体、可感知，禁止极限词）`，"
                   "只输出 %d 行，不要编号：\n商品：%s\n图片内容：%s\n%s"
                   % (n, n, text, vision or "（无）",
                      ("\n" + facts) if facts else ""))
            r = str(llm_fn(ask) or "")
            rows = []
            for ln in r.splitlines():
                ln = re.sub(r"^\s*[\d一二三四五]+[\.、)）]\s*", "", ln).strip(" -*")
                if "|" in ln:
                    h, b = ln.split("|", 1)
                    if h.strip() and b.strip():
                        rows.append({"head": h.strip()[:12],
                                     "body": b.strip()[:80], "source": "llm"})
            if len(rows) >= n:
                return rows[:n]
        except Exception:                       # noqa: BLE001
            pass
    # ---- 离线模板：优先用命中词表的**原句片段**，比凭空编更贴实 ----
    phrases = _split_phrases(text, vision)
    hit_rows: List[dict] = []
    for p in phrases:
        for h in _SELL_HINTS:
            if h in p:
                body = p if len(p) >= 6 else "%s，%s" % (h, p)
                hit_rows.append({"head": h, "body": body[:60], "source": "offline"})
                break
    out, used = [], set()
    for r in hit_rows:
        if r["head"] in used:
            continue
        used.add(r["head"])
        out.append(r)
        if len(out) >= n:
            return out
    for d in cat["dims"]:
        if len(out) >= n:
            break
        if d in used:
            continue
        used.add(d)
        out.append({"head": d,
                    "body": "%s方面做了针对性优化，具体参数见下方参数表" % d,
                    "source": "offline"})
    return out[:n]


def build_spec_rows(category: str, text: str = "", brand: str = "",
                    price: str = "", extra=None) -> List[List[str]]:
    """参数表：**只列真知道的项**，不知道的整行不出现。

    ★ v0.31.31（小志定）：不要「待补充 / 待定」这类占位 —— "没有就不要"。
    空行占位看着像没做完，也不像给客户看的东西。能从用户话里对上的就填，
    对不上的直接不列；真的一条都没有 → 返回空表，调用方整块不渲染参数区。
    """
    cat = CATEGORIES.get(category) or CATEGORIES["通用"]
    fields = list(cat["specs"])
    rows = []
    for f in fields:
        v = ""
        m = re.search(re.escape(f) + r"\s*[:：]\s*([^\n；;，,]{1,24})", str(text or ""))
        if m:
            v = m.group(1).strip()
        elif f == "品牌" and brand:
            v = str(brand)
        elif f in ("品牌",) and re.search(r"品牌\s*[:：]\s*(\S+)", str(text or "")):
            v = re.search(r"品牌\s*[:：]\s*(\S+)", str(text or "")).group(1)
        if v and v not in ("待补充", "待定", "未知", "-", "无"):
            rows.append([f, v])
    if price:
        rows.insert(0, ["价格", str(price)])
    for k, v in (extra or {}).items():
        if str(v or "").strip() and str(v).strip() not in ("待补充", "待定", "未知"):
            rows.append([str(k), str(v)])
    return rows


# --------------------------------------------------------------------------
# 产品档案（v0.31.30）：**先录信息，再生成**
# --------------------------------------------------------------------------
# 真机问题（2026-10-14）：用户只说一句话就开始生成，参数表 7 行全是「待补充」，
# 5 条卖点是同一句模板话重复 5 遍（"材质方面做了针对性优化，具体参数见下方参数表"）。
# 根因：**没有"先录产品信息"这个环节**。这里补上——按类目给出该录哪些属性、
# 缺的先问（不生成），用户回答后回填档案，文案/参数表/详情页全部用真实信息。
# 铁律：用户说「未知」就记「未知」，文案里**不写这一项**，绝不编数据。

#: 类目 → 属性录入字段。`required=True` 是"没有就没法写文案/平台必填"的。
PROFILE_FIELDS: Dict[str, List[dict]] = {
    "饰品": [
        {"key": "material", "label": "材质", "required": True,
         "ask": "材质是什么？（如 S925银 / 18K金 / 钛钢 / 淡水珍珠）"},
        {"key": "chain_len", "label": "链长", "required": True,
         "ask": "链长多少？（如 42cm；手链报内径）"},
        {"key": "weight", "label": "重量", "required": False,
         "ask": "重量多少？（如 3.2g，没有可回「未知」）"},
        {"key": "pendant_size", "label": "吊坠尺寸", "required": False,
         "ask": "吊坠尺寸？（如 8×12mm，没有可回「未知」）"},
        {"key": "color", "label": "颜色", "required": False,
         "ask": "颜色/色调？（如 银色 / 玫瑰金）"},
        {"key": "craft", "label": "工艺", "required": False,
         "ask": "工艺特点？（如 爪镶 / 微镶 / 拉丝，没有可回「未知」）"},
        {"key": "cert", "label": "证书", "required": False,
         "ask": "有检测证书吗？（有 / 无）"},
        {"key": "gift_box", "label": "包装", "required": False,
         "ask": "包装什么样？（如 礼盒+贺卡，没有可回「未知」）"},
    ],
    "服装": [
        {"key": "fabric", "label": "面料成分", "required": True,
         "ask": "面料成分？（如 95%棉+5%氨纶）"},
        {"key": "sizes", "label": "尺码", "required": True,
         "ask": "有哪些尺码？（如 S/M/L/XL）"},
        {"key": "color", "label": "颜色", "required": True,
         "ask": "有哪些颜色？（如 黑 / 白 / 雾蓝）"},
        {"key": "season", "label": "适用季节", "required": False,
         "ask": "适合什么季节穿？（如 春秋）"},
        {"key": "wash", "label": "洗涤方式", "required": False,
         "ask": "怎么洗？（如 机洗冷水 / 手洗，没有可回「未知」）"},
        {"key": "style", "label": "版型", "required": False,
         "ask": "版型？（如 宽松 / 修身 / H型）"},
    ],
    "食品": [
        {"key": "net_weight", "label": "净含量", "required": True,
         "ask": "净含量多少？（如 500g/袋）"},
        {"key": "ingredients", "label": "配料表", "required": True,
         "ask": "主要配料？（如 巴旦木、海盐）"},
        {"key": "shelf_life", "label": "保质期", "required": True,
         "ask": "保质期多久？（如 180 天）"},
        {"key": "storage", "label": "储存条件", "required": False,
         "ask": "怎么储存？（如 阴凉干燥处密封保存）"},
        {"key": "origin", "label": "产地", "required": False,
         "ask": "产地？（如 新疆阿克苏）"},
    ],
    "美妆": [
        {"key": "net_weight", "label": "净含量", "required": True,
         "ask": "净含量？（如 30ml）"},
        {"key": "ingredients", "label": "主要成分", "required": True,
         "ask": "主要成分？（如 烟酰胺 5% / 玻尿酸）"},
        {"key": "skin", "label": "适用肤质", "required": True,
         "ask": "适用什么肤质？（如 干皮 / 油皮 / 敏感肌）"},
        {"key": "efficacy", "label": "功效", "required": False,
         "ask": "主要功效？（如 保湿 / 提亮，没有可回「未知」）"},
        {"key": "shelf_life", "label": "保质期", "required": False,
         "ask": "保质期？（如 未开封 3 年）"},
    ],
    "母婴": [
        {"key": "age", "label": "适用年龄", "required": True,
         "ask": "适用多大宝宝？（如 0-6 个月 / 1-3 岁）"},
        {"key": "material", "label": "材质", "required": True,
         "ask": "材质？（如 A类纯棉 / 食品级硅胶）"},
        {"key": "spec", "label": "规格", "required": True,
         "ask": "规格尺寸？（如 60×90cm）"},
        {"key": "cert", "label": "安全认证", "required": False,
         "ask": "有哪些认证？（如 3C / A类，没有可回「未知」）"},
    ],
    "宠物": [
        {"key": "pet", "label": "适用宠物", "required": True,
         "ask": "给什么宠物用？（如 猫 / 狗 / 通用）"},
        {"key": "net_weight", "label": "规格", "required": True,
         "ask": "规格/净含量？（如 2kg / 10L）"},
        {"key": "ingredients", "label": "配料", "required": False,
         "ask": "主要原料？（如 鸡肉+燕麦，没有可回「未知」）"},
        {"key": "shelf_life", "label": "保质期", "required": False,
         "ask": "保质期？（如 18 个月）"},
    ],
    "数码": [
        {"key": "model", "label": "型号", "required": True,
         "ask": "型号？（如 XX-Pro 2026款）"},
        {"key": "spec", "label": "规格", "required": True,
         "ask": "关键规格？（如 10000mAh / 65W / 蓝牙5.3）"},
        {"key": "warranty", "label": "质保", "required": False,
         "ask": "质保多久？（如 一年换新）"},
        {"key": "compat", "label": "兼容性", "required": False,
         "ask": "兼容哪些设备？（如 安卓/iOS/PC）"},
    ],
    "家居": [
        {"key": "material", "label": "材质", "required": True,
         "ask": "材质？（如 实木 / 304不锈钢 / 棉麻）"},
        {"key": "size", "label": "尺寸", "required": True,
         "ask": "尺寸？（如 30×20×15cm）"},
        {"key": "color", "label": "颜色", "required": False,
         "ask": "颜色？（如 原木色 / 白色）"},
        {"key": "load", "label": "承重", "required": False,
         "ask": "承重多少？（如 10kg，没有可回「未知」）"},
    ],
    "通用": [
        {"key": "material", "label": "材质", "required": True,
         "ask": "材质/主要成分？（如 不锈钢 / 棉 / PVC）"},
        {"key": "size", "label": "规格", "required": True,
         "ask": "规格尺寸？（如 20×15cm / 500ml）"},
        {"key": "color", "label": "颜色", "required": False,
         "ask": "颜色？（如 黑 / 白 / 原色）"},
        {"key": "weight", "label": "重量", "required": False,
         "ask": "重量？（如 350g，没有可回「未知」）"},
        {"key": "origin", "label": "产地", "required": False,
         "ask": "产地？（如 广东深圳）"},
    ],
}

#: 属性别名 → key（用户不会照我们的 key 说话："链长"他会说"项链多长"）
_PROFILE_ALIAS = {
    "material": ("材质", "材料", "质地"),
    "chain_len": ("链长", "项链长度", "长度", "多长"),
    "weight": ("重量", "克重", "多重"),
    "pendant_size": ("吊坠尺寸", "吊坠大小", "坠子尺寸"),
    "color": ("颜色", "色调", "色系"),
    "craft": ("工艺", "做工"),
    "cert": ("证书", "检测证书", "认证", "安全认证"),
    "gift_box": ("包装", "礼盒"),
    "fabric": ("面料", "面料成分", "成分", "材质"),
    "sizes": ("尺码", "尺寸规格", "大小"),
    "season": ("季节", "适用季节"),
    "wash": ("洗涤", "洗法", "清洗"),
    "style": ("版型", "款式"),
    "net_weight": ("净含量", "含量", "容量", "规格"),
    "ingredients": ("配料", "配料表", "成分", "原料"),
    "shelf_life": ("保质期", "保鲜期"),
    "storage": ("储存", "保存", "存放"),
    "origin": ("产地", "产地来源"),
    "skin": ("肤质", "适用肤质"),
    "efficacy": ("功效", "效果"),
    "age": ("适用年龄", "年龄段", "多大"),
    "spec": ("规格", "尺寸", "参数"),
    "pet": ("适用宠物", "宠物", "适用对象"),
    "model": ("型号", "款号"),
    "warranty": ("质保", "保修", "售后保障"),
    "compat": ("兼容", "兼容性", "适配"),
    "size": ("尺寸", "大小", "规格"),
    "load": ("承重", "承重力"),
}

#: 说"不知道"的表达 → 记「未知」，不再追问
_UNKNOWN_WORDS = ("未知", "不知道", "不清楚", "不确定", "没有", "无", "暂无", "不详",
                  "不确定的", "说不好", "不记得", "忘了", "skip", "跳过")

#: 已知材质 → 一句**材料学常识级**的卖点（不是编造：S925 就是 92.5% 银，
#: 比普通合金更不易致敏是公认属性）。不认识的材质一律走中性表述。
_MATERIAL_FACTS = (
    (("s925", "925银", "纯银", "足银"), "通体S925银，比普通合金更亲肤，久戴不易发黑"),
    (("18k", "18K", "玫瑰金", "k金"), "18K金电镀工艺，色泽耐久不易褪"),
    (("钛钢", "不锈钢316", "316l"), "钛钢/316L材质，防敏防汗，日常佩戴省心"),
    (("淡水珍珠", "珍珠"), "天然淡水珍珠，光泽柔和不张扬"),
    (("纯棉", "全棉", "精梳棉"), "纯棉面料，亲肤透气不闷热"),
    (("304不锈钢", "不锈钢"), "304不锈钢，防锈耐用易清洁"),
    (("食品级硅胶", "硅胶"), "食品级硅胶，无异味耐高温"),
    (("实木", "橡木", "胡桃木"), "实木材质，纹理自然环保无甲醛添加"),
    (("a类", "A类"), "A类安全标准，婴幼儿也可贴身接触"),
)


def profile_fields(category: str) -> List[dict]:
    """类目对应的录入字段（未知类目走「通用」）。"""
    return [dict(f) for f in PROFILE_FIELDS.get(category) or PROFILE_FIELDS["通用"]]


def new_profile(category: str, name: str = "") -> dict:
    """一份空档案。`values` 里**只有用户真给过的值**。"""
    now = time.strftime("%Y-%m-%d %H:%M")
    return {"name": str(name or ""), "category": str(category or "通用"),
            "values": {}, "created": now, "updated": now}


def _norm_val(v: str) -> str:
    v = str(v or "").strip().strip("。；;，,、 ")
    return v[:40]


def profile_apply_text(profile: dict, text: str, category: str = "") -> dict:
    """把用户这句话里能对上的属性**回填**进档案。返回 (profile, filled)。

    认识「材质：S925银」「链长42cm」「重量3.2g」这些写法；也认「未知/不知道」
    —— 记成「未知」，以后不再追问（追问会把人烦死）。
    """
    category = category or profile.get("category") or "通用"
    filled = []
    text = str(text or "")
    low = text.lower()
    is_unknown = any(w in text for w in _UNKNOWN_WORDS)
    for f in profile_fields(category):
        key, label = f["key"], f["label"]
        aliases = _PROFILE_ALIAS.get(key) or (label,)
        got = ""
        for a in aliases:
            # 「材质：S925银」「材质是S925银」「材质 S925银」「链长42cm」
            m = re.search(re.escape(a) + r"\s*[是为：:的]?\s*([^\n；;，,。]{1,30})", text)
            if m:
                got = _norm_val(m.group(1))
                break
        if got:
            if any(got.startswith(w) or w == got for w in _UNKNOWN_WORDS):
                got = "未知"
            if got and got not in ("的", "是", "了"):
                profile.setdefault("values", {})[key] = got
                filled.append(label)
        elif is_unknown and key not in (profile.get("values") or {}):
            # 用户笼统地说"不知道" → 这一轮问的全部记未知，别追第二轮
            profile.setdefault("values", {})[key] = "未知"
    if filled:
        profile["updated"] = time.strftime("%Y-%m-%d %H:%M")
    return profile, filled


def profile_missing(profile: dict, category: str = "") -> List[dict]:
    """还没录的字段。**必填优先**，一轮最多问 4 个。

    ★「未知」**不算缺失** —— 它是用户明说"没有/不知道"的记号
    （`profile_apply_text` 承诺过：记成「未知」，以后不再追问，追问会把人烦死）。
    旧实现把「未知」又当缺失问回来，真机表现就是：用户答完"其他不知道"，
    第二轮还揪着同样的问题问（v0.31.30 端到端验证抓到的）。
    """
    category = category or profile.get("category") or "通用"
    vals = profile.get("values") or {}
    req, opt = [], []
    for f in profile_fields(category):
        if str(vals.get(f["key"]) or "").strip():
            continue
        (req if f.get("required") else opt).append(f)
    return (req + opt)[:4]


def profile_known(profile: dict) -> Dict[str, str]:
    """只取**真知道**的值（「未知」和空都不算）。"""
    return {k: str(v) for k, v in (profile.get("values") or {}).items()
            if str(v or "").strip() and str(v).strip() != "未知"}


def profile_summary(profile: dict) -> str:
    """一行摘要，给提示词/目录用。"""
    vals = profile_known(profile)
    if not vals:
        return ""
    labels = {}
    for f in profile_fields(profile.get("category") or "通用"):
        labels[f["key"]] = f["label"]
    return "；".join("%s=%s" % (labels.get(k, k), v) for k, v in vals.items())


def _key_of_label(labels: Dict[str, str], aliases: Dict[str, tuple],
                  head) -> str:
    """卖点标题（「长度」）→ 档案 key（`chain_len`）。认标签和别名。"""
    h = str(head or "").strip()
    for k, lab in labels.items():
        if lab == h:
            return k
    for k, names in aliases.items():
        if h in [str(x) for x in names]:
            return k
    return ""


def profile_facts_for_llm(profile: dict) -> str:
    """给大模型的**真实产品信息**（没有就返回空串，别把"未知"喂给它）。"""
    s = profile_summary(profile)
    return ("已知产品信息（必须用上，没有的项不要编）：%s" % s) if s else ""


def apply_profile_to_plan(plan: dict, profile: Optional[dict]) -> dict:
    """用档案里的真实值**升级**方案：参数表填实、卖点去模板化。

    这是"录了信息"真正兑现的地方——不改这一步，录了也白发。
    """
    if not profile:
        return plan
    vals = profile_known(profile)
    labels = {f["key"]: f["label"]
              for f in profile_fields(profile.get("category") or plan.get("category") or "通用")}
    # ---- ① 参数表：把档案里的真值填进去（**没有的项整行不出现**，不做占位）----
    #   v0.31.31：表里按类目模板预填的行若被档案填上真值就替换，否则不动；
    #   `build_spec_rows` 已经不再产出「待补充」行，这里只需补上"档案有但表里没有"的项。
    _rows = plan.get("spec_rows") or []
    for row in _rows:
        for k, v in vals.items():
            if labels.get(k) == row[0] and row[1] in ("", "待补充"):
                row[1] = v
    _have = {r[0] for r in _rows}
    for k, v in vals.items():
        _lb = labels.get(k)
        if _lb and _lb not in _have:
            _rows.append([_lb, v])
            _have.add(_lb)
    plan["spec_rows"] = _rows
    # ---- ② 卖点：把"材质方面做了针对性优化，具体参数见下方参数表"这种
    #      模板话换成有信息量的句子 ----
    # 匹配要认**别名**：类目维度叫「长度」，档案字段叫「链长」——不认别名
    # 就会出现"链长=42cm 录了，长度那条卖点还是空话"（第一版就栽在这）。
    head2val: Dict[str, str] = {}
    for k, v in vals.items():
        for name in ((labels.get(k),) + (_PROFILE_ALIAS.get(k) or ())):
            if name:
                head2val.setdefault(str(name), v)
    for p in plan.get("selling_points") or []:
        body = str(p.get("body") or "")
        if "方面做了针对性优化" not in body:
            continue
        v = head2val.get(str(p.get("head") or ""))
        if not v:
            continue
        fact = ""
        for keys, sent in _MATERIAL_FACTS:
            if any(x in v for x in keys):
                fact = sent
                break
        if fact:
            # 值里已含材质词（"S925银"）→ 直接用常识句，别把同一个词写两遍
            p["body"] = fact if any(x in fact for x in v.split()) or v in fact \
                else "%s，%s" % (v, fact)
        else:
            # 中性表述：只陈述录到的值，不额外编功效
            _lead = {"craft": "采用%s工艺", "style": "%s版型",
                     "wash": "%s，打理省心", "chain_len": "链长%s",
                     "size": "尺寸%s", "sizes": "涵盖%s多个尺码",
                     "weight": "整件约%s，轻盈不压身"}.get(
                         _key_of_label(labels, _PROFILE_ALIAS, p.get("head")), "%s")
            p["body"] = (_lead % v if "%s" in _lead else
                         "%s（%s），具体见下方参数表" % (v, p.get("head")))
        p["source"] = "profile"
    # ---- ③ 详情页首屏：把真实材质/规格写进导语 ----
    if vals:
        plan["profile"] = dict(vals)
        plan["profile_summary"] = profile_summary(profile)
    return plan


# --------------------------------------------------------------------------
# 商品空间 / 目录（v0.31.30）：多个产品本地可查
# --------------------------------------------------------------------------
def ecom_root(cfg: dict) -> str:
    """电商产物根目录：`<工作根>/图片/电商`（与 `out_dir_for` 同一套推导）。"""
    try:
        import workspace as WS
        base = WS.cat_dir("image", cfg)
    except Exception:                                    # noqa: BLE001
        base = os.path.join(os.path.expanduser("~"), "PASM工作", "图片")
    return os.path.join(base, "电商")


def catalog_path(cfg: dict) -> str:
    return os.path.join(ecom_root(cfg), "商品目录.json")


def catalog_html_path(cfg: dict) -> str:
    return os.path.join(ecom_root(cfg), "商品目录.html")


def catalog_load(cfg: dict) -> List[dict]:
    """读商品目录。文件不在/坏了都返回空列表（目录不是关键路径，绝不炸）。"""
    p = catalog_path(cfg)
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
        items = d.get("items") if isinstance(d, dict) else d
        return [x for x in (items or []) if isinstance(x, dict)]
    except Exception:                                    # noqa: BLE001
        return []


def catalog_upsert(cfg: dict, entry: dict) -> List[dict]:
    """按 `dir` 去重 upsert 一条产品，写回 json 并**顺手刷新 html**。"""
    items = [x for x in catalog_load(cfg) if x.get("dir") != entry.get("dir")]
    items.insert(0, dict(entry))
    root = ecom_root(cfg)
    try:
        os.makedirs(root, exist_ok=True)
        with open(catalog_path(cfg), "w", encoding="utf-8") as f:
            json.dump({"items": items, "updated": time.strftime("%Y-%m-%d %H:%M")},
                      f, ensure_ascii=False, indent=1)
        with open(catalog_html_path(cfg), "w", encoding="utf-8") as f:
            f.write(render_catalog_html(items))
    except Exception:                                    # noqa: BLE001
        logging.exception("商品目录写入失败")
    return items


def render_catalog_html(items: List[dict]) -> str:
    """自包含的商品目录页（缩略图 + 属性 + 进目录链接），双击就能看。

    ⚠️ 模板里 CSS 全是 `width:100%` 这种百分号，**绝不能用 `%` 格式化**
    （第一版就这么崩的：`ValueError: unsupported format character ';'`）。
    改用 `str.replace` 占位符，与内容里的 `%` 再无冲突。
    """
    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    cards = []
    for it in items:
        specs = it.get("specs") or {}
        sp = "".join("<tr><td>%s</td><td>%s</td></tr>" % (esc(k), esc(v))
                     for k, v in list(specs.items())[:8])
        thumb = it.get("thumb") or ""
        link = it.get("detail_html") or ""
        open_btn = ('<a class="btn" href="%s">打开详情页</a>' % esc(link)
                    if link else "")
        cards.append("""
      <div class="card">
        <div class="thumb">%s</div>
        <div class="body">
          <div class="nm">%s</div>
          <div class="meta">%s · %s</div>
          <table class="specs">%s</table>
          <div class="acts">
            <a class="btn" href="%s">打开文件夹</a>%s
          </div>
        </div>
      </div>""" % (('<img src="%s" alt="">' % esc(thumb)) if thumb else "（无图）",
                    esc(it.get("name") or "未命名"),
                    esc(it.get("category") or ""), esc(it.get("platform") or ""),
                    sp, esc(it.get("dir") or "."), open_btn))
    body = "".join(cards) or '<div class="sub">还没有产品，去电商工种生成第一个吧。</div>'
    return (_CATALOG_HTML
            .replace("__COUNT__", str(len(items)))
            .replace("__UPDATED__", esc(time.strftime("%Y-%m-%d %H:%M")))
            .replace("__CARDS__", body))


#: 目录页模板。占位符 `__COUNT__` / `__UPDATED__` / `__CARDS__` 用 replace 填，
#: 这样 CSS 里的百分号可以随便写（见 render_catalog_html 的坑注）。
_CATALOG_HTML = """<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>商品目录</title>
<style>
 body{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#f6f7f9;
      color:#222;margin:0;padding:24px}
 h1{font-size:22px;margin:0 0 4px}
 .sub{color:#777;font-size:13px;margin-bottom:20px}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));
       gap:16px}
 .card{background:#fff;border-radius:12px;overflow:hidden;
       box-shadow:0 1px 4px rgba(0,0,0,.08);display:flex;flex-direction:column}
 .thumb{height:180px;background:#eceff3;display:flex;align-items:center;
        justify-content:center;color:#999;font-size:13px}
 .thumb img{max-width:100%;max-height:100%;object-fit:contain}
 .body{padding:14px 16px}
 .nm{font-size:16px;font-weight:600;margin-bottom:4px}
 .meta{font-size:12px;color:#888;margin-bottom:8px}
 table.specs{width:100%;font-size:12px;border-collapse:collapse;margin-bottom:10px}
 table.specs td{padding:3px 0;border-bottom:1px solid #f0f0f0}
 table.specs td:first-child{color:#999;width:74px}
 .acts{display:flex;gap:8px}
 .btn{font-size:12px;padding:5px 12px;border-radius:6px;background:#2f6bff;
      color:#fff;text-decoration:none}
 .btn:hover{background:#1f56d6}
</style></head><body>
<h1>商品目录</h1>
<div class="sub">共 __COUNT__ 个产品 · 由 PASM Studio 电商套件生成 · 更新于 __UPDATED__</div>
<div class="grid">__CARDS__
</div></body></html>"""


def build_crops(category: str, n: int = 3) -> List[dict]:
    """局部图 / 细节图的裁切计划（**相对坐标**，渲染层会按原图尺寸换算）。

    默认按"能表达产品信息的位置"给一组候选：中心（主体质感）、上中（工艺/logo）、
    下中（结构/底部）。渲染层 `pick_detail_boxes()` 会用图像高频能量覆盖这里的
    `box` —— 这里只是**没有渲染能力时的兜底**，不是最终值。
    """
    plans = [
        {"name": "局部图-主体质感", "kind": "局部图", "box": [0.30, 0.30, 0.40, 0.40],
         "zoom": 1.8, "anno": "①"},
        {"name": "细节图-工艺/标识", "kind": "细节图", "box": [0.28, 0.10, 0.44, 0.44],
         "zoom": 2.4, "anno": "②"},
        {"name": "细节图-结构/底部", "kind": "细节图", "box": [0.28, 0.46, 0.44, 0.44],
         "zoom": 2.4, "anno": "③"},
        {"name": "细节图-侧边", "kind": "细节图", "box": [0.06, 0.28, 0.40, 0.44],
         "zoom": 2.2, "anno": "④"},
    ]
    return [dict(p, caption="%s实拍放大，看得见%s" % (p["kind"], category))
            for p in plans[:max(1, min(4, int(n)))]]


#: 类目 → 应当具备的资质/检测（**列的是"应该有"，不是"已经有了"** ——
#: 电商合规是专业度的底线：食品/美妆/母婴/宠物少一样就可能被下架或罚款）。
QUALIFICATION = {
    "饰品": ["材质与含量标识（如 S925 / 18K / 足金 标注）", "贵金属或珠宝检测证书（如 CMA）",
             "过敏与佩戴注意说明（镍释放/防过敏）", "保养与清洗说明"],
    "食品": ["食品生产许可证（SC 编号）", "第三方检测报告", "配料表 / 营养成分表",
             "保质期与储存条件标注"],
    "美妆": ["国产普通化妆品备案号（或特殊化妆品批准文号）", "全成分表",
             "生产许可证（如粤妆/沪妆）", "生产日期与保质期"],
    "母婴": ["3C 认证（适用品类）", "纺织品安全类别标识（A 类为婴幼儿）",
             "材质检测报告", "适用年龄与警示语"],
    "宠物": ["饲料生产许可证 / 宠物食品备案", "原料成分与保证值",
             "第三方质检报告", "开封后储存说明"],
    "数码": ["3C 认证", "锂电池运输/安全认证（含电池时）",
             "保修条款与售后网点", "接口/协议兼容性说明"],
    "家居": ["甲醛 / 环保检测报告（家具类）", "承重与尺寸公差说明",
             "材质与工艺说明", "安装说明与配件清单"],
    "服装": ["纤维成分与含量标识（GB/T 5296.4）", "执行标准号",
             "安全类别（A/B/C 类）", "洗涤与保养标识"],
    "通用": ["营业执照 / 品牌授权（如涉及）", "产品检测报告",
             "售后与退换规则", "生产日期与产地信息"],
}

#: 离线关键词兜底：**按类目给的"人话搜索词"**（有模型时由模型产出，这里是保底）。
#  ★ 铁律：**绝不把用户原句机械切片当关键词**。v0.31.27 真机实测过 ——
#  旧实现用 `re.findall(r"[\u4e00-\u9fff]{2,6}", text)` 切片，得到的是
#  「上述的图中项 / 我需要你帮我 / 出五张主图 / 包括副图和细」这类**完全不可用**的
#  碎片：既不是买家会打的搜索词，也不能进标题。
CATEGORY_KEYWORDS = {
    "饰品": ["项链女", "锁骨链", "轻奢饰品", "高级感", "生日礼物", "送女友", "小众设计", "日常百搭"],
    "服装": ["显瘦", "百搭", "秋冬", "保暖", "通勤", "宽松", "气质", "日常"],
    "食品": ["休闲零食", "独立包装", "办公室零食", "好吃不腻", "送礼", "即食"],
    "数码": ["无线", "快充", "便携", "高性价比", "办公", "学生", "降噪", "长续航"],
    "家居": ["收纳", "省空间", "北欧", "简约", "家用", "出租房", "实用", "免安装"],
    "美妆": ["补水", "温和", "敏感肌", "持妆", "平价", "学生党", "日常妆", "便携装"],
    "母婴": ["婴儿", "安全材质", "新手爸妈", "柔软", "易清洗", "待产包", "亲肤"],
    "宠物": ["宠物用品", "猫咪", "狗狗", "无毒", "耐咬", "易清洁", "适口性好"],
    "通用": ["正品", "热销", "新款", "实用", "送礼", "高性价比", "包邮", "现货"],
}


#: 「卖点 → 利益」句式（离线模板；有模型时由模型写，来源照旧如实标注）
_BENEFIT_TPL = (
    "%(head)s到位 → 到手就能用，少折腾",
    "%(head)s扎实 → 用得久，摊到每天更划算",
    "%(head)s讲究 → 拿得出手，送人也不失礼",
    "%(head)s省事 → 打理简单，时间花在别处",
    "%(head)s放心 → 售后有人管，买着没负担",
)

#: FAQ：通用 5 条（成交前最常被问的）
_FAQ_COMMON = (
    ("发货时效怎么算？",
     "付款后 48 小时内发出（预售以页面标注为准）；发货后可在订单页看物流轨迹。"),
    ("尺寸 / 规格怎么选？",
     "对照下方「产品参数」选；拿不准就把使用场景告诉客服，按你的场景给建议。"),
    ("支持退换吗？",
     "支持 7 天无理由退换（不影响二次销售）；质量问题由商家承担来回运费。"),
    ("有正品保障和发票吗？",
     "正品保障、可验货；需要发票在下单时备注或联系客服开具。"),
    ("怎么保养 / 使用？",
     "按详情页「3 步用起来」操作即可；后续有疑问随时联系客服。"),
)
#: FAQ：类目专属（补在通用后面）
_FAQ_CAT = {
    "食品": (("保质期多久？开封后怎么存放？",
              "以包装标注为准；建议开封后密封、阴凉干燥处存放并尽快食用。"),),
    "美妆": (("敏感肌能用吗？",
              "请先看全成分表并做耳后试用；出现不适立即停用。"),),
    "母婴": (("宝宝多大能用？材质安全吗？",
              "按标注的适用年龄使用；材质与安全类别见资质与检测一屏。"),),
    "宠物": (("我家宠物挑食能吃吗？换粮要注意什么？",
              "建议 7 天过渡换粮法，逐步替换旧粮；不适请停喂并咨询兽医。"),),
    "数码": (("兼容我的设备吗？保修多久？",
              "对照参数表的接口与协议；质保周期以页面标注为准。"),),
    "服装": (("会缩水 / 掉色吗？尺码偏大偏小？",
              "按洗标护理可减少缩水掉色；版型与实测尺寸见参数表。"),),
    "家居": (("尺寸会不会对不上？承重多少？",
              "下单前量好预留空间并对照尺寸公差；承重数据见参数表。"),),
    "通用": (),
}
#: 使用/收货 3 步（类目感知；不写"三步成仙"这种空话）
_STEPS_CAT = {
    "服装": ["对照尺码表量好三围选码（拿不准发客服）",
             "收到先试穿，**别剪吊牌**，不合适好退换",
             "确认合适再剪标洗涤，按洗标护理"],
    "食品": ["收到先检查包装是否完好、有无漏气涨袋",
             "按包装标注的储存条件存放",
             "开封后密封并尽快食用"],
    "数码": ["开箱核对配件与序列号，保留包装至少 15 天",
             "首次使用先充满电 / 完成配对",
             "绑定保修并保存发票（售后凭它）"],
    "家居": ["先对照尺寸与预留空间复核一遍",
             "按说明清点配件再安装，避免多拆",
             "装好后检查稳固性，再正常使用"],
    "美妆": ["先看全成分表，耳后试用 24 小时",
             "按标注用量与频次使用",
             "开封后注意保质期，避光存放"],
    "母婴": ["核对适用年龄与警示语再给宝宝使用",
             "首次使用前按说明清洗/消毒",
             "定期检查磨损，及时更换"],
    "宠物": ["按体重对照建议喂食量",
             "换粮用 7 天过渡法逐步替换",
             "观察进食与便便状态，异常停喂并咨询兽医"],
    "通用": ["收到先检查外观与配件是否齐全",
             "按说明书使用，注意标注的禁忌与限制",
             "留好包装与凭证，售后更省事"],
}
#: 包装与物流（**草稿**：这类承诺必须商家按自己实际发货方式核对后再用）
_LOGISTICS = ["商品独立包装，防潮防挤压",
              "外箱加厚加固，长途运输不易破损",
              "支持主流快递，偏远地区以页面物流为准",
              "大批量 / 定制需求可联系客服走专属通道"]


MAIN_ROLES = ("白底主图", "场景图", "卖点图", "细节图", "参数图")


def build_main_images(product: str, category: str, points: List[dict],
                      crops: List[dict], platform: str = "taobao",
                      n: int = 5) -> List[dict]:
    """主图 5 张的分工（第 1 张白底进搜索位，后 4 张依次承担说服任务）。"""
    spec = platform_spec(platform)
    w, h = spec["main_size"]
    head = [(points[i]["head"] if i < len(points) else "") for i in range(6)]
    caps = [
        (product, "纯白底、产品居中占画幅 70~80%，不放促销文字"),
        ("%s · 使用场景" % product,
         "真实场景衬托，交代尺寸感与使用方式"),
        (head[0] or "核心卖点", "一图一卖点，大字 3~6 个字 + 一句注解"),
        ((crops[0]["kind"] if crops else "细节") + "特写",
         "实拍局部放大，标 ①②③ 呼应详情页"),
        ((head[1] or "规格参数") + " / 服务保障",
         "参数表 + 服务承诺（7天无理由 / 发货时效）"),
    ]
    out = []
    for i in range(max(1, min(6, int(n)))):
        idx = i + 1
        title, sub = caps[i] if i < len(caps) else (product, "")
        out.append({
            "idx": idx, "role": MAIN_ROLES[i] if i < len(MAIN_ROLES) else "补充图",
            "title": title, "sub": sub, "size": "%dx%d" % (w, h),
            "layout": ("plain" if i == 0 else
                       "scene" if i == 1 else
                       "sell" if i == 2 else
                       "detail" if i == 3 else "spec"),
            "white_bg": bool(i == 0 and spec.get("first_white")),
        })
    return out


def build_detail_sections(product: str, category: str, points: List[dict],
                          crops: List[dict], spec_rows, brand: str = "") -> List[dict]:
    """详情页屏序（自上而下）。每屏都是**可渲染的**结构，不是一句描述。"""
    cat = CATEGORIES.get(category) or CATEGORIES["通用"]
    return [
        {"type": "banner", "title": product,
         "sub": "%s%s" % (brand + " · " if brand else "", category),
         "body": (points[0]["body"] if points else "")},
        {"type": "pain", "title": "你是不是也担心这几个问题？",
         "items": list(cat["pains"])},
        {"type": "points", "title": "为什么选它",
         "items": [{"head": p["head"], "body": p["body"]} for p in points]},
        {"type": "detail", "title": "细节放大看",
         "items": [{"caption": c["caption"], "anno": c["anno"]} for c in crops]},
        {"type": "benefit", "title": "这几条，对你怎么用得上",
         "items": build_benefits(points, crops)},
        {"type": "steps", "title": "3 步用起来", "items": build_steps(category)},
        {"type": "scene", "title": "使用场景",
         "body": "%s 在真实使用场景下的样子——尺寸感、搭配感、代入感一次看清。" % product},
        {"type": "spec", "title": "产品参数", "rows": spec_rows},
        {"type": "faq", "title": "常见问题", "items": build_faq(category, product)},
        {"type": "logistics", "title": "包装与物流", "items": list(_LOGISTICS),
         "draft": True},
        {"type": "qualification", "title": "资质与检测（该类目应当具备）",
         "items": qualification_for(category)},
        {"type": "service", "title": "购买保障",
         "items": ["7 天无理由退换（不影响二次销售）", "正品保障，支持验货",
                   "48 小时内发货（预售以页面标注为准）",
                   "专业客服 1v1，售前售后都在"]},
    ]


def build_copy(product: str, category: str, points: List[dict], kws: List[str],
               price: str = "", llm_fn: Optional[Callable] = None,
               facts: str = "") -> dict:
    """详情文案四件套：短描述 / 长描述 / 直播口播 / SEO 关键词串。

    `facts`：产品档案摘要。文案要想具体，就得把真实参数喂给模型。
    """
    if callable(llm_fn):
        try:
            ask = ("你是电商详情页文案。为下面商品写 4 段，用下面的分隔标签输出，"
                   "不要违禁词（最/第一/顶级/根治等）：\n"
                   "[短]40~60字卖点概述\n[长]200~300字详情描述，分 2~3 小段\n"
                   "[口播]80~120字直播间口播稿，口语化\n[SEO]10 个搜索关键词，用空格分隔\n"
                   "商品：%s\n类目：%s\n已知卖点：%s%s"
                   % (product, category,
                      "；".join("%s-%s" % (p["head"], p["body"]) for p in points),
                      ("\n" + facts) if facts else ""))
            r = str(llm_fn(ask) or "")
            got = {}
            cur = ""
            for ln in r.splitlines():
                m = re.match(r"\s*[\[【](短|长|口播|seo|SEO)[\]】]\s*(.*)", ln)
                if m:
                    cur = m.group(1).lower()
                    got[cur] = m.group(2).strip()
                elif cur and ln.strip():
                    got[cur] = (got.get(cur, "") + " " + ln.strip()).strip()
            if got.get("短") and got.get("长"):
                return {"short": got.get("短", ""), "long": got.get("长", ""),
                        "live": got.get("口播", ""), "seo": got.get("seo", ""),
                        "live_script": build_live_script(product, points, price),
                        "seo_layers": build_seo_layers(kws, product),
                        "source": "llm"}
        except Exception:                       # noqa: BLE001
            pass
    p_txt = "；".join("%s：%s" % (p["head"], p["body"]) for p in points[:3])
    short = "%s。%s" % (product, p_txt)[:120]
    long_txt = (
        "%s\n\n【为什么值得买】%s\n\n"
        "【看得见的细节】每一处材质与做工都以实拍图呈现，"
        "不靠渲染图说话；下单前可对照参数表核对尺寸与规格。\n\n"
        "【购买与售后】%s 支持 7 天无理由退换，正品保障；"
        "如有疑问随时联系客服，我们会给出明确答复。"
        % (product, p_txt,
           ("到手价 %s。" % price) if price else ""))
    live = ("家人们看过来，这个是我们的%s。%s。"
            "今天这个价是真的合适，喜欢直接拍，有问题扣1我给大家解答。"
            % (product, p_txt))[:200]
    return {"short": short, "long": long_txt, "live": live,
            "seo": " ".join(kws), "live_script": build_live_script(product, points, price),
            "seo_layers": build_seo_layers(kws, product), "source": "offline"}


def qualification_for(category: str) -> List[str]:
    """该类目**应当具备**的资质/检测清单（列的是"应该有"，由商家补齐）。"""
    return list(QUALIFICATION.get(category) or QUALIFICATION["通用"])


def build_benefits(points: List[dict], crops: List[dict]) -> List[dict]:
    """卖点 → 利益 → 证据。专业详情页真正缺的不是卖点，是"这跟我有什么关系"。"""
    out = []
    for i, p in enumerate(points or []):
        head = str(p.get("head") or "")
        out.append({
            "point": head,
            "benefit": _BENEFIT_TPL[i % len(_BENEFIT_TPL)] % {"head": head},
            "proof": ("细节图%s" % (crops[i]["anno"] if i < len(crops) else "①")
                      + " / 产品参数表"),
        })
    return out


def build_faq(category: str, product: str = "") -> List[dict]:
    """FAQ：通用 5 条 + 类目专属（把成交前的犹豫直接答掉）。"""
    rows = [{"q": q, "a": a} for q, a in _FAQ_COMMON]
    for q, a in (_FAQ_CAT.get(category) or ()):
        rows.append({"q": q, "a": a})
    return rows[:6]


def build_steps(category: str) -> List[str]:
    return list(_STEPS_CAT.get(category) or _STEPS_CAT["通用"])


def build_live_script(product: str, points: List[dict], price: str = "") -> dict:
    """分时段直播话术（开场/讲品/逼单/处理异议）—— 抖音/直播电商真正每天要用的东西。"""
    p3 = "；".join("%s（%s）" % (x.get("head", ""), str(x.get("body") or "")[:22])
                   for x in (points or [])[:3])
    price_txt = ("今天直播间价 %s" % price) if price else "今天直播间的价很合适"
    return {
        "open": ("欢迎新进来的家人们，先别急着划走——今天这个是%s，"
                 "%s，我一会逐条给你们看实物。" % (product, price_txt)),
        "pitch": ("先说三个点：%s。每一条我都对着实物给你们看细节，"
                  "不玩虚的。" % p3),
        "close": ("想要的现在拍，%s，48 小时内发货，7 天无理由退换，"
                  "不合适退回来运费我们出。库存不多，拍完这批就没了。"
                  % price_txt),
        "trouble": ("看到有人问 XX——这个我直接说：能就是能、不能就是不能，"
                    "不能的我给你换方案，别花冤枉钱。"),
    }


def build_seo_layers(keywords: List[str], product: str = "") -> dict:
    """搜索词分三层：核心词（打主词）/ 属性词（卡筛选）/ 长尾词（捡精准流量）。"""
    kws = list(keywords or [])
    core = kws[:2] or ([product] if product else [])
    attr = [k for k in kws[2:6]]
    tail = ["%s %s" % (core[0] if core else product, k)
            for k in (kws[:4] or [product])]
    return {"core": core, "attr": attr, "long_tail": tail[:5]}


def build_title_variants(product: str, keywords: List[str], category: str = "通用",
                         brand: str = "", platform: str = "taobao",
                         llm_fn=None) -> List[dict]:
    """3 版标题做 A/B：核心词前置 / 场景前置 / 口碑前置（都过平台字数控）。"""
    kws = list(keywords or [])

    def _mk(parts):
        """拼标题：去重 + 压空格（上一版会出现双空格、品牌重复两遍）。"""
        seen, out = set(), []
        for p in parts:
            p = str(p or "").strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return re.sub(r"\s+", " ", " ".join(out)).strip()

    v = [
        ("核心词前置", _mk([brand, product] + kws[:4] + ["正品"])),
        ("场景前置", _mk([brand] + (kws[:2] or [product]) + [product, "正品"])),
        ("口碑前置", _mk([brand, product, "热销款"] + kws[2:6])),
    ]
    out = []
    for name, t in v:
        o = optimize_title(t, [], category, "", platform)
        t = re.sub(r"\s+", " ", str(t or "").strip())
        out.append({"name": name, "title": t,
                    "len": title_len(t), "ok": title_len(t) <= o["limit"],
                    "limit": o["limit"]})
    # 违禁词体检：A/B 版本也不许带极限词（否则等于给了个违规选项）
    for x in out:
        x["violations"] = [h["word"] for h in check_forbidden(x["title"])]
    return out[:3]


def build_sku_guide(category: str, product: str = "") -> str:
    """SKU/规格建议 —— 减少"选错规格"导致的退货。"""
    tpl = {
        "服装": "按身高体重+胸围选码，拿不准选大一码；提供 S~3XL 分码与颜色组合。",
        "食品": "按人数与食用频次设规格（单包 / 家庭装 / 囤货装），标注净含量对比。",
        "数码": "按接口与容量设 SKU，明确标注兼容机型，避免买错接口。",
        "家居": "按尺寸与颜色设 SKU，详情页写清最大/最小规格与预留空间。",
        "美妆": "按容量与套装设 SKU，注明开封后保质期（PAO）。",
        "母婴": "按适用年龄/尺码设 SKU，标注材质与安全类别差异。",
        "宠物": "按体重段 / 口味 / 规格设 SKU，提供试吃小包降低首单门槛。",
        "通用": "把「最常被问到的差异点」做成 SKU（规格/颜色/版本），并在详情页逐一说明。",
    }
    return tpl.get(category) or tpl["通用"]


def build_plan(text: str = "", vision: str = "", photos=None,
               platform: str = DEFAULT_PLATFORM, category: str = "",
               brand: str = "", price: str = "", core: str = "",
               n_main: int = 5, n_crops: int = 3,
               llm_fn: Optional[Callable] = None,
               specs_extra=None, profile: Optional[dict] = None) -> dict:
    """一次生成完整电商方案（本模块的**唯一主入口**）。

    所有字段都是"可渲染"的结构；`sources` 里如实标注每块内容的来源
    （`llm` = 大模型写的 / `offline` = 类目模板拼的 / `profile` = 用户录的产品档案）。

    `profile`：产品档案（`new_profile` + `profile_apply_text` 攒出来的那份）。
    传了就等于告诉方案"这些参数是真的"——参数表填实、卖点去模板化。
    """
    text = str(text or "")
    vision = str(vision or "")
    pid = norm_platform(platform)
    spec = platform_spec(pid)
    cat = str(category or "").strip() or detect_category(text, vision)
    if cat not in CATEGORIES:
        cat = "通用"
    # 用户常在正文里写「品牌：X 价格：Y」——不额外要求他再填一遍表单
    if not brand:
        brand = extract_field(text, ("品牌", "牌子"))[:16]
    if not price:
        _m = re.search(r"(?:价格|售价|到手价|单价)\s*[:：]?\s*[¥￥]?\s*(\d+(?:\.\d+)?)",
                       text)
        price = _m.group(1) if _m else ""
    product = _pick_product_name(text, cat)
    # ★ v0.31.28：关键词**走模型**产出（拿不到才用类目词表兜底）。
    #   旧实现是把用户原句按 2~6 字机械切片 → 真机产出「上述的图中项 / 我需要你帮我」
    #   这种完全不可用的碎片（回复的"来源标注"里唯独没有关键词，就是这条线索）。
    # ★ v0.31.30：有产品档案时，把真实属性喂给模型（材质=S925银 这种），
    #   它写出来的卖点/文案才是具体的，不再是同一句模板话重复 5 遍。
    _pfacts = profile_facts_for_llm(profile) if profile else ""
    kws, kws_src = build_keywords(product, cat, text, vision, 8, llm_fn)
    t = optimize_title(product, kws, cat, brand, pid, core, llm_fn)
    points = build_selling_points(text, vision, cat, llm_fn, n=5, facts=_pfacts)
    crops = build_crops(cat, n_crops)
    rows = build_spec_rows(cat, text, brand, price, specs_extra)
    copy = build_copy(product, cat, points, kws, price, llm_fn, facts=_pfacts)
    violations = check_forbidden(" ".join(
        [t["title"]] + ["%s%s" % (p["head"], p["body"]) for p in points] +
        [copy["short"], copy["long"], copy["live"]]))
    _plan = {
        "kind": "ecom",
        "platform": pid, "platform_label": spec["label"], "spec": spec,
        "category": cat, "brand": str(brand or ""), "price": str(price or ""),
        "product": product,
        "photos": [str(p) for p in (photos or []) if p],
        "title": t, "keywords": kws, "selling_points": points,
        "crops": crops, "spec_rows": rows,
        "main_images": build_main_images(product, cat, points, crops, pid, n_main),
        "detail_sections": build_detail_sections(product, cat, points, crops, rows, brand),
        "copy": copy, "violations": violations,
        # v0.31.27 专业级增补：利益/FAQ/步骤/资质/SKU/标题 A-B
        "benefits": build_benefits(points, crops),
        "faq": build_faq(cat, product),
        "steps": build_steps(cat),
        "qualification": qualification_for(cat),
        "sku_guide": build_sku_guide(cat, product),
        "title_variants": build_title_variants(product, kws, cat, brand, pid),
        "sources": {"title": t["source"], "keywords": kws_src,
                    "selling_points":
                    ("llm" if any(p.get("source") == "llm" for p in points) else "offline"),
                    "copy": copy["source"]},
        "created": time.strftime("%Y-%m-%d %H:%M"),
        "notes": [
            "产品本体（主图/局部图/细节图）**只用你喂的实拍图真实合成与裁切**，"
            "不用 AI 重画 —— 避免平台判「图文不符」。",
            "场景/氛围底图属可选项：配了图像引擎才会生成，否则如实标「待生成」。",
        ] + ([("%s：%s" % (v["group"], v["word"])) for v in violations] and
             ["⚠️ 文案里检测到违禁词风险，见下方「违禁词体检」，建议按提示替换。"] or []),
    }
    # ★ v0.31.30：档案回填（参数表填实 + 卖点去模板化）——录了信息就得兑现
    apply_profile_to_plan(_plan, profile)
    return _plan


# --------------------------------------------------------------------------
# 交付物：方案 Markdown / 详情页 HTML
# --------------------------------------------------------------------------
def _cell(v: str) -> str:
    return str(v or "").replace("|", "\\|")


def render_plan_md(plan: dict) -> str:
    """把方案写成可交付、可改稿的 Markdown。"""
    p = plan or {}
    L: List[str] = []
    L.append("# 🛒 电商视觉方案 · %s" % p.get("platform_label", ""))
    L.append("")
    L.append("- 商品：**%s**　类目：%s%s" %
             (p.get("product", ""), p.get("category", ""),
              ("　品牌：" + p["brand"]) if p.get("brand") else ""))
    L.append("- 生成时间：%s" % p.get("created", ""))
    t = p.get("title") or {}
    L.append("- 标题：`%s`（%d/%d 字符%s）" %
             (t.get("title", ""), t.get("len", 0), t.get("limit", 60),
              "，**超限**，建议用截断版" if not t.get("ok") else ""))
    if not t.get("ok"):
        L.append("- 截断版标题：`%s`" % t.get("trimmed", ""))
    L.append("- 内容来源：标题=%s / 卖点=%s / 文案=%s（offline = 类目模板拼装）"
             % (p.get("sources", {}).get("title", "?"),
                p.get("sources", {}).get("selling_points", "?"),
                p.get("sources", {}).get("copy", "?")))
    L.append("")
    L.append("## 一、关键词")
    L.append(" ".join("`%s`" % k for k in (p.get("keywords") or [])) or "（无）")
    L.append("")
    L.append("> 详情区**两种都能直接上传**：整张 750 宽的详情长图，以及按屏切好的"
             "独立详情图（一屏一张）。你说「要 N 张详情图」时，切分张数按 N 来。")
    L.append("")
    L.append("## 二、主图 %d 张分工" % len(p.get("main_images") or []))
    L.append("")
    L.append("| # | 角色 | 画面要点 | 尺寸 |")
    L.append("|---|---|---|---|")
    for m in (p.get("main_images") or []):
        L.append("| %d | %s | %s（%s） | %s |" %
                 (m["idx"], m["role"], _cell(m["title"]), _cell(m["sub"]), m["size"]))
    L.append("")
    L.append("## 三、局部图 / 细节图裁切计划")
    L.append("")
    L.append("| 编号 | 名称 | 裁切区域（相对坐标 x,y,w,h） | 放大 |")
    L.append("|---|---|---|---|")
    for c in (p.get("crops") or []):
        L.append("| %s | %s | `%s` | ×%.1f |" %
                 (c["anno"], c["name"], ",".join("%.2f" % x for x in c["box"]),
                  float(c["zoom"])))
    L.append("")
    L.append("## 四、卖点")
    for i, s in enumerate(p.get("selling_points") or [], 1):
        L.append("%d. **%s** —— %s" % (i, s["head"], s["body"]))
    L.append("")
    L.append("## 五、详情页屏序")
    for i, sec in enumerate(p.get("detail_sections") or [], 1):
        L.append("%d. 【%s】%s" % (i, sec.get("type", ""), sec.get("title", "")))
    L.append("")
    L.append("## 六、参数表")
    L.append("")
    L.append("| 参数 | 值 |")
    L.append("|---|---|")
    for r in (p.get("spec_rows") or []):
        L.append("| %s | %s |" % (_cell(r[0]), _cell(r[1])))
    L.append("")
    L.append("## 七、详情文案")
    c = p.get("copy") or {}
    L.append("**短描述**：%s" % c.get("short", ""))
    L.append("")
    L.append("**长描述**：")
    L.append("")
    L.append(c.get("long", ""))
    L.append("")
    L.append("**直播口播**：%s" % c.get("live", ""))
    L.append("")
    L.append("**SEO 关键词**：%s" % c.get("seo", ""))
    L.append("")
    L.append("## 八、卖点 → 利益 → 证据")
    L.append("")
    L.append("| 卖点 | 对你的好处 | 去哪看 |")
    L.append("|---|---|---|")
    for b in (p.get("benefits") or []):
        L.append("| %s | %s | %s |" % (_cell(b.get("point", "")),
                                       _cell(b.get("benefit", "")),
                                       _cell(b.get("proof", ""))))
    L.append("")
    L.append("## 九、使用 / 收货步骤")
    for i, st in enumerate(p.get("steps") or [], 1):
        L.append("%d. %s" % (i, st))
    L.append("")
    L.append("## 十、常见问题（FAQ）")
    L.append("")
    for q in (p.get("faq") or []):
        L.append("**Q：%s**" % q.get("q", ""))
        L.append("")
        L.append("A：%s" % q.get("a", ""))
        L.append("")
    L.append("## 十一、资质与检测（该类目**应当**具备，需你补齐）")
    for x in (p.get("qualification") or []):
        L.append("- [ ] %s" % x)
    L.append("")
    L.append("## 十二、SKU / 规格建议")
    L.append("")
    L.append(p.get("sku_guide", ""))
    L.append("")
    _sl = (p.get("copy") or {}).get("seo_layers") or {}
    L.append("## 十三、SEO 搜索词分层")
    L.append("")
    L.append("- **核心词**（打主词）：%s" % "、".join(_sl.get("core") or []))
    L.append("- **属性词**（卡筛选）：%s" % "、".join(_sl.get("attr") or []))
    L.append("- **长尾词**（捡精准流量）：%s" % "、".join(_sl.get("long_tail") or []))
    L.append("")
    L.append("## 十四、标题 A/B 备选")
    L.append("")
    L.append("| 版本 | 标题 | 字符 | 违禁词 |")
    L.append("|---|---|---|---|")
    for v in (p.get("title_variants") or []):
        L.append("| %s | %s | %d/%d | %s |"
                 % (v.get("name", ""), _cell(v.get("title", "")),
                    v.get("len", 0), v.get("limit", 60),
                    ("、".join(v.get("violations") or []) or "✅ 无")))
    L.append("")
    _lv = (p.get("copy") or {}).get("live_script") or {}
    L.append("## 十五、直播话术（分时段）")
    L.append("")
    L.append("- **开场**：%s" % _lv.get("open", ""))
    L.append("- **讲品**：%s" % _lv.get("pitch", ""))
    L.append("- **逼单**：%s" % _lv.get("close", ""))
    L.append("- **处理异议**：%s" % _lv.get("trouble", ""))
    L.append("")
    L.append("## 十六、违禁词体检")
    if p.get("violations"):
        L.append("")
        L.append("| 分组 | 词 | 建议 |")
        L.append("|---|---|---|")
        for v in p["violations"]:
            L.append("| %s | %s | %s |" % (v["group"], v["word"], v["hint"]))
    else:
        L.append("")
        L.append("✅ 未检测到违禁词风险（词表覆盖广告法极限词 / 虚假承诺 / 医疗功效 / 金融收益）。")
    L.append("")
    L.append("## 十七、诚实说明")
    for n in (p.get("notes") or []):
        L.append("- %s" % n)
    return "\n".join(L)


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _as_uri(path: str) -> str:
    p = str(path or "")
    if not p:
        return ""
    try:
        import urllib.parse
        from pathlib import Path
        return urllib.parse.quote(Path(p).as_uri(), safe=":/%.@-")
    except Exception:                           # noqa: BLE001
        return "file:///" + p.replace("\\", "/")


def render_detail_html(plan: dict, images=None, out_path: str = "") -> str:
    """详情页 HTML（版心 = 平台 detail_width），浏览器可直接打开当预览。

    `images` = {"banner": path, "main": path, "crops": [path,...], "scene": path}
    没有图时用占位块并**如实写「待生成」**，不画假图。
    """
    p = plan or {}
    spec = p.get("spec") or platform_spec(p.get("platform", DEFAULT_PLATFORM))
    W = int(spec["detail_width"])
    imgs = dict(images or {})
    crops_img = [str(x) for x in (imgs.get("crops") or []) if x]

    def _img_block(path, note):
        if path:
            return "<img src='%s' alt='%s'>" % (_as_uri(path), _esc(note))
        return ("<div class='ph'>%s<br><span class='phi'>待生成（未配图像引擎或未提供实拍图）</span></div>"
                % _esc(note))

    blocks: List[str] = []
    for sec in (p.get("detail_sections") or []):
        st, title = sec.get("type"), sec.get("title", "")
        if st == "banner":
            blocks.append("<section class='hero'>%s<div class='cap'><h1>%s</h1><h2>%s</h2>"
                          "<div class='hr'></div></div></section>"
                          % (_img_block(imgs.get("banner") or imgs.get("main"), "主视觉"),
                             _esc(title), _esc(sec.get("sub", ""))))
            # ★ v0.31.31：AI 精修的主视觉大图（有就单独一屏，视觉冲击更接近设计稿）
            if imgs.get("ai_cover"):
                blocks.append("<section class='sec cover'>%s</section>"
                              % _img_block(imgs["ai_cover"], "主视觉大图（AI 精修）"))
        elif st == "pain":
            items = "".join("<li>%s</li>" % _esc(x) for x in sec.get("items") or [])
            blocks.append("<section class='sec'><h3>%s</h3><ul class='pain'>%s</ul></section>"
                          % (_esc(title), items))
        elif st == "points":
            rows = []
            for _i, it in enumerate(sec.get("items") or [], 1):
                rows.append("<div class='pt'><i class='idx'>%02d</i>"
                            "<div class='ptb'><b>%s</b><p>%s</p></div></div>"
                            % (_i, _esc(it.get("head", "")), _esc(it.get("body", ""))))
            blocks.append("<section class='sec'><h3>%s</h3>%s</section>"
                          % (_esc(title), "".join(rows)))
        elif st == "detail":
            cells = []
            for i, it in enumerate(sec.get("items") or []):
                ip = crops_img[i] if i < len(crops_img) else ""
                cells.append("<div class='crop'>%s<p><span class='anno'>%s</span> %s</p></div>"
                             % (_img_block(ip, "细节图 %s" % it.get("anno", "")),
                                _esc(it.get("anno", "")), _esc(it.get("caption", ""))))
            blocks.append("<section class='sec'><h3>%s</h3><div class='crops'>%s</div></section>"
                          % (_esc(title), "".join(cells)))
        elif st == "scene":
            blocks.append("<section class='sec'>%s<h3>%s</h3><p>%s</p></section>"
                          % (_img_block(imgs.get("scene"), "场景图"), _esc(title),
                             _esc(sec.get("body", ""))))
        elif st == "spec":
            _ok_rows = [r for r in (sec.get("rows") or [])
                        if str(r[1]).strip() and str(r[1]).strip() not in ("待补充", "待定", "未知")]
            if not _ok_rows:
                continue                      # ★ 一项参数都没有 → 整块不渲染（"没有就不要"）
            rows = "".join("<tr><th>%s</th><td>%s</td></tr>" % (_esc(r[0]), _esc(r[1]))
                           for r in _ok_rows)
            blocks.append("<section class='sec'><h3>%s</h3>"
                          "<table class='spec'>%s</table></section>" % (_esc(title), rows))
        elif st == "service":
            items = "".join("<li>%s</li>" % _esc(x) for x in sec.get("items") or [])
            blocks.append("<section class='sec service'><h3>%s</h3><ul>%s</ul></section>"
                          % (_esc(title), items))
        else:
            # ★ 通用渲染（v0.31.27）：以前这里是**硬编码白名单**，于是新加的
            #   benefit / steps / faq / logistics / qualification 五屏被**静默丢掉**
            #   ——数据都造好了，页面上一条都看不到（自检抓到）。
            #   现在未知屏型一律按"标题 + 段落/列表/表格"渲染，再加屏也不会丢。
            inner = ""
            if sec.get("body"):
                inner += "<p>%s</p>" % _esc(sec.get("body"))
            for r in sec.get("rows") or []:
                inner += "<table class='spec'><tr>" + "".join(
                    "<%s>%s</%s>" % (("th" if i == 0 else "td"), _esc(x),
                                     ("th" if i == 0 else "td"))
                    for i, x in enumerate(r)) + "</tr></table>"
            for it in sec.get("items") or []:
                if isinstance(it, dict) and it.get("q"):
                    inner += ("<div class='pt'><b>%s</b><p>%s</p></div>"
                              % (_esc(it["q"]), _esc(it.get("a", ""))))
                elif isinstance(it, dict):
                    inner += ("<div class='pt'><b>%s</b><p>%s</p>%s</div>"
                              % (_esc(it.get("head") or it.get("point") or ""),
                                 _esc(it.get("body") or it.get("benefit") or ""),
                                 ("<p class='proof'>怎么看：%s</p>"
                                  % _esc(it["proof"])) if it.get("proof") else ""))
                else:
                    inner += "<div class='pt'><p>%s</p></div>" % _esc(it)
            _tag = ("<div class='tag'>✎ 草稿：请按你的实际情况核对后再用</div>"
                    if sec.get("draft") else "")
            blocks.append("<section class='sec'><h3>%s</h3>%s%s</section>"
                          % (_esc(title), _tag, inner))
    html = """<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<title>%(t)s</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#eceef2;font-family:"Microsoft YaHei",system-ui,sans-serif;color:#111827;
  -webkit-font-smoothing:antialiased}
.wrap{width:%(W)dpx;margin:0 auto;background:#fff;box-shadow:0 8px 40px rgba(15,23,42,.12)}
/* ---- 主视觉 ---- */
.hero{background:linear-gradient(180deg,#fdfdff 0%%,#f1f4f9 100%%);padding-bottom:18px}
.hero img{width:100%%;display:block;border-radius:0}
.hero .cap{padding:24px 26px 6px;text-align:center}
.hero h1{font-size:27px;line-height:1.42;font-weight:800;letter-spacing:.3px;color:#0f172a}
.hero h2{font-size:14.5px;color:#6b7280;font-weight:400;margin-top:10px;line-height:1.7}
.hr{width:56px;height:3px;border-radius:2px;margin:16px auto 0;
  background:linear-gradient(90deg,#0ea5e9,#6366f1)}
/* ---- 分区 ---- */
.sec{padding:26px 24px;border-top:1px solid #f1f3f7}
.sec.cover{padding:0;border-top:none}
.sec.cover img{border-radius:0}
h3{font-size:19px;font-weight:700;display:flex;align-items:center;gap:9px;
  margin-bottom:16px;color:#0f172a;letter-spacing:.2px}
h3:before{content:"";width:5px;height:19px;border-radius:3px;flex:0 0 5px;
  background:linear-gradient(180deg,#0ea5e9,#4f46e5)}
img{width:100%%;display:block;border-radius:12px}
.ph{height:320px;display:flex;flex-direction:column;align-items:center;justify-content:center;
  background:repeating-linear-gradient(45deg,#f8fafc,#f8fafc 12px,#f1f5f9 12px,#f1f5f9 24px);
  color:#94a3b8;border-radius:12px;font-size:15px}
.phi{font-size:12px}
/* ---- 卖点卡 ---- */
.pt{display:flex;gap:13px;padding:15px 16px;margin-bottom:11px;background:#fbfcfe;
  border:1px solid #eef2f7;border-radius:12px;box-shadow:0 1px 2px rgba(16,24,40,.03)}
.idx{font-style:normal;font-size:13px;font-weight:800;color:#4f46e5;flex:0 0 30px;
  height:30px;line-height:30px;text-align:center;border-radius:9px;background:#eef1ff}
.ptb{flex:1}
.pt b{font-size:16px;display:block;margin-bottom:5px;color:#0f172a}
.pt p{color:#4b5563;font-size:14px;line-height:1.78}
.pain li{margin:8px 0 8px 20px;color:#b91c1c;line-height:1.7}
/* ---- 细节图网格 ---- */
.crops{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.crop{background:#fbfcfe;border:1px solid #eef2f7;border-radius:12px;padding:10px}
.crop p{font-size:13px;color:#4b5563;margin-top:9px;line-height:1.65}
.anno{display:inline-block;background:linear-gradient(135deg,#0ea5e9,#4f46e5);color:#fff;
  border-radius:7px;padding:1px 8px;font-size:11.5px;margin-right:6px;font-weight:600}
/* ---- 参数表 ---- */
table.spec{width:100%%;font-size:14px;border-collapse:separate;border-spacing:0;
  border:1px solid #eef2f7;border-radius:12px;overflow:hidden}
table.spec th{width:34%%;text-align:left;padding:12px 14px;background:#f7f9fc;color:#6b7280;
  font-weight:500;border-bottom:1px solid #eef2f7}
table.spec td{padding:12px 14px;border-bottom:1px solid #eef2f7;color:#111827}
table.spec tr:last-child th,table.spec tr:last-child td{border-bottom:none}
/* ---- 服务 / 备注 ---- */
.service{background:#f6fdf9}
.service li{margin:7px 0 7px 20px;color:#15803d;line-height:1.7}
.proof{color:#4f46e5 !important;font-size:12.5px !important;margin-top:6px}
.tag{display:inline-block;background:#fef9c3;color:#854d0e;border-radius:8px;
 padding:4px 11px;font-size:12px;margin-bottom:10px}
</style></head><body><div class="wrap">%(b)s</div></body></html>""" % {
        "t": _esc(p.get("title", {}).get("title") or p.get("product", "详情页")),
        "W": W, "b": "".join(blocks)}
    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(html)
    return html


# --------------------------------------------------------------------------
# 落点（与 workspace 的统一工作根对齐；**不新增分类**，避免破坏 CATS 契约）
# --------------------------------------------------------------------------
def out_dir_for(cfg: dict, title: str, now=None) -> str:
    """电商产出目录：`<工作根>/图片/电商/<标题>_<时间>`。

    为什么不新增一个 workspace 分类：`pasm.cognitive.workspace.CATS` 是**被断言钉死的
    7 类**（`selftest` 里有 `CATS == (...)`），加一类会连带改核心侧与守卫。
    电商物料本质就是"图片资产"，归 `图片/` 下再加一层 `电商/` 子目录，
    既好找（目录名自带"电商"）又零契约风险。
    """
    try:
        import workspace as WS
        base = WS.cat_dir("image", cfg)
    except Exception:                           # noqa: BLE001
        base = os.path.join(os.path.expanduser("~"), "PASM工作", "图片")
    slug = re.sub(r"[\\/:*?\"<>|\s]+", "_", str(title or "电商")).strip("._")[:24] or "电商"
    stamp = time.strftime("%Y%m%d_%H%M%S", now or time.localtime())
    return os.path.join(base, "电商", "%s_%s" % (slug, stamp))


def html_path_for(out_dir: str) -> str:
    return os.path.join(out_dir, "详情页.html")


def md_path_for(out_dir: str) -> str:
    return os.path.join(out_dir, "电商方案.md")


# --------------------------------------------------------------------------
# 自检（正例 + **反例**；全部离线、不碰用户目录）
# --------------------------------------------------------------------------
def selftest() -> int:
    passed = failed = 0

    def check(cond, name, detail=""):
        nonlocal passed, failed
        # 参数顺序自检：写成 check("名字", 条件) 会因字符串恒真而**静默假绿**
        if isinstance(cond, str):
            raise AssertionError("check() 参数顺序写反：应为 check(条件, 名称, 详情)，收到 %r" % cond)
        if cond:
            passed += 1
            print("  ok   %s" % name)
        else:
            failed += 1
            print("  FAIL %s  %s" % (name, detail))

    print("== ecom 自检 ==")

    # ---------- 平台规格 ----------
    tb, jd = platform_spec("淘宝"), platform_spec("京东")
    check(tb["main_size"] == (800, 800), "淘宝主图 800×800", tb["main_size"])
    check(tb["detail_width"] == 750, "淘宝详情版心 750", tb["detail_width"])
    check(jd["detail_width"] == 790 and jd["main_min"] == 800, "京东详情 790 / 主图 800", jd)
    check(platform_spec("不认识的平台")["label"] == tb["label"], "未知平台回落淘宝")
    check(norm_platform("天猫旗舰店") == "taobao" and norm_platform("JD自营") == "jd",
          "平台别名识别（天猫→淘宝 / JD→京东）")
    check(norm_platform("") == DEFAULT_PLATFORM, "空平台名回落默认")

    # ---------- 标题字符口径（中文按 2 字符） ----------
    check(title_len("abc") == 3 and title_len("中") == 2 and title_len("a你") == 3,
          "标题长度口径：1 汉字 = 2 字符", title_len("a你"))
    long_title = "白色纯棉圆领短袖T恤男装夏季透气吸汗简约百搭打底衫正品包邮"
    r = optimize_title("白色纯棉圆领短袖T恤", ["男装", "夏季", "透气", "吸汗", "百搭"],
                       "服装", "示范品牌", "taobao")
    check(r["len"] >= title_len("白色纯棉圆领短袖T恤 示范品牌"), "标题长度被真实计算", r["len"])
    check(r["limit"] == 60, "淘宝标题上限 60 字符", r["limit"])
    over = optimize_title("白色纯棉T恤", ["男装", "夏季", "透气", "吸汗", "百搭",
                                          "打底衫", "简约", "修身", "大码", "潮"],
                          "服装", "示范品牌")
    check(over["ok"] and title_len(over["title"]) <= 60,
          "★ 10 个关键词时标题**默认就做进平台上限内**（自动下沉）",
          "%d/%d %s" % (over["len"], over["limit"], over["title"]))
    check(len(over.get("dropped") or []) >= 1,
          "★ 下沉的词如实记在 dropped（不静默丢词）", over.get("dropped"))
    check(not any(d in over["title"] for d in over.get("dropped") or []),
          "★ 下沉的词**确实不在**标题里（不是记了个幽灵）",
          (over["title"], over["dropped"]))
    check(all(d in over["title"] or True for d in ["示范品牌"]) and
          over["title"].startswith("示范品牌"),
          "品牌在标题首位")
    check(over["title"].split(" ")[0] == "示范品牌",
          "★ 核心词仍在最前（下沉只动尾部弱词）", over["title"][:20])
    # 反例：产品名本身过长 → 装不下就如实报 ok=False（不许假装合规）
    huge = optimize_title("超长产品名" * 14, ["男装"], "服装", "示范品牌")
    check((not huge["ok"]) and title_len(huge["trimmed"]) <= 60,
          "★ 反例：核心词自身超限时如实报 ok=False 并给截断版",
          "%d -> %d" % (huge["len"], title_len(huge["trimmed"])))
    check(hanzi_count("a你b好") == 2, "汉字计数", hanzi_count("a你b好"))

    # ---------- 类目识别 ----------
    check(detect_category("猫粮 冻干 全价无谷") == "宠物", "类目：宠物",
          detect_category("猫粮 冻干 全价无谷"))
    check(detect_category("纯棉白色T恤 男装") == "服装", "类目：服装")
    check(detect_category("蓝牙耳机 降噪") == "数码", "类目：数码")
    check(detect_category("随便什么东西") == "通用", "认不出 → 通用")

    # ---------- 违禁词：正例 ----------
    v = check_forbidden("这是全网最好的产品，国家级品质，唯一选择")
    words = [x["word"] for x in v]
    check({"最好", "国家级", "唯一"} <= set(words),
          "违禁词命中：最好 / 全国第一 / 国家级 / 唯一", words)
    check(all(x.get("group") and x.get("hint") for x in v),
          "违禁词带分组与改稿建议", v)
    check(len(words) == len(set(words)), "同词只报一次", words)
    # ---------- 违禁词：**反例**（不许误伤正常表达） ----------
    neg = [
        "最近上新了一批新款",
        "最后一批现货，售完即止",
        "最多可装 20L，容量很能打",
        "最快次日达，物流很快",
        "优质面料，手感很好",
    ]
    bad = [(t, check_forbidden(t)) for t in neg]
    check(all(not h for _t, h in bad),
          "★ 反例：正常表达不许被判违禁（最近/最后/最多/最快/优质）",
          [x for x in bad if x[1]])

    # ---------- 卖点：离线模板也要有 5 条且不编造 ----------
    pts = build_selling_points("加厚保暖，防水面料，易清洗，食品级材质",
                               "一件深灰色外套", "服装")
    check(len(pts) == 5, "离线卖点 5 条", len(pts))
    check(all(p["source"] == "offline" for p in pts),
          "离线卖点标了 source=offline", pts)
    check(any("加厚" in p["body"] or p["head"] == "加厚" for p in pts),
          "卖点优先用命中词表的原句（加厚/防水）", pts)

    # ---------- 裁切计划：相对坐标 ----------
    crops = build_crops("服装", 3)
    check(len(crops) == 3 and all(c.get("box") for c in crops),
          "裁切计划 3 条且带兜底 box")
    check(all(0.0 <= float(x) <= 1.0 for c in crops for x in c["box"]),
          "裁切坐标是**相对值** 0~1（不是绝对像素）", [c["box"] for c in crops])
    check(all(c["box"][0] + c["box"][2] <= 1.0001 and
              c["box"][1] + c["box"][3] <= 1.0001 for c in crops),
          "裁切 box 不出界（x+w<=1 且 y+h<=1）", [c["box"] for c in crops])
    check([c["anno"] for c in crops] == ["①", "②", "③"],
          "局部/细节图带编号标注 ①②③", [c["anno"] for c in crops])

    # ---------- 主图分工 ----------
    mains = build_main_images("白色T恤", "服装", pts, crops, "taobao", 5)
    check(len(mains) == 5 and mains[0]["layout"] == "plain"
          and mains[0]["white_bg"] is True,
          "主图 5 张，第 1 张是白底", mains[0])
    check(all(m["size"] == "800x800" for m in mains),
          "主图尺寸 = 平台规格", [m["size"] for m in mains])
    check(len({m["role"] for m in mains}) == 5,
          "主图角色分工不重复", [m["role"] for m in mains])

    # ---------- 完整方案 ----------
    plan = build_plan("加厚保暖外套，防水面料，易清洗，品牌：示范，价格：199",
                      vision="一件深灰色连帽外套，正面有口袋",
                      photos=[r"D:\不存在\实拍.jpg"], platform="jd")
    check(plan["platform"] == "jd" and plan["platform_label"] == "京东",
          "方案平台被正确解析", plan["platform"])
    check(plan["category"] == "服装", "方案含类目", plan["category"])
    check(all(k in plan for k in ("title", "keywords", "selling_points", "crops",
                                  "spec_rows", "main_images", "detail_sections",
                                  "copy", "violations")),
          "方案含标题/关键词/卖点/裁切/参数/主图/详情/文案/违禁词 九大块",
          sorted(plan.keys()))
    check(set(plan["sources"]) == {"title", "keywords", "selling_points", "copy"},
          "方案如实记录来源（sources）", plan["sources"])
    # ---------- v0.31.28 回归：真机那句话不许再抽错 ----------
    _REAL = ("上述的图中项链，我需要你帮我出五张主图、包括副图和细节图，"
             "还有帮我出10张详情图，图中包括文案")
    _rp = build_plan(_REAL)
    check(_rp["product"] == "项链",
          "★ 真机原句的产品名必须是「项链」（旧版抽成「还有帮我出10张详情图」）",
          _rp["product"])
    check(_rp["category"] == "饰品",
          "★ 「项链」应判类目「饰品」（旧版落「通用」→ 规格/资质全走错模板）",
          _rp["category"])
    # v0.31.31：表里不再有「待补充」占位 → 断言改成"类目真字段**有值时**才出现"
    _rp2 = build_plan(_REAL + "，材质：S925银，链长：42cm，重量：3.2g")
    check(any("链长" in r[0] for r in _rp2["spec_rows"]),
          "★ 饰品参数表含「链长」等真实字段（有值时）",
          [r[0] for r in _rp2["spec_rows"]])
    check(not _rp["spec_rows"] or all(r[1] not in ("待补充", "待定")
                                     for r in _rp["spec_rows"]),
          "★ 没给参数时不摆空占位行", _rp["spec_rows"])
    check(any("检测证书" in x or "CMA" in x for x in _rp["qualification"]),
          "★ 饰品资质清单给的是检测证书这类真要求", _rp["qualification"])
    _junk = {"上述的图中项", "我需要你帮我", "出五张主图", "包括副图和细",
             "节图", "还有帮我出", "张详情图", "图中包括文案"}
    check(not (_junk & set(_rp["keywords"])),
          "★ 关键词绝不许再是原句机械切片（旧版 8 个词全是碎片）", _rp["keywords"])
    check(len(_rp["keywords"]) >= 4 and "项链" in _rp["keywords"],
          "★ 离线兜底关键词也要有品类词 + 类目词", _rp["keywords"])
    # 认图判据
    check(parse_detail_count(_REAL) == 10,
          "★ 从「还有帮我出10张详情图」里取到 10 张", parse_detail_count(_REAL))
    check(parse_detail_count("详情图 6 张") == 6 and
          parse_detail_count("帮我做套主图") is None and
          parse_detail_count("要999张详情图") is None,
          "★ 数量解析：正例过、没提数量与越界都返回 None",
          (parse_detail_count("详情图 6 张"), parse_detail_count("帮我做套主图"),
           parse_detail_count("要999张详情图")))
    check(refers_prior_image(_REAL) and refers_prior_image("就用我刚才那张图"),
          "★ 认得出「上述的图 / 刚才那张图」这类指代")
    check(not refers_prior_image("帮我做一套防水背包的主图方案"),
          "★ 反例：没提图的句子不许被判成「指代上面那张图」")
    # 修饰词左扩：该带上的要带上、不该带的不许带
    check(_noun_with_modifiers("加厚保暖外套，防水面料", "外套") == "加厚保暖外套",
          "★ 修饰词左扩：加厚保暖外套（带上修饰）",
          _noun_with_modifiers("加厚保暖外套，防水面料", "外套"))
    check(_noun_with_modifiers("上述的图中项链", "项链") == "项链",
          "★ 修饰词左扩：不越过虚词（不许变成「图中项链」）",
          _noun_with_modifiers("上述的图中项链", "项链"))
    # 模型可用时关键词走模型并标 llm
    check(_rp["sources"]["keywords"] in ("llm", "offline"),
          "关键词来源标注合法", _rp["sources"])

    _cloth = build_plan("加厚保暖外套，面料成分：95%棉+5%氨纶，尺码：S/M/L/XL")
    check({"面料成分", "尺码"} <= {r[0] for r in _cloth["spec_rows"]},
          "参数表字段来自类目模板（服装含面料成分/尺码，有值时）",
          [r[0] for r in _cloth["spec_rows"]])
    check(all(r[1] not in ("待补充", "待定") for r in plan["spec_rows"]),
          "★ 参数表里没有任何「待补充/待定」占位（没有的项整行不出现）",
          plan["spec_rows"])
    _blank = build_plan("随便一句没有参数的话", platform="taobao")["spec_rows"]
    check(all(r[1].strip() and r[1] not in ("待补充", "待定") for r in _blank),
          "★ 一句话都没带参数时，表里也不留空占位行", _blank)
    check(any(r[0] == "价格" for r in plan["spec_rows"]), "价格进了参数表")
    check([s["type"] for s in plan["detail_sections"]] ==
          ["banner", "pain", "points", "detail", "benefit", "steps", "scene",
           "spec", "faq", "logistics", "qualification", "service"],
          "详情页 12 屏齐全（含专业级：利益/步骤/FAQ/物流/资质）",
          [s["type"] for s in plan["detail_sections"]])
    # ---------- v0.31.27 专业级增补：每一项都要真产出且不越界 ----------
    check(len(plan["benefits"]) == 5 and all(
        b.get("point") and b.get("benefit") and b.get("proof") for b in plan["benefits"]),
        "★ 卖点→利益→证据 5 条齐全（每条都指向详情页哪看）", plan["benefits"][:1])
    check(5 <= len(plan["faq"]) <= 6 and all(q.get("q") and q.get("a") for q in plan["faq"]),
        "★ FAQ 5~6 条（通用 5 + 类目专属）", len(plan["faq"]))
    check(len(plan["steps"]) == 3, "★ 使用/收货 3 步", plan["steps"]) 
    check(bool(plan["qualification"]) and isinstance(plan["qualification"], list),
        "★ 资质清单非空", plan["qualification"])
    check(any("纤维成分" in x for x in plan["qualification"]),
        "★ 资质**按类目**给（服装应含纤维成分，不是一张通用表糊过去）",
        plan["qualification"])
    check("通用" not in plan["qualification"], "资质清单里不混入「通用」占位")
    check(bool(plan["sku_guide"]) and isinstance(plan["sku_guide"], str),
        "★ SKU/规格建议非空（减少选错规格退货）", plan["sku_guide"][:30])
    _lay = plan["copy"]["seo_layers"]
    check(all(_lay.get(k) for k in ("core", "attr", "long_tail")),
        "★ SEO 分三层（核心/属性/长尾）", _lay)
    _ls = plan["copy"]["live_script"]
    check(all(_ls.get(k) for k in ("open", "pitch", "close", "trouble")),
        "★ 直播话术分时段（开场/讲品/逼单/异议）", sorted(_ls))
    _tv = plan["title_variants"]
    check(len(_tv) == 3 and all(t.get("title") for t in _tv),
        "★ 3 版标题做 A/B", [t["name"] for t in _tv])
    check(all((not t["violations"]) for t in _tv),
        "★ A/B 标题本身先过违禁词体检（不给用户违规选项）",
        [(t["title"], t["violations"]) for t in _tv])
    _bad = [s2["type"] for s2 in plan["detail_sections"]
            if s2["type"] in ("benefit", "faq", "steps", "qualification")
            and not (s2.get("items") or [])]
    check(not _bad, "★ 反例：专业级新屏不许是空的（空屏比没有这屏更糟）", _bad)
    check(plan["detail_sections"][-3].get("draft") is True,
        "★ 物流承诺标了 draft（必须商家按实际核对后再用）")
    check(all(plan["copy"].get(k) for k in ("short", "long", "live", "seo")),
          "详情文案四件套齐全（短/长/口播/seo）")
    check(any("不用 AI 重画" in n for n in plan["notes"]),
          "方案带「不用 AI 重画产品」的诚实说明", plan["notes"])

    # ---------- LLM 注入：能用就用、挂了不许影响出方案 ----------
    def _boom(_ask):
        raise RuntimeError("模型挂了")

    p2 = build_plan("防水背包", llm_fn=_boom)
    check(bool(p2["title"]["title"]) and p2["sources"]["title"] == "offline",
          "★ 模型抛异常时仍能出完整方案（降级不是崩溃）", p2["sources"])

    def _fake(_ask):
        return "示例商品 防水背包 户外 大容量 正品"

    p3 = build_plan("防水背包", llm_fn=_fake)
    check(p3["sources"]["title"] == "llm",
          "模型可用时标题走模型并标 source=llm", p3["sources"])

    # ---------- 交付物：Markdown / HTML ----------
    md = render_plan_md(plan)
    check(all(k in md for k in ("# 🛒", "主图 5 张分工", "局部图 / 细节图裁切计划",
                                "详情文案", "违禁词体检")),
          "方案 md 含标题/主图/裁切/文案/违禁词章节", md[:200])
    h = render_detail_html(plan, images={}, out_path="")
    check("width:790px" in h,
          "详情页 HTML 版心宽度 = 平台 detail_width（京东 790）")
    check("待生成" in h and "img src='data:" not in h,
          "无图时 HTML 如实写「待生成」而不是画假图")
    h2 = render_detail_html(plan, images={"main": r"D:\a.png",
                                          "crops": [r"D:\c1.png", r"D:\c2.png",
                                                    r"D:\c3.png"]})
    check("file:///" in h2, "有图时引用真实路径（file URI）", h2[:200])
    # ---------- ★ v0.31.31 详情页视觉与"没有就不要" ----------
    check("class='hero'" in h2 and "class='cap'" in h2,
          "★ 详情页改版：主视觉区（hero）在", h2[:160])
    check(".pt{" in h2 and "idx" in h2.replace(".idx", ""),
          "★ 卖点改成带编号徽章的卡片")
    check("待补充" not in h2,
          "★ 详情页里绝不出现「待补充」字样")
    _hp = render_detail_html(plan, images={"main": r"D:.png"})
    _spec_sec = [x for x in (plan.get("detail_sections") or [])
                 if x.get("type") == "spec"]
    check(bool(_spec_sec), "方案里仍有 spec 屏定义（模板保留）")
    if _spec_sec:
        _empty_plan = dict(plan)
        _empty_plan["detail_sections"] = [
            dict(x, rows=[]) if x.get("type") == "spec" else x
            for x in _spec_sec and (plan.get("detail_sections") or [])]
        _h3e = render_detail_html(_empty_plan, images={})
        check("<table class='spec'>" not in _h3e,
              "★ 参数一项都没有时 → 整块不渲染（没有就不要）")
    check("pain" in h2, "详情页正例：详情结构（痛点/卖点/参数表）都在 HTML 里",
          h2[:300])

    # ---------- 落点 ----------
    d = out_dir_for({}, "白色T恤")
    check(("电商" in d and "白色T恤" in d),
          "产出目录含「图片/电商」且目录名带标题", d)
    check(bool(re.search(r"\d{8}_\d{6}$", d)),
          "产出目录带时间戳（同标题不互相覆盖）", d)

    # ---------- 健壮性 ----------
    _pu = new_profile("服装", "测试外套")
    profile_apply_text(_pu, "面料：涤纶，尺码：L，颜色：黑，其他不知道", "服装")
    check(profile_missing(_pu, "服装") == [],
          "★ 用户答完「其他不知道」→ 无缺项不再追问（未知≠缺失，"
          "旧实现会把未知问回来）")
    check(bool(build_plan("")["title"]["title"]), "空输入不炸且仍出方案")
    check(bool(build_plan("防水" * 3000)["product"]), "超长输入不炸")
    check(bool(build_plan("😀🎉" * 40)["product"]), "乱码/表情不炸")
    check(build_plan("x", photos=None)["photos"] == [], "photos 传 None 不炸")

    # ---------- v0.31.30 产品档案：先录信息，再生成 ----------
    # 真机事故：没说一句话就生成 → 参数表 7 行全「待补充」、5 条卖点同一句模板话。
    prof = new_profile("饰品", "示范项链")
    _miss = profile_missing(prof, "饰品")
    check(_miss and _miss[0]["label"] == "材质",
          "空档案先问必填项（材质第一）", [f["label"] for f in _miss])
    prof, filled = profile_apply_text(prof, "材质：S925银，链长42cm", "饰品")
    check(filled == ["材质", "链长"] and prof["values"]["material"] == "S925银",
          "「材质：S925银」「链长42cm」两种写法都能回填", filled)
    check(len(profile_missing(prof, "饰品")) >= 2,
          "录了两项后仍缺项会继续问（不擅自开工）")
    prof, _f2 = profile_apply_text(prof, "重量3.2g，其他不知道", "饰品")
    check(prof["values"].get("pendant_size") == "未知",
          "说「不知道」记未知，绝不追第二轮", prof["values"])
    check("未知" not in str(profile_known(prof)),
          "★ profile_known 只还真实值（未知不喂给文案）", profile_known(prof))
    _p_plan = build_plan("S925银项链女锁骨链", platform="taobao", profile=prof)
    _rows = {r[0]: r[1] for r in _p_plan["spec_rows"]}
    check(_rows.get("材质") == "S925银" and _rows.get("重量") == "3.2g",
          "★ 档案值真的填进参数表（不再是待补充）", _rows)
    _mat = [p for p in _p_plan["selling_points"] if p["head"] == "材质"]
    check(_mat and "S925银" in _mat[0]["body"] and "针对性优化" not in _mat[0]["body"],
          "★ 材质卖点从模板话变成具体句", _mat[0]["body"] if _mat else None)
    _len = [p for p in _p_plan["selling_points"] if p["head"] == "长度"]
    check(_len and "42cm" in _len[0]["body"],
          "★ 别名匹配：类目维度「长度」吃到档案「链长」的值", _len[0]["body"] if _len else None)
    _no = build_plan("S925银项链女锁骨链", platform="taobao")
    check(all("针对性优化" in p["body"]
              for p in _no["selling_points"] if p["source"] == "offline"),
          "★ 反例：**没录档案就不许编**，卖点保持模板话（诚实）")
    check("S925" not in str(_no.get("spec_rows")),
          "★ 反例：没录档案时参数表不许出现真实值")

    # ---------- v0.31.30 商品空间/目录：多产品本地可查 ----------
    import tempfile as _tf
    _tmp = _tf.mkdtemp(prefix="ecom_selftest_")
    _cfg = {"ws_dir": _tmp}                    # ★ 隔离：绝不碰用户真实目录
    _root = EC_root = ecom_root(_cfg)
    check(_root.startswith(_tmp), "目录根走隔离工作区（自检不落用户桌面）", _root)
    for _n in ("产品甲", "产品乙"):
        os.makedirs(os.path.join(_root, _n), exist_ok=True)
    catalog_upsert(_cfg, {"name": "甲", "dir": os.path.join(_root, "产品甲"),
                          "title": "甲-初版", "specs": {"材质": "S925"}})
    catalog_upsert(_cfg, {"name": "乙", "dir": os.path.join(_root, "产品乙"),
                          "title": "乙", "specs": {}})
    catalog_upsert(_cfg, {"name": "甲", "dir": os.path.join(_root, "产品甲"),
                          "title": "甲-改版", "specs": {"材质": "S925", "重量": "3g"}})
    _items = catalog_load(_cfg)
    check(len(_items) == 2, "同目录重复生成只算一个产品（按 dir 去重）", len(_items))
    check(_items[0]["title"] == "甲-改版",
          "★ 重新生成后条目被更新且置顶（不是留一条旧的）", _items[0]["title"])
    check(os.path.isfile(catalog_path(_cfg)) and os.path.isfile(catalog_html_path(_cfg)),
          "目录落盘 json + html 两个文件")
    _html = open(catalog_html_path(_cfg), encoding="utf-8").read()
    check(_html.count('class="card"') == 2 and "width:100%" in _html,
          "★ 目录 html 每产品一张卡片（CSS 百分号不再炸格式化）")
    check(len(render_catalog_html([])) > 100, "空目录也能渲染（不崩）")
    check(catalog_load({"ws_dir": os.path.join(_tmp, "不存在")}) == [],
          "目录文件不存在时返回空列表（不炸）")

    print("-" * 46)
    print("ecom 自检：%d 项，%s" % (passed + failed,
                                  "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    if "--demo" in sys.argv:
        _p = build_plan("加厚保暖外套，防水面料，易清洗，品牌：示范，价格：199",
                        vision="一件深灰色连帽外套", platform="taobao")
        print(render_plan_md(_p))
        raise SystemExit(0)
    print("ecom.py —— 电商视觉套件（%d 个平台 / %d 个类目 / %d 组违禁词表）"
          % (len(PLATFORMS), len(CATEGORIES), len(FORBIDDEN_GROUPS)))
    print("用法：python ecom.py --selftest | --demo")
    selftest()
