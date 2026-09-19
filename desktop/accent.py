# -*- coding: utf-8 -*-
"""accent —— 语言天赋：跟用户学说话（口音 / 方言 / 语调）（v0.27.3）

用户要的：不同用户说话味道不一样，聊久了它会学会——
用户一股东北味，聊着聊着它也用东北话回你。

怎么做到（不靠微调，靠"可观测 + 可撤回"的轻量机制）：
  1. 观察：每收到一条用户消息，用方言特征词表给它打一次分，计数落盘。
  2. 沉淀：同一方言累计到阈值（默认 6 次）且占比领先 → 认定"这位用户这么说话"。
  3. 应用：把该方言的**说法**（词表 + 句式 + 示范）注入 system 提示词；
     同时把 TTS 换成对应口音的语音（东北→辽宁话音，粤语→粤语音…）。
  4. 退出：特征消失了，计数会自然衰减，不会永久定型。

所有判定都是**计数**不是猜测，用户说「别学我说话」可一键清零（clear()）。
"""
from __future__ import annotations

import json
import os
import re
import time

# ---------------------------------------------------------------- 数据落点
def _data_dir() -> str:
    if os.environ.get("PASM_STUDIO_DIR"):
        return os.environ["PASM_STUDIO_DIR"]
    if getattr(__import__("sys"), "frozen", False):
        return os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                            "PASMStudio")
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        ".pasmstudio_dev")


def _path() -> str:
    d = _data_dir()
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.join(d, "style_profile.json")


# ---------------------------------------------------------------- 方言词库
# markers：一眼能认出的特征词/句式（出现即强信号）
# soft：弱信号词（多见于该方言但不独有，权重减半）
# voice：edge-tts 对应口音（没有官方方言音的留 ""，只改文字风格）
DIALECTS = {
    "东北": {
        "markers": ["咋地", "咋整", "干哈", "嘎哈", "老铁", "铁子", "整挺好",
                    "搁哪", "搁这", "啥玩意儿", "得劲", "不得劲", "唠嗑", "忽悠",
                    "埋汰", "稀罕", "老鼻子", "皮实", "麻溜", "贼拉", "得瑟",
                    "费劲巴力", "五脊六兽", "秃噜", "毛愣", "那必须的", "可不咋地"],
        # v0.28.3 串味修复：旧 soft 是「整/贼/老/啥/咋/呗」这类**普通话高频单字**，
        # 用户说普通话每句都给东北 +0.5 偷偷攒分，攒过阈值就把整条风格/口音带歪成东北
        # （真机症状："先说粤语，聊着聊着带东北味"）。soft 一律改为 ≥2 字且普通话语境罕见的词。
        "soft": ["嗯呐", "哎呀妈"],
        "voice": "zh-CN-liaoning-XiaobeiNeural",
        "tone": "东北味儿：爽快、热乎、爱用「整/贼/咋/啥」这类词，句子短、爱带「呗」「啊」。",
        "sample": ["这事儿整挺好，麻溜儿就办完了。",
                   "咋地，还不信我啊？那必须的。"],
    },
    "四川": {
        "markers": ["啥子", "咋个", "巴适", "要得", "莫得", "安逸", "瓜娃子",
                    "摆龙门阵", "雄起", "勒是", "晓得", "啷个", "撇脱", "归一"],
        # 「咯/哈/嘛」普通话常见，删（v0.28.3 串味修复）
        "soft": ["噻", "撒"],
        "voice": "",   # v0.29.1：zh-CN-sichuan-YunxiNeural 已被微软下架，留空 = 用标准声线（同"北京"）
        "tone": "川味：慢悠悠的，爱用「啥子/咋个/巴适/要得」，句尾常带「噻」「哈」。",
        "sample": ["这个整法巴适得板，要得。", "莫得事，慢慢来嘛。"],
    },
    "粤语": {
        # 「多谢」普通话也天天说，删（v0.28.3）；粤语谢意由「唔该」守
        "markers": ["唔", "咩", "嘅", "系咪", "点解", "食饭", "唔该",
                    "好犀利", "得唔得", "咁", "边度", "依家", "睇下", "搞掂"],
        # 「啦/咯」普通话高频，删（v0.28.3 串味修复），只留粤语独有的「喎」
        "soft": ["喎"],
        "voice": "zh-HK-HiuGaaiNeural",
        "tone": "粤语味道：「唔/咩/嘅/系咪」，口语里夹书面语，读出来要用粤语。",
        "sample": ["呢个我搞掂咗啦，唔使担心。", "得唔得？唔得我再谂过。"],
    },
    "台湾腔": {
        # 「瞎」普通话常用（瞎说/瞎忙），删（v0.28.3）；「很瞎」组合保留
        "markers": ["蛤", "欸", "酱子", "这样子", "好康", "阿娜答", "机车",
                    "有的没的", "orz", "鲁蛇", "夯", "很瞎", "齁", "是在哈啰"],
        # 「喔/耶/吼」普通话常见，删（v0.28.3 串味修复）
        "soft": ["捏", "了啦"],
        "voice": "zh-TW-HsiaoChenNeural",
        "tone": "台湾腔：句尾上扬爱带「喔/耶/捏/啦」，语气软、爱用叠词。",
        "sample": ["好喔，我帮你用用看捏～", "这个超夯的欸，你要不要试试？"],
    },
    "北京": {
        # 「您」（礼貌用语全普通话通用）/「甭」「回头」「丫」误判高，删（v0.28.3）；
        # 组合词「甭管」保留，京味由 老炮儿/局气/磁器/得嘞/倍儿/哥们儿 守
        "markers": ["溜达", "搓火", "局气", "磁器", "老炮儿",
                    "得嘞", "甭管", "哥们儿", "倍儿"],
        # 「儿/嘿/哎」普通话高频（"女儿/婴儿"都含"儿"），全删（v0.28.3 串味修复）
        "soft": [],
        "voice": "",
        "tone": "京味：客气里带点贫，儿化音多，「您/甭/得嘞/倍儿」。",
        "sample": ["得嘞，这事儿交给我您就甭管了。", "倍儿棒，回头咱再聊。"],
    },
    "河南": {
        # 裸「中」是普通话超高频字（中午/中间全中招），删（v0.28.3）；
        # 「中不中 / 中嘞 / 中！」由词组与正则守
        "markers": ["恁", "得劲", "咋弄", "弄啥嘞", "木有", "中不中",
                    "俺", "恁咋"],
        # 「嘞/咧」口语偶见，删（v0.28.3 串味修复）
        "soft": [],
        "voice": "",   # v0.29.1：zh-CN-henan-YundengNeural 已被微软下架，留空 = 用标准声线（同"北京"）
        "tone": "河南味：「恁/中/弄啥嘞/俺」，朴实直爽。",
        "sample": ["这事儿中！恁就放心吧。", "弄啥嘞，俺都给你办好了。"],
    },
    "山东": {
        "markers": ["俺", "恁", "咋治", "得为", "拉呱", "杠赛来", "滋儿"],
        # 「哈」（哈哈/哈尔滨）/「嘞」普通话高频，删（v0.28.3 串味修复）
        "soft": [],
        "voice": "",   # v0.29.1：zh-CN-shandong-YunxiangNeural 已被微软下架，留空 = 用标准声线（同"北京"）
        "tone": "山东味：实在、厚道，「俺/恁/拉呱」。",
        "sample": ["俺给你弄好了，杠赛来！", "恁放心，这事儿靠谱。"],
    },
    # v0.28.4：云南（西南官话）——接收走 zh-CN 即可，这里负责"识别+用云南味回"。
    # 注意：edge-tts 没有云南神经语音，voice 留空 → TTS 自动回落普通话音，
    # 但回复文字会用云南口语味（给是/板扎/挨呢），朗读听感仍是普通话。
    "云南": {
        "markers": ["给是", "板扎", "挨呢", "咋们", "杂个", "莫挨", "老挨", "太板扎"],
        "soft": [],
        "voice": "",
        "tone": "云南味（西南官话）：平缓软糯，「给是」=是不是、「板扎」=好、"
                "「挨呢」=在呢、「咋个/杂个」=怎么。",
        "sample": ["给是这们整？", "太板扎了，挨呢慢慢来嘛。"],
    },
}

# ---------------------------------------------------------------- 口音朗读特征词（回复文本里出现即切到对应 TTS 口音）
# 用于 voice_for_text()：不再只认粤语/台湾，四川/河南/东北/山东/北京等回复都会用对应口音读。
# 【v0.28.3 串味修复】只收"该方言独有、普通话语境极少出现"的强词——
# 旧表里的「晓得/安逸/俺/恁/得劲/木有/甭/得嘞/伙计/噻/咯」普通话也用（或跨方言共有），
# LLM 回复偶尔蹦一个就把口音整条切走，真机表现"聊着聊着带东北味/河南味"。
_VOICE_SCAN = {
    "粤语": ["唔", "咩", "嘅", "系咪", "点解", "咁", "边度", "依家", "睇下", "搞掂",
             "食饭", "唔该", "得唔得", "喎", "乜", "畀", "唔使", "嘢", "咗",
             "哋", "啲", "㗎", "冇"],
    "台湾腔": ["酱子", "这样子", "喔捏", "了啦", "是在哈啰", "鲁蛇", "阿娜答",
               "齁", "好康"],
    "四川": ["啥子", "咋个", "巴适", "要得", "莫得", "瓜娃子", "摆龙门阵",
             "雄起", "勒是", "啷个", "撇脱", "归一"],
    "河南": ["弄啥嘞", "中不中", "中嘞", "恁咋"],
    "东北": ["咋地", "咋整", "干哈", "嘎哈", "老铁", "铁子", "整挺好", "搁哪",
             "啥玩意儿", "唠嗑", "忽悠", "麻溜", "贼拉", "那必须的", "五脊六兽",
             "嗯呐", "哎呀妈"],
    "山东": ["拉呱", "杠赛来", "滋儿", "咋治", "得为"],
    "北京": ["老炮儿", "局气", "磁器", "倍儿", "搓火", "溜达"],
    "云南": ["给是", "板扎", "挨呢", "咋们", "杂个"],
}

# 各口音的男声替代（voice_for_text 在 gender=male 时换算）。
# 只列确认存在的两个；其余方言口音在 edge-tts 里多为单一性别声线，
# 用 .get(v, v) 原样返回（绝不编造不存在的语音名，否则会合成失败回落）。
_MALE_VOICE = {
    "zh-HK-HiuGaaiNeural": "zh-HK-WanLungNeural",
    "zh-TW-HsiaoChenNeural": "zh-TW-YunJheNeural",
}


# ---------------------------------------------------------------- 方言规则（高精度补充）
# 【为什么需要它 v0.28.1】
# 上面 markers 只认"标准方言书写"，可用户真机打字用的是**同音字**：
#   粤语「依家」打成「宜家」、「嘅」打成「既」、「唔」打成「吾」、「係」打成「系」。
# 结果：真机实测 detect('你食咗饭未啊？') 都返回空 → 粤语指令不注入、
# 朗读不切粤语音、style_profile.json 一辈子不生成，"用你的方言跟你说话"整条链失效。
#
# 这里用**组合式正则**补齐同音写法。设计原则：
#   · 单字歧义大的（系/既/家/边）必须成词或带限定上下文才算命中；
#   · 只收"普通话里基本不出现"的字（唔/咗/嘅/嘢/冇/乜/咁…）才允许单字命中。
# 文件末尾的 `_selftest()` 用普通话负样本守着这条线（零误判才允许发布）。
_RULE_SRC = {
    "粤语": [
        # ① 单字强信号：这些字普通话里基本不出现
        (r"[唔咗嘅嘢喺哋啲冇乜咩噉㗎咁]", 2.0),
        (r"[睇佢]", 1.5),
        # ② 粤语常用词（「多谢」普通话通用，v0.28.3 移除）
        (r"唔该|唔該|搞掂|点解|點解|边度|邊度|得唔得|犀利|"
         r"食咗|走咗|做咗|嚟到|返工|放工|得闲|得閒|唔使|咁样|噉样|"
         r"边个|邊個|细佬|細佬|老豆|老母|返屋企", 2.0),
        # ③ 同音替代写法（关键：用户就是这么打的）
        (r"宜家(?=[，,。！？?!、\s]|几|幾|係|系|去|做|食|返|要|想|冇|唔|"
         r"点|點|時|时|先|就|我|你|佢|既|嘅|有|无|無)", 2.0),
        (r"吾(?=[系係使好知要可以得会會识識明想])", 2.0),
        (r"(?:既|嘅)\s*(?:嘢|野|事|人|話|话|时候|時候|时间|時間|問題|问题|"
         r"心机|心機|样|樣)", 2.0),
        (r"[我你佢](?:系|係)\s*(?:唔|吾|不|咪)", 2.0),
        (r"(?:系|係)\s*(?:唔|吾)\s*(?:系|係)", 2.0),
        (r"(?:呢|嗰)(?:个|個|度|啲|d)", 1.5),
        (r"好(?=正|抵|靓|靚)", 1.0),
    ],
    "台湾腔": [
        (r"酱子|醬子|这样子|這樣子|好康|阿娜答|机车|機車|鲁蛇|魯蛇|"
         r"是在哈啰|是在哈囉|阿北|北车|北車", 2.0),
        (r"[蛤欸齁]", 1.5),
        (r"(?:了啦|捏|喔|耶)(?=[，,。！？?!\s]|$)", 1.0),
    ],
    # v0.28.2：补齐四川/河南/东北/山东/北京的高精度检测（同音/口语写法），
    # 与粤语/台湾腔一视同仁——否则这些方言只能靠标准词表硬匹配，口语一变就漏检。
    "四川": [
        (r"啥子|咋个|巴适|要得|莫得|摆龙门阵|雄起|瓜娃子|撇脱|归一|勒是|啷个", 2.0),
        (r"晓得|安逸", 1.2),
        # 句尾语气词（噻/撒/咯）：普通话极少这样收尾
        (r"(?:噻|撒|咯)(?=[，,。！？?!\s]|$)", 1.0),
    ],
    "河南": [
        # 单字「中」太常见，必须成词（中不中/中嘞/中！）才命中
        (r"恁|弄啥嘞|中不中|木有|中[！!啊嘞]|得劲|咋弄|俺", 2.0),
    ],
    "东北": [
        (r"咋地|咋整|干哈|嘎哈|老铁|铁子|整挺好|搁哪|啥玩意儿|唠嗑|忽悠|"
         r"麻溜|贼拉|那必须的|五脊六兽", 2.0),
        (r"得劲", 1.2),
    ],
    "山东": [
        (r"拉呱|杠赛来|滋儿|咋治|得为|伙计", 2.0),
        # 俺/恁 为多地方言共有，单独出现只给弱信号
        (r"俺|恁", 0.8),
    ],
    "北京": [
        # 避开「您/甭」（普通话礼貌用语，误判高）；只收地道京片子词
        (r"老炮儿|局气|磁器|得嘞|倍儿|搓火|溜达", 2.0),
    ],
    # v0.28.4：云南（西南官话，与四川接壤但用词不同，需专属强词避免和四川串）
    "云南": [
        (r"给是|板扎|挨呢|咋们|杂个|莫挨|老挨|太板扎", 2.0),
    ],
}

_RULES = {}
for _dname, _src in _RULE_SRC.items():
    _RULES[_dname] = [(re.compile(p), w) for p, w in _src]


def _score(text: str, name: str, cfg: dict) -> float:
    """给一句文本打该方言的分：字面词表（markers/soft）+ 组合规则。

    detect() 与 observe() 共用同一把尺子 —— 否则"这一句跟不跟"与
    "攒够次数没有"会得出不一致的结论（v0.28.1 前就是这样）。
    """
    t = text or ""
    s = 0.0
    for w in cfg.get("markers", []):
        if w and w in t:
            s += W_MARKER
    for w in cfg.get("soft", []):
        if w and w in t:
            s += W_SOFT
    for pat, w in _RULES.get(name, ()):
        if pat.search(t):
            s += w
    return s


def _selftest() -> bool:
    """普通话负样本守卫：任何一句被误判成方言就返回 False。"""
    _neg = [
        "今天天气不错，我们出去走走吧",
        "系统更新完成了吗，帮我看看日志",
        "既然是这样，那就按这个方案来吧",
        "我在家里，你把文件发给我",
        "这个功能好贵啊，能不能便宜点",
        "他是我的关系户，请联系一下他",
        "时间不早了，我们先吃饭吧",
        "隔壁那个项目进度怎么样了",
        "边缘度假村的环境很好",
        "水果个数统计出来了没有",
        "这个系统的时间设置在哪里改",
        # v0.28.3 串味修复回归：这些句子带旧 soft 弱字（咋/啥/啦/儿/嘿/嘛），
        # 修复前会被误攒东北/四川/北京分——修复后必须 0 分
        "你说咋办就咋办吧",
        "你干啥去这么晚才回来",
        "这个好贵啊，能不能便宜一点嘛",
        "好啦好啦，我知道啦",
        "女儿今天在学校得了奖",
        "嘿，你猜怎么着",
    ]
    for s in _neg:
        for nm, cfg in DIALECTS.items():
            if _score(s, nm, cfg) > 0:
                return False
    # 各方言正样本：必须能被各自方言识别到（覆盖新加的四川/河南/东北/山东/北京）
    _pos = {
        "粤语": ["你食咗饭未啊？", "我讲既是宜家既时间是几点，吾系问宜家家私哦",
                 "你好啊，宜家几点啊？", "呢个我搞掂咗啦，唔使担心",
                 "你係唔係唔得闲啊"],
        "四川": ["这个整法巴适得板，要得。", "莫得事，慢慢来嘛。",
                 "咋个回事哦，你啷个才来。", "摆龙门阵摆到雄起！"],
        "河南": ["恁弄啥嘞，俺都给你办好了。", "这事儿中不中？中！",
                 "得劲，弄啥都顺。", "木有恁说的那么邪乎。"],
        "东北": ["老铁，这事儿整挺好啊！", "干哈呢，搁哪疙瘩呢？",
                 "唠嗑唠到麻溜儿。", "那必须的，贼拉好！"],
        "山东": ["俺给你弄好了，杠赛来！", "恁放心，这事儿靠谱。",
                 "咱拉呱拉呱。", "得为，滋儿啊。"],
        "北京": ["您这老炮儿，局气！", "得嘞，倍儿棒，溜达溜达去。",
                 "这磁器，搓火。", "甭管了，咱溜达去。"],
        "云南": ["给是这们整？", "太板扎了，挨呢慢慢来嘛。",
                 "你咋们还不来，杂个回事。", "莫挨我，老挨呢。"],
    }
    for _nm, _samples in _pos.items():
        _cfg = DIALECTS[_nm]
        for s in _samples:
            if _score(s, _nm, _cfg) <= 0:
                return False
    return True


# 累积到多少次才开始"学"（低于这个不应用，避免一两句方言就串味）
MIN_HITS = 6
# 词表命中一次的权重
W_MARKER, W_SOFT = 2.0, 0.5
# 记忆半衰期（秒）：约 45 天，用户改了说话方式会自然淡忘
HALF_LIFE = 45 * 86400.0


def _load() -> dict:
    try:
        d = json.load(open(_path(), encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


_PROFILE_CACHE = {"ts": 0.0, "data": None}


def _load_cached() -> dict:
    """带短 TTL 的画像缓存：每轮对话会多次读 style_profile.json
    （voice_for_text / dominant / prompt_block），缓存避免重复磁盘读。"""
    now = time.time()
    c = _PROFILE_CACHE
    if c["data"] is not None and (now - c["ts"]) < 1.5:
        return c["data"]
    d = _load()
    c["data"] = d
    c["ts"] = now
    return d


def _save(d: dict):
    try:
        with open(_path(), "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        _PROFILE_CACHE["data"] = d
        _PROFILE_CACHE["ts"] = time.time()
    except Exception:
        pass


def profile() -> dict:
    """当前语言画像（方言 → 加权命中数、最近一次命中时间）。"""
    d = _load_cached()
    return d.get("dialects") or {}


def observe(text: str) -> dict:
    """观察一条用户消息，更新方言计数。返回本次命中的方言→分数。"""
    t = (text or "").strip()
    if not t or len(t) > 500:
        return {}
    now = time.time()
    hits = {}
    for name, cfg in DIALECTS.items():
        score = _score(t, name, cfg)
        if score > 0:
            hits[name] = score
    if not hits:
        return {}
    d = _load_cached()
    dl = d.get("dialects") or {}
    for name, s in hits.items():
        rec = dl.get(name) or {"score": 0.0, "last": 0.0, "n": 0}
        # 指数衰减：老印象随时间打折，最近的说法权重最高
        age = max(0.0, now - float(rec.get("last") or now))
        decay = 0.5 ** (age / HALF_LIFE)
        rec["score"] = float(rec.get("score", 0.0)) * decay + s
        rec["last"] = now
        rec["n"] = int(rec.get("n", 0)) + 1
        dl[name] = rec
    d["dialects"] = dl
    d["updated"] = now
    _save(d)
    return hits


def dominant(min_score: float = float(MIN_HITS)) -> tuple:
    """当前主导方言 → (名称, 分数)；没到阈值返回 ("", 0)。"""
    dl = profile()
    if not dl:
        return ("", 0.0)
    now = time.time()
    best, bs = "", 0.0
    for name, rec in dl.items():
        age = max(0.0, now - float(rec.get("last") or 0))
        s = float(rec.get("score", 0.0)) * (0.5 ** (age / HALF_LIFE))
        if s > bs:
            best, bs = name, s
    return (best, bs) if bs >= min_score else ("", bs)


def detect(text: str) -> tuple:
    """单句方言判定（不累积计数），返回 (方言名, 分数)；无特征返回 ('', 0)。

    用于"用户这一句说的方言就立刻跟"——不等画像攒到 MIN_HITS 次，
    用户用粤语打字当下就把回复/朗读切到粤语，无需提醒、无需等 6 轮。"""
    t = (text or "").strip()
    if not t or len(t) > 500:
        return ("", 0.0)
    best, bs = "", 0.0
    for name, cfg in DIALECTS.items():
        s = _score(t, name, cfg)
        if s > bs:
            best, bs = name, s
    return (best, bs)


def prompt_block() -> str:
    """生成注入 system 的语言风格段（没学到就返回空，不打扰）。"""
    name, score = dominant()
    if not name:
        return ""
    cfg = DIALECTS.get(name) or {}
    rec = (profile().get(name) or {})
    n = int(rec.get("n", 0))
    samples = "\n".join("    · " + x for x in (cfg.get("sample") or [])[:2])
    return (
        f"【跟这位用户学来的说话习惯·{name}】"
        f"（最近 {n} 轮对话里听出 {score:.0f} 处特征，自然地用，别演）\n"
        f"- {cfg.get('tone','')}\n"
        f"- 常见说法：{'、'.join((cfg.get('markers') or [])[:8])}\n"
        f"- 示范：\n{samples}\n"
        f"- 分寸：**点到为止**，一句话里最多带一两个方言词，别整段方言；"
        f"说正事、安慰人、给结论时先说清楚，方言只是调味。")


def voice_for(gender: str = "none", fallback: str = "") -> str:
    """按学到的方言挑 TTS 语音；没有对应方言音就用调用方给的兜底。"""
    name, _ = dominant()
    if not name:
        return fallback
    v = (DIALECTS.get(name) or {}).get("voice") or ""
    if not v:
        return fallback
    # 女声优先；明确要男声时，粤语/台湾有男声可换
    if gender == "male":
        alt = {"zh-HK-HiuGaaiNeural": "zh-HK-WanLungNeural",
               "zh-TW-HsiaoChenNeural": "zh-TW-YunJheNeural"}.get(v)
        v = alt or v
    return v or fallback


def _is_traditional(text: str) -> bool:
    """粗判是否为繁体中文（用一批简体里不出现的常用繁体独用字）。"""
    _trad = set("這語說話題對點時們國會個來過嗎產員實開發貓無與該當還總結覺體驗歡這邊們"
                "們來們個這麼說話題對點時們國會過嗎產員實開發貓無與該當還總結覺體驗歡這邊")
    return any(c in _trad for c in (text or ""))


def voice_for_text(text: str, gender: str = "none", fallback: str = "",
                   input_dialect: str = "") -> str:
    """按**要念的文本语言**挑 TTS 语音（而非只按学到的方言画像）。

    这样回复是粤语就用粤语音、繁体/台湾腔用台湾音、英文用英文音，
    解决"回复是粤语、朗读却是普通话"的别扭；识别不出语种时退回 fallback
    （一般是画像里的方言口音，再不行就是默认普通话）。

    v0.27.8+：新增 input_dialect —— 用户**这一句本身**说的方言（不等人攒到 6 次）。
    用户用粤语打字，哪怕回复是标准中文，也直接用粤语音读，立刻切换、无需提醒。"""
    t = (text or "").strip()
    if not t:
        return fallback
    # 1) 英文为主 → 英文音（回复本身是英文时优先，哪怕用户说粤语）
    ascii_n = sum(1 for c in t if ord(c) < 128)
    if ascii_n / max(1, len(t)) >= 0.6 and re.search(r"[A-Za-z]{3,}", t):
        return "en-US-JennyNeural" if gender != "male" else "en-US-GuyNeural"
    # 稳定性修复（v0.28.x）：任何方言音色切换都必须与"已学成的方言画像 dominant()"一致，
    # 否则回复里偶尔冒出的一个方言词（咩/嘅/点解…）会把音色误切走（串味），
    # 造成朗读忽普忽粤、反复横跳。普通话为主的用户（dominant=""）永远留在普通话音。
    _dom, _dom_score = dominant()
    # 1.5) 用户这一句说的方言 → 仅当它正是已学成的主导方言时才切
    if input_dialect and input_dialect == _dom:
        v = (DIALECTS.get(input_dialect) or {}).get("voice") or ""
        if v:
            return _MALE_VOICE.get(v, v) if gender == "male" else v
    # 2) 回复文本里的方言特征词 → 仅当命中数>=2 且等于主导方言才切（单弱词不再能切走）
    _best_dn, _best_n = "", 0
    for _dn, _feats in _VOICE_SCAN.items():
        _v = (DIALECTS.get(_dn) or {}).get("voice") or ""
        if not _v:
            continue
        _n = sum(1 for _w in _feats if _w in t)
        if _n > _best_n:
            _best_dn, _best_n = _dn, _n
    if _best_dn and _best_n >= 2 and _best_dn == _dom:
        _v = (DIALECTS.get(_best_dn) or {}).get("voice") or ""
        return _MALE_VOICE.get(_v, _v) if gender == "male" else _v
    # 3) 台湾腔：只在「已学成台湾方言」或「确为繁体且带多处台湾腔用词」时才切台湾音。
    #    加这道闸，是因为普通话用户回复里偶尔冒出的口头禅（了啦/蛤/欸）或零星繁体字，
    #    会把音色误切到台湾腔——正是「忽普忽台」的串味来源（v0.28.x 稳定性修复）。
    _tw = ("酱子", "这样子", "蛤", "欸", "齁", "喔捏", "了啦", "是在哈啰", "夯",
           "鲁蛇", "机车", "阿娜答", "有的没的")
    _tw_hit = sum(1 for w in _tw if w in t)
    if _dom == "台湾腔" or (_tw_hit >= 3 and _is_traditional(t)):
        return "zh-TW-YunJheNeural" if gender == "male" else "zh-TW-HsiaoChenNeural"
    # 4) 识别不出语种 → 退回画像里的方言口音（若有），否则兜底
    name, _ = dominant()
    if name:
        v = (DIALECTS.get(name) or {}).get("voice") or ""
        if v:
            return _MALE_VOICE.get(v, v) if gender == "male" else v
    return fallback


def status_text() -> str:
    """给用户看的一句状态（我现在是怎么跟你说话的）。"""
    name, score = dominant()
    if not name:
        top = sorted(profile().items(),
                     key=lambda kv: -float(kv[1].get("score", 0)))[:1]
        if top:
            return (f"我还在学你说话呢——目前听出一点「{top[0][0]}」味"
                    f"（{int(top[0][1].get('n',0))} 轮对话 / "
                    f"{float(top[0][1].get('score',0)):.0f} 分，攒到 {MIN_HITS} 分我就跟上）。")
        return "我还没听出你说话的特别味道。多聊几句，我会慢慢学会。"
    n = int((profile().get(name) or {}).get("n", 0))
    return (f"我学会你的「{name}」味了（{n} 轮对话听出来的），会自然地用一点。"
            f"不想让我学就说「别学我说话」。")


def clear():
    """一键清零（用户说「别学我说话」时调用）。"""
    try:
        _save({"dialects": {}, "updated": time.time()})
    except Exception:
        pass
