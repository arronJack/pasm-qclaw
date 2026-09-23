"""PASM Companion —— 对话版"会成长的 AI 伙伴"，v0.3.1（本地为服务器）。

双脑：
- 语言脑：默认本机 Ollama（自动发现已下载模型并挑中文 instruct 模型，完全免 Key），
  也可配 DeepSeek / 任意 OpenAI 兼容端点 / llama-server。
- PASM 认知脑：进程内"内世界"，每次对话转化为情绪/性格/关于你的记忆。

功能（qclaw 式）：持久会话（重启恢复上下文）、新会话、Markdown 渲染、
记忆管理器（查看/删除它记住的关于你的事）、内心侧栏、主动反问、离线微脑兜底。
"""
import html
import io                       # v0.30.15 修：_wp_preview_artifact 曾用未导入的 `_io`
import json
import urllib.parse
import logging
import os
import re
import sys
import tempfile                 # v0.30.15 修：_grab_screen 曾用未导入的 tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qt_compat import QtCore        # v0.30.16：ChatBrowser 要用 QUrl（别再漏导入）
from qt_compat import (QApplication, QCheckBox, QColor, QComboBox, QCursor, QDialog,
                       QFileDialog, QFrame, QHBoxLayout, QIcon, QInputDialog, QLabel,
                       QLayout,
                       QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMenu,
                       QPlainTextEdit,
                       QPainter, QPixmap, QPushButton, QRect, QSize, QSizePolicy,
                       QSlider, QSplitter,
                       QStackedWidget,
                       # v0.31.0：右栏降级渲染器（FitBrowser）要放进自适高的滚动容器；
                       # `QTabBar` 也是新用的（右栏标签改成"只做选择器"的 OutTabBar 基类）。
                       QScrollArea, QTabBar,
                       QTabWidget,
                       QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                       QTextBrowser, QTextEdit, QVBoxLayout, QWidget, Qt,
                       QTimer, Signal, QFont, QTextCursor, ui_dispatch, QPoint,
                       QMessageBox)

try:
    import worklog as WORKLOG          # v0.27.4 工作任务台账（聊天↔工作关联）
except Exception:
    WORKLOG = None

# PASM 引擎：统一经接口层创建（desktop/engine_factory.py → pasm.engine_api）。
# v0.28.6 起桌面不再直连任何具体引擎——"换引擎不改调用代码"在这里生效：
# 新增/替换引擎只需登记进契约注册表，下面 20 多处 self.agent.xxx 一行不用动。
# 完整七层引擎需要 torch；拿不到时由接口层择优降级，并如实报告缺了哪些能力。
import engine_factory as EF
PASM_FULL = EF.full_available()

from appinfo import APP_NAME, APP_VERSION
from updater import check_async
from offline_brain import reply as offline_reply
import tts as tts_mod
import audio as audio_mod
import platform_ops as PLATFORM_OPS   # 跨平台：打开文件 / 在文件管理器中定位（v0.31.1）
import asr as asr_mod
from knowledge import learned_bullets, record as kb_record, summarize as kb_summary, read_book as kb_read
import knowledge
import todo as todo_mod
import agent_tools as AT
import growth as GROWTH
import autostart as AUTOSTART      # v0.29 开机自动启动（HKCU Run 键，免提权）
import permission as PERM          # v0.29 权限三档（安全/标准/完全访问）
import cog as COG
import memory_layers as ML
try:
    import videoeng as VE            # 图生视频双引擎（0.21.0）：可选依赖
except Exception:
    VE = None
import skillstore as SKL
import llm_gateway as GW
import creators as CRE
import prompts as PRT             # v0.22 结构化提示词模板
import validator as VAL           # v0.22 输出验证器
import planner as PLN             # v0.22 任务规划器
import workflow_engine as WORKFLOW   # v0.30.4 AI 自动化工作流引擎
import ad_design as AD              # v0.30.4 广告设计工作版本（文案+主视觉）
import capability as CAP            # v0.30.5 声明式能力注册表（对标白龙马）
try:
    import mcp_bridge as MCPB        # v0.30.11 MCP 客户端桥（#11：接 pasm-mcp-server 认知工具）
except Exception:                   # 模块缺失也不影响启动，桥自带失败缓存
    MCPB = None
try:
    import payment as PAY           # v0.30.12 支付配置页（沙箱默认；密钥只显示掩码）
except Exception:                   # 模块缺失不影响启动，设置页会如实说明不可用
    PAY = None
import autopilot as AUTO            # v0.30.5 自主 Tick 心跳（巡检/续跑/状态）
import context_assembly as ASM      # v0.30.5 上下文装配（统一注入）
import executors as EXEC            # v0.30.5 工作流执行器（媒体/浏览器/知识）
import msg_source as MSG            # v0.30.5 统一消息源（多端预留）
import ui_tech as UT                # v0.30.6 科技感零件（动态波形/卡片）
import pet_tuning as PT             # v0.30.9 小人观感调参（飞机/尾气/飞天高度）
import effect_view as EV           # v0.31.0 右栏效果渲染器（真浏览器 QtWebEngine）
try:
    EXEC.install(WORKFLOW)          # 把媒体/浏览器/知识执行器挂到工作流（幂等）
except Exception:  # noqa: BLE001   装不上只是少几个自动执行器，不影响启动
    pass
try:
    import agent_team as TEAM     # v0.24 多智能体技能团队（方向一 P1-P3）
except Exception:
    TEAM = None
try:
    import cando as _CD           # v0.27.1 能力判定 broker（非预设机制）
except Exception:
    _CD = None
try:
    import selfheal as HEAL       # v0.24 自检·自定位·自修复（方向二 C1-C3）
except Exception:
    HEAL = None
try:
    from pasm.cognitive import mathlab as MLAB   # v0.27 数学脑：离散/线性分析（Phase A）
except Exception:
    try:
        import mathlab as MLAB
    except Exception:
        MLAB = None
try:
    from pasm.cognitive import quantum as QTM    # v0.27 量子策略决策层（Phase C）
except Exception:
    try:
        import quantum as QTM
    except Exception:
        QTM = None
try:
    from pasm.cognitive import percept as PERC   # v0.27 多模态感知：看图+听写（Phase B）
    from pasm.cognitive import ir as IR          # P1：IR 唯一真相源（词表/判定）
except Exception:
    try:
        import percept as PERC
    except Exception:
        PERC = None
try:
    from pasm import sft as SFT        # v0.19 SFT 样本采集（微调语料资产）
except Exception:
    SFT = None

PERSONALITIES = {"温和沉稳": [0.1, 0.7, 0.5], "好奇活泼": [0.8, -0.2, 0.6],
                 "机灵敏锐": [0.4, 0.1, 0.3],
                 "温柔内向": [0.1, 0.8, 0.15], "活泼外向": [0.85, -0.3, 0.9],
                 "调皮灵动": [0.7, -0.15, 0.65], "沉稳可靠": [0.05, 0.65, 0.4],
                 # v0.27.3 新增（用户点名要的"大大咧咧、偶尔冒一句我靠"那挂）
                 "豪爽直率": [0.9, -0.6, 0.9], "毒舌损友": [0.6, -0.4, 0.5]}
# 性格档案元信息：temper 决定行为偏好（intro 内向 / extra 外向 / playful 调皮 / steady 沉稳），
# energy 决定自主活动频率，play 决定开心时"撒欢"的倾向（人格层 P3 用）
ARCH_META = {
    "温和沉稳": {"label": "温和沉稳 · 慢热体贴", "temper": "steady",
                "energy": 0.35, "play": 0.2, "line": "平时安安静静陪着你，被你夸了会轻轻晃两下"},
    "好奇活泼": {"label": "好奇活泼 · 爱闹爱笑", "temper": "extra",
                "energy": 0.75, "play": 0.7, "line": "精力充沛爱蹦跶，开心了会跳舞转圈"},
    "机灵敏锐": {"label": "机灵敏锐 · 反应快", "temper": "playful",
                "energy": 0.6, "play": 0.5, "line": "机灵得很，偶尔会悄悄跟你皮一下"},
    "温柔内向": {"label": "温柔内向 · 安静细腻", "temper": "intro",
                "energy": 0.22, "play": 0.15, "line": "话不多但心细，安静时会自己捧着书看"},
    "活泼外向": {"label": "活泼外向 · 阳光热烈", "temper": "extra",
                "energy": 0.85, "play": 0.85, "line": "自来熟爱热闹，高兴起来恨不得拉着你一起玩"},
    "调皮灵动": {"label": "调皮灵动 · 鬼点子多", "temper": "playful",
                "energy": 0.7, "play": 0.9, "line": "古灵精怪，最爱逗你笑，被你凶了会先嘴硬再委屈"},
    "沉稳可靠": {"label": "沉稳可靠 · 踏实安心", "temper": "steady",
                "energy": 0.3, "play": 0.2, "line": "不慌不忙，答应的事一定会做到"},
    # v0.27.3 新增
    "豪爽直率": {"label": "豪爽直率 · 大大咧咧", "temper": "extra",
                 "energy": 0.8, "play": 0.6,
                 "line": "有啥说啥，高兴了就夸，偶尔冒一句「我靠」，办事雷厉风行"},
    "毒舌损友": {"label": "毒舌损友 · 嘴硬心软", "temper": "playful",
                 "energy": 0.55, "play": 0.75,
                 "line": "嘴上不饶人，手上最靠谱；吐槽归吐槽，事儿一定办漂亮"},
}
# 干活类意图：聊天模式下绝不自动执行，只给"切到干活模式"的开工入口
_HEAVY_KINDS = frozenset({"project", "script", "genppt", "gendoc", "genxls",
                          "weblearn", "skill", "image", "video", "manga",
                          # v0.30.13：广告会真落盘（方案 + 主视觉），与图像同级
                          "ad",
                          # v0.28.x 新增：会改动本地状态或对外发东西的，一律先确认
                          "remind_add", "remind_del", "mail_send",
                          "cal_add", "cal_del", "evolve_save", "team_auto"})
# v0.22 计划确认/改计划的口令（只匹配整句，避免误吞正常聊天）
_RE_PLAN_CONFIRM = re.compile(
    r"^(开始|确认|同意|按计划|就这么办|干吧|执行吧|开工|好的?|行|嗯+|ok|go|好呀|好嘞)"
    r"[吧呗啊呀~！!。.\s]*$", re.I)
_RE_PLAN_REPLAN = re.compile(
    r"^(不(行|要|用|对|改|了)|先不|换个?|改一下|改改|重新?(规划|计划|想)|算了)"
    r"[吧啊呀~！!。.\s]*$")
# v0.22 智能总线：这些话才值得走"工具轮"回忆个人相关内容（其余直通，省一趟生成）
_RE_RECALL_HINT = re.compile(
    r"(记得|上次|之前|前几天|说过|提到过|喜欢|讨厌|偏好|学过|答应|说好|我叫|我的名字|"
    r"咱们|生日|过敏|忌口|多大了|几岁|性格|心情如何|你什么情绪)", re.I)
_KIND_ANCHOR = {
    "project": ("🖥 开发项目", "project"), "script": ("✍️ 写代码", "code"),
    "genppt": ("📽 做 PPT", "ppt"), "gendoc": ("📄 Word 文档", "doc"),
    "genxls": ("📊 Excel 表格", "xls"), "weblearn": ("🌐 上网自学", "web"),
    "skill": ("🎓 用技能开工", "skill"),
    "image": ("🎨 生成图片", "image"), "video": ("🎬 生成短片", "video"),
    "manga": ("📖 生成漫剧", "manga"),
}
_GENDER_LABELS = [("女生", "female"), ("男生", "male"), ("无(中性)", "none")]

_CN_NUM_MAP = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
               "八": 8, "九": 9, "十": 10}


def _cn_num(s: str) -> int:
    """"第2镜/第二镜/第10镜" → int（仅支持 1~99 的常见写法）。"""
    s = (s or "").strip()
    if s.isdigit():
        return int(s)
    if s in _CN_NUM_MAP:
        return _CN_NUM_MAP[s]
    if s.startswith("十"):
        return 10 + (_CN_NUM_MAP.get(s[1:], 0) or 0)
    if s.endswith("十") and len(s) > 1:
        return _CN_NUM_MAP.get(s[0], 0) * 10
    if "十" in s:
        a, _, b = s.partition("十")
        return _CN_NUM_MAP.get(a, 0) * 10 + _CN_NUM_MAP.get(b, 0)
    return 1
SENT_POS = ["开心", "谢谢", "感谢", "喜欢", "棒", "太好了", "真棒", "爱", "不错", "感动",
            "幸福", "高兴", "兴奋", "满意", "靠谱", "好用", "厉害", "牛", "赞", "哈哈",
            "嘿嘿", "好耶", "满意", "顺利", "搞定", "成功", "谢谢你", "辛苦了", "想你了",
            "夸你", "真行", "太强", "了不起", "羡慕"]
SENT_NEG = ["烦", "气", "难过", "失望", "差", "讨厌", "伤心", "愤怒", "崩溃", "焦虑",
            "生气", "郁闷", "沮丧", "累死", "烦死", "气死", "难受", "痛苦", "糟糕", "失败",
            "出错", "报错", "不行", "没用", "太差", "垃圾", "破", "卡", "慢死", "等半天",
            "没反应", "没动静", "坏掉", "闪退", "退出", "罢工", "无语", "头疼", "炸了"]
LOCAL_ENDPOINTS = [("ollama", "http://127.0.0.1:11434/v1"),
                   ("llama-server", "http://127.0.0.1:8080/v1")]
OLLAMA_API = "http://127.0.0.1:11434"


def _data_dir() -> str:
    """数据目录 —— 实现统一放在 logsetup，**单一来源**。

    各写一份的话，日志目录一旦与数据目录漂移，就会出现"日志写在别处、
    排查时找不到"的问题（比没日志更难发现）。
    """
    import logsetup
    return logsetup.data_dir()


DATA_DIR = _data_dir()
os.makedirs(DATA_DIR, exist_ok=True)
CONFIG = os.path.join(DATA_DIR, "config.json")
NOTES = os.path.join(DATA_DIR, "user_notes.json")
HISTORY = os.path.join(DATA_DIR, "chat_last.json")
PREFS = os.path.join(DATA_DIR, "prefs.json")
#: v0.31.3 干活会话状态（各栏目的"上一轮需求"）—— **必须落盘**。
#: 起因（真机）：用户在开发栏目只打「请立即开始」，而"上一次要做什么"只存在内存里
#: （`self.dev`），App 一重启就丢 → 那句话被原样当需求送进模型 → 凭空造一个项目，
#: 用户看到"一个不是我想要的开发"。落盘后跨重启也能续上。
WORK_SESSION = os.path.join(DATA_DIR, "work_session.json")
SESSION_DIR = os.path.join(DATA_DIR, "sessions")
GEN_MEM = os.path.join(DATA_DIR, "genfiles.json")  # v0.26.2 产物记忆：会话 → 最近生成的表格/文档/PPT {path, md}
os.makedirs(SESSION_DIR, exist_ok=True)
SLOTS_FILE = os.path.join(SESSION_DIR, "_slots.json")   # 栏目 → 当前会话文件（v0.17.2 起）
LOG_PATH = os.path.join(DATA_DIR, "pasm.log")
# 走 logsetup：它带 force=True。裸 basicConfig 在 root 已有 handler 时
# **静默失效**（v0.30.0 就是这样丢了整整一个版本的日志）。
try:
    import logsetup
    logsetup.setup(DATA_DIR)
except Exception as _lse:
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s",
                        encoding="utf-8", force=True)
    try:
        with open(os.path.join(os.environ.get("TEMP") or ".", "pasm_logsetup_error.txt"),
                  "a", encoding="utf-8") as _f:
            _f.write("pasm_companion: logsetup 失败：%r\n" % (_lse,))
    except Exception:
        pass


def _excepthook(tp, val, tb):
    logging.critical("UI unhandled", exc_info=(tp, val, tb))


sys.excepthook = _excepthook


def _load_json(path, default):
    try:
        if os.path.exists(path):
            return json.load(open(path, "r", encoding="utf-8"))
    except Exception:
        pass
    return default


def _save_json(path, obj):
    try:
        json.dump(obj, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    except Exception:
        pass


def _extract_target_dir(text: str) -> str:
    """从需求里抽出用户**明确指定且已存在**的目标目录（没有就返回空串）。

    真机场景（2026-09-23 用户会话实录）：用户说
    「帮我先看下我在 D:\\Code副\\springcloud-business 的**Spring Boot** 项目」，
    后来连续说了 6 遍"执行 / 马上执行 / 请马上开发"，最后说「请立即开始」——
    包里**完全无视那个路径**，跑到 `桌面/PASM工作/project/` 下另建了一个
    Python 项目（`app.py` / `backend.py`）。用户的原话是
    「我检查过我的 D:\\Code副\\springcloud-business 了，里面是空的，怎么没有内容」。

    → 用户**在需求里写明的目录**是他的显式指令，优先级高于"在工作根里另建一个"。
      这里只认**真实存在**的目录（不存在的路径不猜，免得又造出一个错地方）。
    """
    if not text:
        return ""
    for m in re.finditer(r"[A-Za-z]:[\\/][^\s，。；;：:）)】」\"'、,]+", str(text)):
        cand = m.group(0).strip(" \t\"'")
        # 路径后面常直接粘中文（"…springcloud-business 的骨架" / "…的骨架"），逐字回退
        while len(cand) > 3 and not os.path.isdir(cand):
            if re.match(r"[\u4e00-\u9fa5。，、；：！？]$", cand[-1]):
                cand = cand[:-1]
            else:
                break
        if os.path.isdir(cand):
            return cand
    return ""


def _pick_project_name(req: str) -> str:
    """从整句需求里抽一个像样的项目名。

    优先级：叫/名为 → 动词+产品短语（做个XX/开发一个XX）→ 去掉口语前缀后截取。
    避免把「那么你能根据这个文档里的内容帮我…」整句前 12 字当项目名。
    """
    name = ""
    # ★ v0.31.3：需求里带了**现成的项目目录**时，优先用那个目录名。
    #   「帮我在 D:\Code副\springcloud-business 里搭 Spring Boot 3.x 骨架」
    #   → springcloud-business（旧版取前 12 字，得到「在DCode副sprin」这种名字）
    m = re.search(r"[A-Za-z]:[\\/][^，。；;：:）)】」\"'\s]*[\\/]([A-Za-z0-9_\-\.]{2,32})", req)
    if not m:
        m = re.search(r"(?:^|[\s（(【\[])([A-Za-z0-9_\-]{2,24}[\\/][A-Za-z0-9_\-\.]{2,32})", req)
    if m:
        cand = m.group(1).strip("\\/. ")
        # 目录名可以比中文项目名长（springcloud-business 就 20 字），别用 16 字上限砍掉它
        if 2 <= len(cand) <= 32:
            return cand
    m = re.search(r"(?:叫|名为|名字[叫是])\s*[「『\"“]?([\w\u4e00-\u9fa5\- ]{1,20}?)(?=的|帮|请|"
                  r"然后|还有|以及|并且|可以|能|，|。|？|!|！|$)", req)
    if m:
        name = m.group(1).strip()
    if not (2 <= len(name) <= 16):
        # 动词 + (一个/个/款) + 产品名（到标点或"的/帮/根据/以及"为止）
        m = re.search(
            r"(?:开发|做一个|做个|做|搭一?个|建一?个|写一?个|帮我做)\s*"
            r"(?:一个|个|一款|一套)?\s*(?:叫|名为)?\s*"
            r"([\w\u4e00-\u9fa5][\w\u4e00-\u9fa5·\- ]{0,14}?)"
            r"(?=的|帮|请|然后|还有|以及|并且|可以|能|根据|，|。|？|!|！|$)", req)
        if m:
            name = m.group(1).strip()
    name = re.sub(r"(吗|吧|呢|啊|呀|么|哦|哈)$", "", name).strip(" -")
    if 2 <= len(name) <= 16:
        return name
    # 去掉口语前缀后截取
    clean = re.sub(r"^(那么|那|你|你能|你可以|请|麻烦|帮我|能不能|可否|可以|就)",
                   "", req.strip())
    clean = re.sub(r"[^\w\u4e00-\u9fa5]", "", clean)
    return clean[:12] or "project"


def classify(text: str) -> float:
    hp = sum(1 for w in SENT_POS if w in text)
    hn = sum(1 for w in SENT_NEG if w in text)
    # v0.26.2：正负情感词之外再叠加"抱怨/报错/等太久"等工具体验信号
    if re.search(r"(?:怎么|又|还|一直|总是).{0,6}(?:没|不|卡|慢|错|崩|停)", text):
        hn += 1
    if hp and not hn:
        return 0.6
    if hn > hp:
        return -0.6
    if hn == hp and hn:
        return -0.35
    return 0.02                       # 中性轻微上行即可，不再一路向好


def _looks_error(out: str) -> bool:
    """运行输出是否像报错/失败（供计划-执行-验证环判定是否需要自动修复）。"""
    if not out or not out.strip():
        return True
    low = out.lower()
    bad = ("traceback", "modulenotfounderror", "importerror", "filenotfounderror",
           "syntaxerror", "indentationerror", "nameerror", "typeerror",
           "valueerror", "keyerror", "indexerror", "attributeerror",
           "permissionerror", "unknownerror", "异常", "报错", "失败",
           "拒绝访问", "不是内部或外部命令", "找不到文件", "无法启动")
    return any(b in low for b in bad)


def detect_local_llm() -> dict:
    import urllib.request
    for name, base in LOCAL_ENDPOINTS:
        try:
            with urllib.request.urlopen(base + "/models", timeout=1) as r:
                if r.status == 200:
                    return {"name": name, "base_url": base}
        except Exception:
            continue
    return {}


def list_ollama_models() -> list:
    import urllib.request
    try:
        with urllib.request.urlopen(OLLAMA_API + "/api/tags", timeout=2) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def _size_of(model: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)b", model.lower())
    return float(m.group(1)) if m else 999.0


def pick_local_model(models: list) -> str:
    """挑最小且适合聊天的本地模型（越小越快）。"""
    if not models:
        return ""
    cand = [m for m in models if "embed" not in m.lower()]
    if not cand:
        cand = models
    cand.sort(key=lambda m: (_size_of(m), not ("qwen" in m.lower())))
    return cand[0]


# ================= v0.30.7 提示词预算装配 =================
def _join_budget(blocks, budget: int):
    """按优先级把提示块装进预算，超预算就**整块**丢掉（绝不腰斩半句）。

    blocks: [(prio, idx, name, text)]；prio 小的先装，prio==0 视为**必须保留**。
    返回 (提示词, 被丢弃的块名列表)。块在输出里仍按 idx（原始）顺序拼接 ——
    块的先后影响模型阅读顺序，不能按优先级重排。
    """
    items = [b for b in blocks if str(b[3] or "").strip()]
    kept, dropped, total = [], [], 0
    for prio, idx, name, txt in sorted(items, key=lambda x: x[0]):
        if prio > 0 and total + len(txt) > budget:
            dropped.append(name)
            continue
        kept.append((idx, txt))
        total += len(txt)
    kept.sort(key=lambda x: x[0])
    return "".join(t for _, t in kept), dropped


def md_to_html(text: str) -> str:
    """轻量 Markdown → HTML：代码块 / 行内码 / 标题(1-6) / 无序·有序列表 /
    引用 / 粗体 / 链接 / 段落。资料库阅读器与聊天共用。"""
    # v0.28.3 防丢字：残留的内嵌思考段（<think>…</think>）若进 QTextBrowser
    # 会被当**未知 HTML 标签连同内容一起吞掉**（真机"回复缺字"根因），先剥掉
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    _i = text.find("<think>")
    if _i >= 0:
        text = text[:_i]

    def esc(s: str) -> str:
        return html.escape(s)

    def inline(s: str) -> str:
        s = esc(s)                                   # 先整体转义，防 HTML 注入/双重转义
        s = re.sub(r"`([^`\n]+)`",
                   lambda m: "<code style='background:#eef2f7;border-radius:4px;"
                             "padding:0 4px;font-family:Consolas,monospace;"
                             "font-size:12px;'>" + m.group(1) + "</code>", s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        # 本地/网络图片：![alt](file:///… 或 C:\\… 或 http…)  → <img>
        def _img(m):
            alt = m.group(1)
            src = m.group(2).strip()
            if src.startswith("file:///"):
                pass
            elif src.startswith(("http://", "https://")):
                pass
            else:
                src = CRE._as_uri(src)
            return ("<img src='%s' alt='%s' style='max-width:420px;max-height:420px;"
                    "border-radius:10px;margin:6px 0;box-shadow:0 2px 10px rgba(15,23,42,.15);'>"
                    % (src, alt))
        s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", _img, s)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)",
                   r"<a style='color:#0ea5e9;text-decoration:none;' href='\2'>\1</a>", s)
        return s

    def fence(code: str) -> str:
        return ("<pre style='background:#0f172a;color:#e2e8f0;border-radius:8px;"
                "padding:10px;font-family:Consolas,monospace;font-size:12px;"
                "line-height:1.5;white-space:pre-wrap;'>"
                + esc(code) + "</pre>")

    def blockquote(lines: list) -> str:
        return ("<div style='border-left:3px solid #cbd5e1;background:#f8fafc;"
                "color:#475569;padding:6px 10px;margin:6px 0;border-radius:0 6px 6px 0;'>"
                + "".join("<p style='margin:2px 0;'>" + inline(x) + "</p>"
                          for x in lines) + "</div>")

    def heading(lvl: int, s: str) -> str:
        size = {1: 20, 2: 17, 3: 15}.get(lvl, 14)
        color = {1: "#0f172a", 2: "#1e293b", 3: "#334155"}.get(lvl, "#475569")
        return ("<h%d style='font-size:%dpx;color:%s;margin:10px 0 4px;'>%s</h%d>"
                % (min(lvl, 4), size, color, inline(s), min(lvl, 4)))

    def _cells(row: str):
        """把 `| a | b |` 拆成 [a, b]（首尾空列丢掉）。"""
        t = row.strip()
        if t.startswith("|"):
            t = t[1:]
        if t.endswith("|"):
            t = t[:-1]
        return [c.strip() for c in t.split("|")]

    def tbl(head, rows) -> str:
        """v0.30.6 新增：Markdown 表格 → Qt 富文本表格。

        原来 md_to_html **完全没有表格支持** —— 模型输出的表格会退化成一堆
        带竖线的普通段落（真机观感"很业余"）。用 Qt 支持的 border/cellspacing/
        bgcolor 属性（CSS 的 border-collapse 在 QTextBrowser 里不生效）。
        """
        h = ("<table border='1' cellspacing='0' cellpadding='5' "
             "style='border-color:#e2e8f0;margin:6px 0;'>")
        if head:
            h += "<tr>" + "".join("<td bgcolor='#f1f5f9'><b>" + inline(c) + "</b></td>"
                                  for c in head) + "</tr>"
        for r in rows:
            h += "<tr>" + "".join("<td>" + inline(c) + "</td>" for c in r) + "</tr>"
        return h + "</table>"

    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = []
    i = 0
    n = len(lines)
    while i < n:
        ln = lines[i]
        s = ln.strip()
        # 代码块
        if s.startswith("```"):
            buf, i = [], i + 1
            while i < n and not lines[i].strip().startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1  # 跳过闭合 ```（可能不存在，i 已到 n）
            out.append(fence("\n".join(buf)))
            continue
        # 标题
        hm = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", s)
        if hm:
            out.append(heading(len(hm.group(1)), hm.group(2)))
            i += 1
            continue
        # 引用块（连续行）
        if s.startswith(">"):
            buf, i = [], i + 1
            while i < n and lines[i].strip().startswith(">"):
                buf.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(blockquote(buf))
            continue
        # v0.30.6：表格（`| a | b |` + 分隔行）。必须排在段落兜底之前，
        # 否则整张表会被当成普通段落、连竖线一起原样吐出来。
        if ("|" in s) and (i + 1 < n) and re.match(
                r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$", lines[i + 1]):
            _head = _cells(s)
            i += 2                                   # 跳过表头行 + 分隔行
            _rows = []
            while i < n and "|" in lines[i] and lines[i].strip():
                _rows.append(_cells(lines[i]))
                i += 1
            out.append(tbl(_head, _rows))
            continue
        # 无序列表（连续项；空行/结构行结束）
        if re.match(r"^[-*•]\s+", s):
            items, i = [], i + 1
            while i <= n:
                cur = lines[i - 1].strip()
                if re.match(r"^[-*•]\s+", cur):
                    items.append(inline(re.sub(r"^[-*•]\s+", "", cur)))
                    i += 1
                else:
                    break
            out.append("<ul style='margin:6px 0;padding-left:22px;'>" +
                       "".join("<li style='margin:2px 0;'>" + x + "</li>"
                               for x in items) + "</ul>")
            continue
        # 有序列表
        om = re.match(r"^(\d+)[.、)]\s+(.*)", s)
        if om:
            items, i = [], i + 1
            while i <= n:
                cur = lines[i - 1].strip()
                om2 = re.match(r"^(\d+)[.、)]\s+(.*)", cur)
                if om2:
                    items.append(inline(om2.group(2)))
                    i += 1
                else:
                    break
            out.append("<ol style='margin:6px 0;padding-left:22px;'>" +
                       "".join("<li style='margin:2px 0;'>" + x + "</li>"
                               for x in items) + "</ol>")
            continue
        # 分隔线
        if re.match(r"^(-{3,}|\*{3,}|_{3,})$", s):
            out.append("<hr style='border:none;border-top:1px solid #e2e8f0;margin:8px 0;'>")
            i += 1
            continue
        if not s:
            i += 1
            continue
        # 普通段落：收集连续文本行（遇结构行/空行停）
        para, i = [ln], i + 1
        while i < n:
            s3 = lines[i].strip()
            if (not s3 or "|" in s3 or
                    re.match(r"^(#{1,6}\s|[-*•]\s|\d+[.、)]\s|```|>|-{3,}$)", s3)):
                break
            para.append(lines[i])
            i += 1
        body_lines = [inline(x.strip()) for x in para if x.strip()]
        if body_lines:
            out.append("<p style='margin:5px 0;line-height:1.7;'>" +
                       "<br>".join(body_lines) + "</p>")
    return "\n".join(out) if out else "<p></p>"


class FlowLayout(QLayout):
    """自动换行流式布局：一行排得下就排一行，排不下才换行（chips 工种分栏用）。"""

    def __init__(self, parent=None, margin=0, hspacing=6, vspacing=6):
        super().__init__(parent)
        self._items = []
        self._hs, self._vs = hspacing, vspacing
        self.setContentsMargins(margin, margin, margin, margin)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._measure(width)[1]

    def sizeHint(self):
        w, h = self._measure(900)
        return QSize(w, h)

    def minimumSize(self):
        w, h = self._measure(300)
        return QSize(w, h)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        m = self.contentsMargins()
        # 关键：子件位置必须以父布局分给我们的真实 rect 左上角为基准，
        # 而不是 (0,0)——否则 chips 会全堆到页面最顶部（挡住头像/名字区）。
        self._measure(rect.width(), place=True,
                      ox=rect.x() + m.left(), oy=rect.y() + m.top())

    def _measure(self, width_hint, place=False, ox=0, oy=0):
        """返回 (内容宽, 总高)；place=True 时把子件摆到 (ox,oy) 起的矩形内。"""
        m = self.contentsMargins()
        avail = width_hint - m.left() - m.right()
        if avail <= 0:
            avail = 900
        x, y, line_h, max_w = 0, 0, 0, 0
        for it in self._items:
            wid = it.widget()
            if wid is None:
                continue
            hint = it.sizeHint()
            ww, hh = hint.width(), hint.height()
            if x > 0 and x + ww + self._hs > avail:
                x = 0
                y += line_h + self._vs
                line_h = 0
            if place:
                it.setGeometry(QRect(ox + x, oy + y, ww, hh))
            x += ww + self._hs
            max_w = max(max_w, x - self._hs)
            line_h = max(line_h, hh)
        if not self._items:
            return m.left() + m.right(), m.top() + m.bottom()
        return (max_w + m.left() + m.right(),
                (y - m.top()) + line_h + m.top() + m.bottom())


class ElideListDelegate(QStyledItemDelegate):
    """QListWidget 项：文本超宽自动用 … 省略号截断（配合无滚动条列表）。"""

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        if opt.text:
            fm = opt.fontMetrics
            opt.text = fm.elidedText(opt.text, Qt.ElideRight,
                                     max(12, opt.rect.width() - 8))
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)


class _CatPill(QPushButton):
    """分类胶囊按钮：胶囊本体右上角内置小「✕」（仅可删分类）。

    - 点击胶囊主体 = 选中该分类；点右上角 ✕ 圆形热区 = 删除分类（先确认再删内容）。
    - 「全部」不可删 → 不绘制 ✕。单个控件直接进流式布局，天然整齐对齐。
    """

    H = 26
    _ZONE_W = 22

    def __init__(self, text, selected, deletable, on_pick, on_del=None):
        super().__init__(text)
        self._text = text
        self._del_zone = bool(deletable)
        self._on_del = on_del
        self._selected = bool(selected)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(self.H)
        self.clicked.connect(lambda _=False: on_pick())
        self.set_selected(self._selected)

    def set_selected(self, sel):
        self._selected = bool(sel)
        right = self._ZONE_W + 6 if self._del_zone else 14
        if sel:
            self.setStyleSheet(
                "QPushButton{background:#0ea5e9;border:none;border-radius:13px;"
                "color:#fff;font-size:11px;font-weight:bold;"
                "padding:0 %dpx 0 12px;text-align:left;}" % right)
        else:
            self.setStyleSheet(
                "QPushButton{background:#f8fafc;border:1px solid #dbe3ee;"
                "border-radius:13px;color:#334155;font-size:11px;"
                "padding:0 %dpx 0 11px;text-align:left;}"
                "QPushButton:hover{background:#e0f2fe;border-color:#7dd3fc;color:#0369a1;}"
                % right)

    def paintEvent(self, e):
        super().paintEvent(e)
        if not self._del_zone:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        hot = self.rect().contains(self.mapFromGlobal(QCursor.pos()))
        circ = QRect(self.width() - self._ZONE_W, (self.height() - 16) // 2, 16, 16)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#e9edf3") if not hot else QColor("#fecaca"))
        p.drawEllipse(circ)
        p.setPen(QColor("#64748b") if not hot else QColor("#dc2626"))
        f = p.font(); f.setBold(True); f.setPointSize(8); p.setFont(f)
        p.drawText(circ, Qt.AlignCenter, "✕")
        p.end()

    def mouseReleaseEvent(self, e):
        if self._del_zone and e.button() == Qt.LeftButton and \
                e.position().x() >= self.width() - self._ZONE_W:
            e.accept()
            if self._on_del:
                self._on_del(self._text)
            return
        super().mouseReleaseEvent(e)


class ChatInput(QTextEdit):
    """多行输入框：Enter=发送，Shift+Enter=换行；圆角科技风。

    - 输入「/」：弹出 技能/指令 快选列表
    - 输入「@」：弹出 可引用文件/资料 快选（同款弹层，↑↓/Enter/Esc 一致），
      选中后由主窗口在输入框上方生成一枚引用条目 chip（WorkBuddy 式）。
    """
    submit = Signal()
    at_picked = Signal(str, str)        # (显示名, 引用写法 @路径/@“含空格路径”)
    images_given = Signal(list)         # v0.27 Phase B：拖入/粘贴的图片路径列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setAcceptDrops(True)
        self.setStyleSheet(
            "QTextEdit{border:1px solid #d8e0ea;border-radius:14px;"
            "background:#ffffff;padding:7px 12px;font-size:13px;}"
            "QTextEdit:hover{border-color:#b6c6da;}"
            "QTextEdit:focus{border:2px solid #0ea5e9;}")
        self.slash_blocks = []          # [(组标题, [(标签, 命令), ...]), ...]
        self.slash_run = None           # 选择后的执行回调(cmd)
        self.at_blocks = None           # @候选数据源回调(query) → [(组标题, [(名,引用), ...])]
        self._pop = None                # 斜杠/@ 快选列表（同一弹层）
        self._pop_mode = "slash"        # 当前弹层模式: slash | at
        self._in_set = False            # 程序改文本时跳过联动
        self.textChanged.connect(self._on_text_changed)

    # ---------- 引用快选辅助 ----------
    @staticmethod
    def _looks_at(q: str) -> bool:
        """末尾的 @查询 是否值得弹引用候选（手输完整路径就不打扰）。"""
        if q is None:
            return True
        if not q:
            return True
        if q[0] in ('“', '"', "'", "【", "["):
            return False
        if "\\" in q or re.match(r"^[A-Za-z]:[\\/]", q) or "://" in q:
            return False
        return len(q) <= 40

    def set_text_quiet(self, t: str):
        """程序改文本：不触发弹层联动；光标移到末尾。"""
        self._in_set = True
        try:
            self.setPlainText(t)
            c = self.textCursor()
            c.movePosition(QTextCursor.End)
            self.setTextCursor(c)
        finally:
            self._in_set = False

    # ---------- 斜杠快选 ----------
    def _ensure_pop(self):
        if self._pop is None:
            p = self.window()
            self._pop = QListWidget(p)
            self._pop.setAttribute(Qt.WA_ShowWithoutActivating)   # 不抢输入焦点
            self._pop.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self._pop.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self._pop.setStyleSheet(
                "QListWidget{background:#ffffff;border:1px solid #d8e0ea;"
                "border-radius:12px;padding:4px;}"
                "QListWidget::item{padding:5px 8px;border-radius:6px;font-size:12px;}"
                "QListWidget::item:hover{background:#e0f2fe;color:#0c4a6e;}"
                "QListWidget::item:selected{background:#bae6fd;color:#0c4a6e;font-weight:bold;}")
            self._pop.itemClicked.connect(lambda it: self._choose(it))
        return self._pop

    def _pop_refill(self, query=""):
        pop = self._ensure_pop()
        if self._pop_mode == "at" and callable(self.at_blocks):
            src = self.at_blocks(query) or []
        else:
            self._pop_mode = "slash"
            src = self.slash_blocks() if callable(self.slash_blocks) \
                else self.slash_blocks
        q = query.strip().lower()
        pop.clear()
        vis = 0
        for head, items in (src or []):
            keep = [(lab, val) for lab, val in items
                    if not q or q in lab.lower() or q in val.lower()]
            if not keep:
                continue
            h = QListWidgetItem(head)
            h.setFlags(Qt.NoItemFlags)          # 组标题：灰显不可选
            f = h.font(); f.setBold(True); f.setPointSize(8); h.setFont(f)
            pop.addItem(h)
            for lab, val in keep:
                it = QListWidgetItem(lab)
                it.setData(Qt.UserRole, val)
                it.setData(Qt.UserRole + 1, lab)
                it.setToolTip(val)
                pop.addItem(it)
                vis += 1
        if vis == 0:
            self._hide_pop()
            return
        self._pop = pop
        self._pop.show()
        self._pop.raise_()
        first = self._first_enabled()
        if first is not None:
            self._pop.setCurrentRow(first)
        self._place_pop()

    def _first_enabled(self):
        for i in range(self._pop.count()):
            it = self._pop.item(i)
            if it.flags() & Qt.ItemIsEnabled:
                return i
        return None

    def _next_enabled(self, delta):
        n = self._pop.count()
        i = self._pop.currentRow()
        for _ in range(n):
            i += delta
            if i < 0:
                i = n - 1
            elif i >= n:
                i = 0
            if self._pop.item(i).flags() & Qt.ItemIsEnabled:
                return i
        return self._first_enabled()

    def _place_pop(self):
        pop = self._pop
        h = pop.sizeHintForRow(0) * min(pop.count(), 12) + 30 if pop.count() else 120
        h = max(120, min(330, h + 20))
        wnd = self.window()
        tl = self.mapToGlobal(QPoint(0, 0))
        p = wnd.mapFromGlobal(tl)
        x = max(6, p.x())
        y = max(6, p.y() - h)
        pop.setGeometry(x, y, max(360, self.width()), h)

    def _on_text_changed(self):
        if self._in_set:
            return
        t = self.toPlainText()
        if t.startswith("/") and len(t) <= 40:
            if self._pop is None or not self._pop.isVisible():
                if len(t) == 1 or (t.count(" ") == 0 and "/" in t[1:]):
                    self._pop_mode = "slash"
                    self._pop_refill(t[1:])
            else:
                self._pop_refill(t[1:])
            return
        # @ 引用候选：光标停在末尾、末段是 @名字 → 弹可引用文件快选
        if "@" in t and self.textCursor().position() == len(t):
            m = re.search(r'@([^\s@，。；！？（）]*)$', t)
            if m and self._looks_at(m.group(1)):
                self._pop_mode = "at"
                self._pop_refill(m.group(1))
                return
        self._hide_pop()

    def _hide_pop(self):
        if self._pop is not None:
            self._pop.hide()

    def _choose(self, item):
        if item is None or not (item.flags() & Qt.ItemIsEnabled):
            return
        if self._pop_mode == "at":
            ref = item.data(Qt.UserRole) or ""
            label = item.data(Qt.UserRole + 1) or item.text()
            self._hide_pop()
            if not ref:
                return
            t = self.toPlainText()
            t2 = re.sub(r'@([^\s@，。；！？（）]*)$', lambda _m: ref + " ", t,
                        count=1)
            self.set_text_quiet(t2)
            self.at_picked.emit(label, ref)
            return
        cmd = item.data(Qt.UserRole)
        self._hide_pop()
        self._in_set = True
        try:
            self.clear()
        finally:
            self._in_set = False
        if cmd and self.slash_run:
            self.slash_run(cmd)

    # ---------- v0.27 Phase B：图片拖入 / 粘贴 → 发给主窗口看图 ----------
    _IMG_RE = re.compile(r"\.(png|jpe?g|bmp|webp|gif)$", re.I)

    def _img_paths_from_mime(self, md):
        paths = []
        if md is None:
            return paths
        if md.hasUrls():
            for u in md.urls():
                if u.isLocalFile() and self._IMG_RE.search(u.toLocalFile() or ""):
                    paths.append(u.toLocalFile())
        if not paths and md.hasImage():     # 截图直贴（剪贴板裸图，无文件）
            try:
                img = md.imageData()
                if img is not None and not img.isNull():
                    import tempfile
                    p = os.path.join(tempfile.gettempdir(),
                                     "pasm_paste_%d.png" % int(time.time() * 1000))
                    if img.save(p, "PNG"):
                        paths.append(p)
            except Exception:
                pass
        return paths

    def dragEnterEvent(self, e):
        if self._img_paths_from_mime(e.mimeData()):
            e.acceptProposedAction()
            return
        super().dragEnterEvent(e)

    def dragMoveEvent(self, e):
        if self._img_paths_from_mime(e.mimeData()):
            e.acceptProposedAction()
            return
        super().dragMoveEvent(e)

    def dropEvent(self, e):
        paths = self._img_paths_from_mime(e.mimeData())
        if paths:
            e.acceptProposedAction()
            self.images_given.emit(paths)
            return
        super().dropEvent(e)

    def canInsertFromMimeData(self, md):
        if self._img_paths_from_mime(md):
            return True
        return super().canInsertFromMimeData(md)

    def insertFromMimeData(self, md):
        paths = self._img_paths_from_mime(md)
        if paths:
            self.images_given.emit(paths)
            return
        super().insertFromMimeData(md)

    def keyPressEvent(self, e):
        k = e.key()
        pop_open = self._pop is not None and self._pop.isVisible()
        if k in (Qt.Key_Return, Qt.Key_Enter) and \
                not (e.modifiers() & Qt.ShiftModifier):
            if pop_open:
                it = self._pop.currentItem()
                if it is not None and (it.flags() & Qt.ItemIsEnabled):
                    self._choose(it)
                    e.accept()
                    return
            self.submit.emit()
            e.accept()
            return
        if pop_open:
            if k in (Qt.Key_Up, Qt.Key_Down):
                self._pop.setCurrentRow(
                    self._next_enabled(1 if k == Qt.Key_Down else -1))
                e.accept()
                return
            if k == Qt.Key_Tab:
                self._pop.setCurrentRow(self._next_enabled(1))
                e.accept()
                return
            if k == Qt.Key_Escape:
                self._hide_pop()
                e.accept()
                return
        super().keyPressEvent(e)


class SettingsDialog(QDialog):
    voice_result = Signal(str)          # 试听结果回传（worker → UI 线程）

    def __init__(self, cfg, local_models, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(660)
        self.cfg = cfg
        self.voice_result.connect(self._on_voice_result)
        # v0.30.4：设置面板改「分页 + 分栏」。
        # 旧版是一根长条（名字→权限→语音→视频引擎→模型→思考…），要点保存得
        # 滚到底，找一项要来回拖。现在按主题分成 4 页，页内再分左右两栏，
        # 常用项一屏可见，保存按钮固定在底部任何页都能点。
        _outer = QVBoxLayout(self)
        _outer.setContentsMargins(14, 12, 14, 12)
        _outer.setSpacing(10)
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #dbe3ec;border-radius:10px;"
            "background:#ffffff;top:-1px;}"
            "QTabBar::tab{padding:7px 18px;margin-right:3px;color:#475569;"
            "font-size:12px;border:1px solid transparent;"
            "border-top-left-radius:8px;border-top-right-radius:8px;}"
            "QTabBar::tab:selected{color:#1d4ed8;background:#ffffff;"
            "border-color:#dbe3ec;border-bottom-color:#ffffff;font-weight:600;}"
            "QTabBar::tab:hover{color:#1e293b;}")
        _outer.addWidget(self.tabs, 1)

        def _tab(title, cols=1):
            "建一个设置页；cols=2 时返回 [左栏, 右栏]，可直接 rebind 给 lay。"
            _w = QWidget()
            _v = QVBoxLayout(_w)
            _v.setContentsMargins(18, 16, 18, 16)
            _v.setSpacing(9)
            _h = QHBoxLayout()
            _h.setSpacing(28)
            _v.addLayout(_h)
            _cols = []
            for _i in range(cols):
                _c = QVBoxLayout()
                _c.setSpacing(9)
                _h.addLayout(_c, 1)
                _cols.append(_c)
            _v.addStretch(1)
            self.tabs.addTab(_w, title)
            return _cols

        _p_basic = _tab("🎭 基本", cols=2)
        lay = _p_basic[0]
        def row(label, edit):
            h = QHBoxLayout(); h.addWidget(QLabel(label)); h.addWidget(edit, 1)
            lay.addLayout(h); return edit
        self.name = row("它的名字：", QLineEdit(cfg.get("name", "小U")))
        # v0.29 开机自动启动：只写 HKCU 的 Run 键（当前用户，不需要管理员提权）
        self.autostart = QCheckBox("开机自动启动（登录 Windows 后自动打开）")
        try:
            if AUTOSTART.is_supported():
                self.autostart.setChecked(AUTOSTART.is_enabled())
            else:
                self.autostart.setChecked(False)
                self.autostart.setEnabled(False)
                self.autostart.setText("开机自动启动（当前系统不支持）")
        except Exception:
            self.autostart.setEnabled(False)
        lay.addWidget(self.autostart)
        # v0.29 权限三档（参考 WorkBuddy 的默认权限）。默认「安全」，
        # 切到「完全访问」时 save() 会弹显式警告并二次确认。
        self.perm = QComboBox()
        for _lv in PERM.LEVELS:
            self.perm.addItem(PERM.LABELS[_lv], _lv)
        _pl_now = PERM.normalize_level(cfg.get("perm_level"))
        for _i in range(self.perm.count()):
            if self.perm.itemData(_i) == _pl_now:
                self.perm.setCurrentIndex(_i)
                break
        row("权限档位：", self.perm)
        self.perm_hint = QLabel("")
        self.perm_hint.setWordWrap(True)
        self.perm_hint.setStyleSheet("color:#94a3b8;font-size:11px;padding-left:2px;")
        lay.addWidget(self.perm_hint)
        self.perm.currentIndexChanged.connect(self._perm_hint_sync)
        self._perm_hint_sync()
        # v0.29 语音唤醒：说角色名即可叫醒。唤醒词**就是名字本身**，
        # 所以改名字后唤醒词自动跟着变（不需要重训模型，也不需要重启）。
        self.wake = QCheckBox("语音唤醒（说「%s」叫醒我）"
                              % str(cfg.get("name", "小U")))
        self.wake.setChecked(bool(cfg.get("wake_enabled")))
        self.wake.setToolTip("开启后会常驻监听麦克风，检测到你在叫它的名字就自动进入待命。\n"
                             "不习惯时可随时关掉 —— 关掉后麦克风完全不再占用。")
        lay.addWidget(self.wake)
        self.wake_hint = QLabel("开启后常驻监听麦克风（说「名字」即可叫醒）；关闭后完全不占用麦克风。")
        self.wake_hint.setWordWrap(True)
        self.wake_hint.setStyleSheet("color:#94a3b8;font-size:11px;padding-left:2px;")
        lay.addWidget(self.wake_hint)
        # 名字一改，开关上的提示文字立刻跟上 —— 让用户当场看到"唤醒词会跟着变"
        self.name.textChanged.connect(self._wake_label_sync)
        lay = _p_basic[1]        # 右栏：人格 / 形象 / 声音
        self.persona = QComboBox()
        self.persona.addItems(list(PERSONALITIES))
        self.persona.setCurrentText(cfg.get("persona", "温和沉稳"))
        row("性格档案：", self.persona)
        self.gender = QComboBox()
        g_now = GROWTH.pet_state().get("gender", "none")
        self.gender.addItems([lab for lab, _ in _GENDER_LABELS])
        self.gender.setCurrentText(next((lab for lab, v in _GENDER_LABELS
                                         if v == g_now), "无(中性)"))
        row("形象性别：", self.gender)
        self.voice_engine = QComboBox()
        self.voice_engine.addItem("拟真语音(在线，失败自动回落)", "auto")
        self.voice_engine.addItem("仅系统语音(离线)", "sapi")
        cur_ve = cfg.get("voice_engine", "auto")
        for i in range(self.voice_engine.count()):
            if self.voice_engine.itemData(i) == cur_ve:
                self.voice_engine.setCurrentIndex(i)
                break
        row("语音引擎：", self.voice_engine)
        self.persona_note = QLabel("语音会随 性格（急缓文静）× 性别（男女声）× 成长阶段"
                                   "（幼儿奶声→少年→青年）× 此刻情绪 自动切换，无需逐条设置。")
        self.persona_note.setWordWrap(True)
        self.persona_note.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(self.persona_note)
        _p_voice = _tab("🔊 声音")           # ── 第 2 页：试听与音量自愈
        lay = _p_voice[0]
        # 声线试听：选一个年龄段立刻听（性别/性格取上面下拉框的当前值）
        ph = QHBoxLayout()
        ph.addWidget(QLabel("试听声线（取上方性别/性格）："))
        self.age_preview = QComboBox()
        self.age_preview.addItem("幼儿（奶声奶气）", 0)
        self.age_preview.addItem("童年（真童声）", 1)
        self.age_preview.addItem("少年（少年音）", 2)
        self.age_preview.addItem("青年（成年声线）", 3)
        self.age_preview.addItem("成年（沉稳声线）", 4)
        self.age_preview.setCurrentIndex(max(0, min(4, GROWTH.current_growth())))
        ph.addWidget(self.age_preview, 1)
        pb = QPushButton("🔊 试听")
        pb.setCursor(Qt.PointingHandCursor)
        pb.clicked.connect(self._preview_voice)
        ph.addWidget(pb)
        sb = QPushButton("🔔 系统音")
        sb.setCursor(Qt.PointingHandCursor)
        sb.setToolTip("播放 Windows 系统提示音：验证扬声器/音量本身是否正常（与语音引擎无关）")
        sb.clicked.connect(self._play_sys_sound)
        ph.addWidget(sb)
        lay.addLayout(ph)
        self.preview_lbl = QLabel("")
        self.preview_lbl.setWordWrap(True)
        self.preview_lbl.setStyleSheet("color:#16a34a;font-size:11px")
        lay.addWidget(self.preview_lbl)
        # v0.18.0：音频自愈开关（默认关=朗读/点小人绝不自动改系统音量）
        self.audio_fix_chk = QCheckBox(
            "允许自动调整系统音量（静音/音量过低时自动恢复；默认关——绝不改你电脑的音量）")
        self.audio_fix_chk.setChecked(bool(cfg.get("audio_autofix", False)))
        self.audio_fix_chk.setStyleSheet("color:#64748b;font-size:11px")
        self.audio_fix_chk.toggled.connect(self._on_audio_fix_toggle)
        lay.addWidget(self.audio_fix_chk)
        _p_engine = _tab("🎨 创作引擎")       # ── 第 3 页：出图/出片引擎
        lay = _p_engine[0]
        # ---- v0.21.0 视频引擎（图生视频：漫剧/短片真视频）----
        sep = QLabel("视频引擎（漫剧/短片真视频）")
        sep.setStyleSheet("color:#64748b;font-size:11px")
        lay.addWidget(sep)
        self.video_engine = QComboBox()
        self.video_engine.addItem("不启用（静态图运镜，零成本）", "none")
        self.video_engine.addItem("即梦 Seedance（火山方舟，云端）", "seedance")
        self.video_engine.addItem("本地 ComfyUI（开源模型，免费）", "comfyui")
        cur_vp = cfg.get("video_provider", "none")
        for i in range(self.video_engine.count()):
            if self.video_engine.itemData(i) == cur_vp:
                self.video_engine.setCurrentIndex(i)
                break
        row("视频引擎：", self.video_engine)
        self.ark_key = row("方舟 API Key（可空）：", QLineEdit(cfg.get("ark_key", "")))
        self.ark_key.setEchoMode(QLineEdit.Password)
        # v0.23.1：模型改下拉（可手填）；旧默认 lite-i2v-250428 已在方舟下线
        # （调用即 404），存了旧值的自动迁移到当前在售推荐模型。
        self.ark_model = QComboBox()
        self.ark_model.setEditable(True)
        self.ark_model.addItems(list(getattr(VE, "_ARK_MODEL_CHOICES",
                                             [VE._DEFAULT_ARK_MODEL])))
        _ark_cur = str(cfg.get("ark_model") or "").strip()
        if not _ark_cur or _ark_cur in getattr(VE, "_RETIRED_ARK_MODELS", set()):
            _ark_cur = VE._DEFAULT_ARK_MODEL
        self.ark_model.setCurrentText(_ark_cur)
        self.ark_model.setToolTip(
            "推荐 doubao-seedance-1-5-pro-251215；2.0 系画质更强，1.0 pro 更便宜。\n"
            "旧 lite 系列已在方舟下线（会 404），已自动迁移。也可手填推理接入点 ep-xxx。")
        row("方舟模型：", self.ark_model)
        self.ark_audio = QCheckBox("方舟视频带同步音频（仅 1.5 系生效；漫剧有自己的配音，默认关）")
        self.ark_audio.setChecked(bool(cfg.get("ark_audio", False)))
        self.ark_audio.setStyleSheet("color:#64748b;font-size:11px")
        lay.addWidget(self.ark_audio)
        self.comfy_url = row("ComfyUI 地址：",
                             QLineEdit(cfg.get("comfy_url", "http://127.0.0.1:8188")))
        # v0.22.2：ComfyUI 自动配置向导（不用自己搭工作流）
        wiz_row = QHBoxLayout()
        wiz_hint = QLabel("ComfyUI 不会配？")
        wiz_hint.setStyleSheet("color:#64748b;font-size:11px")
        wiz_row.addWidget(wiz_hint)
        wiz_btn = QPushButton("🧰 一键配置向导（检测→示例→导入→自检）")
        wiz_btn.setCursor(Qt.PointingHandCursor)
        wiz_btn.clicked.connect(
            lambda: (ComfyWizard(self.cfg, self).exec(), self._vp_status()))
        wiz_row.addWidget(wiz_btn, 1)
        lay.addLayout(wiz_row)
        # v0.22.4：随 PASM 启动/退出托管本机 ComfyUI
        self.comfy_manage = QCheckBox(
            "随 PASM 启动/退出本机 ComfyUI（需先装好 ComfyUI 便携版，并勾选下方「✓ 已装」由向导找到它）")
        self.comfy_manage.setChecked(bool(cfg.get("comfy_manage", False)))
        self.comfy_manage.setStyleSheet("color:#64748b;font-size:11px")
        lay.addWidget(self.comfy_manage)
        ve_note = QLabel("即梦 Seedance：火山方舟开通模型后填 Key，一镜 5s 约几毛到几元。\n"
                         "本地 ComfyUI：先在本机运行 ComfyUI 并搭好图生视频流程\n"
                         "（Wan2.2 / LTX 均可），用 Export (API) 导出 JSON 存到\n"
                         "%APPDATA%\\PASMStudio\\comfy_i2v_workflow.json。\n"
                         "不启用 / 某镜失败时，该镜自动退回静态图运镜模式。")
        ve_note.setWordWrap(True)
        ve_note.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(ve_note)
        # v0.22.1：选完引擎立刻做本地就绪检查（workflow 文件/Key 是否存在），
        # 免去"选了 ComfyUI 却没放 workflow → 成片静默变静态"的困惑
        self.ve_status = QLabel("")
        self.ve_status.setWordWrap(True)
        self.ve_status.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(self.ve_status)
        self.video_engine.currentIndexChanged.connect(self._vp_status)
        self._vp_status()
        _p_model = _tab("🧠 模型与思考")      # ── 第 4 页：大脑与推理
        lay = _p_model[0]
        self.key = row("云端 API Key（可空）：", QLineEdit(cfg.get("api_key", "")))
        self.key.setEchoMode(QLineEdit.Password)
        self.base = row("云端 Base URL：", QLineEdit(cfg.get("base_url", "https://api.deepseek.com/v1")))
        self.model = row("云端 Model：", QLineEdit(cfg.get("model", "deepseek-chat")))
        h = QHBoxLayout()
        h.addWidget(QLabel("本地 Ollama 模型："))
        self.local = QComboBox()
        self.local.addItem("（自动选择）")
        self.local.addItems(local_models or ["（未检测到，请先运行 ollama）"])
        h.addWidget(self.local, 1)
        refresh = QPushButton("刷新")
        refresh.clicked.connect(self._refresh)
        h.addWidget(refresh)
        lay.addLayout(h)
        info = QLabel(f"已检测到本地模型：{'、'.join(local_models) if local_models else '无'}"
                      "\n（完全免 Key 离线对话；未启动 Ollama 时自动退回离线微脑）")
        info.setWordWrap(True)
        info.setStyleSheet("color:#64748b;font-size:12px")
        lay.addWidget(info)
        # v0.27.13：思考链改为「按价值分配」——不是简单的开/关。
        # 真机实测：同一句闲聊，开思考 136.8s、关思考 4.8s（答案没变好）；
        # 但常识陷阱题，关思考答错、开思考答对。所以默认"智能"：
        # 聊天永远不开；后台提炼/自学自测开；重活只在问题确实需要推理时才开。
        trow = QHBoxLayout()
        trow.addWidget(QLabel("本地模型思考链："))
        self.think_mode = QComboBox()
        self.think_mode.addItem("智能（推荐：只在需要推理时才深思）", "auto")
        self.think_mode.addItem("关闭（最快，任何情况都不想）", "off")
        self.think_mode.addItem("始终开启（最严谨，但等待显著变长）", "on")
        _tm = cfg.get("local_think_mode") or (
            "on" if cfg.get("local_think") else "auto")
        _idx = self.think_mode.findData(_tm)
        self.think_mode.setCurrentIndex(_idx if _idx >= 0 else 0)
        trow.addWidget(self.think_mode)
        trow.addStretch(1)
        lay.addLayout(trow)
        tinfo = QLabel("  开启思考时会给足输出配额，并在超预算时保留已想到的思路继续收敛"
                       "——不会出现「等了很久却一个字都没回」。")
        tinfo.setWordWrap(True)
        tinfo.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(tinfo)
        lay = _p_engine[0]       # 回到「创作引擎」页，放引擎接入入口
        engrow = QHBoxLayout()
        engrow.addWidget(QLabel("创作引擎（图像/视频/漫剧出图）："))
        eng_btn = QPushButton(
            "已接入" if CRE.conf_ok(CRE.get_engine(cfg, "image")) else "未接入 · 配置…")
        eng_btn.setCursor(Qt.PointingHandCursor)
        eng_btn.clicked.connect(lambda: EngineDialog(self.cfg, "image", self).exec())
        engrow.addWidget(eng_btn, 1)
        lay.addLayout(engrow)
        # ── 第 5 页：🧍 小人（v0.30.9）── 它飞天时的观感，三个滑块
        # 值统一存在 pet_tuning（聊天页头像与桌面浮窗都读它），
        # 拖动即生效、点保存才落盘、取消会回滚（见 _pt_apply / reject）。
        _p_pet = _tab("🧍 小人")
        _pet_col = _p_pet[0]
        _pet_hint = QLabel(
            "形象 DIY：拖动「色相 / 饱和度 / 明度」给它换一身配色（色相 0% = 原色），"
            "「头饰 / 全息天线」勾选框控制头上的耳·角与天线。\n"
            "飞天观感：先变成飞机形态、机尾拖一条发光尾气、绕着屏幕飞一圈，"
            "落地前再变回人形。")
        _pet_hint.setStyleSheet("color:#64748b;font-size:11px")
        _pet_hint.setWordWrap(True)
        _pet_col.addWidget(_pet_hint)
        # 打开对话框时的原值 —— 取消时要回滚（拖动是实时生效的）
        try:
            self._pt_origin = dict(PT.all_values())
        except Exception:
            self._pt_origin = dict(PT.defaults())
        self._pt_sliders = {}
        self._pt_labels = {}
        self._pt_toggles = {}
        try:
            _pt_now = dict(PT.all_values())
        except Exception:
            _pt_now = dict(self._pt_origin)
        for _key, _spec in PT.KNOBS.items():
            _dflt, _lo, _hi, _short, _desc = _spec
            # v0.30.11 #12：0/1 开关项（头饰）渲染成勾选框，不是滑块。
            # 集合只认 pet_tuning.TOGGLES（单一来源，界面不另写一份键名）。
            if _key in getattr(PT, "TOGGLES", ()):
                _cb = QCheckBox(_short)
                _cb.setChecked(float(_pt_now.get(_key, _dflt)) >= 0.5)
                _cb.setStyleSheet("font-size:12px;color:#334155")
                _cb.setToolTip(_desc)
                _cb.toggled.connect(
                    lambda on, k=_key: self._pt_apply_toggle(k, on))
                _pet_col.addWidget(_cb)
                self._pt_toggles[_key] = _cb
                continue
            _h = QHBoxLayout()
            _nm = QLabel(_short)
            _nm.setStyleSheet("font-size:12px;color:#334155")
            _h.addWidget(_nm)
            _h.addStretch(1)
            _val = QLabel("")
            _val.setStyleSheet("font-size:12px;color:#1d4ed8;font-weight:600")
            _h.addWidget(_val)
            _pet_col.addLayout(_h)
            _sl = QSlider(Qt.Horizontal)
            _sl.setRange(int(round(_lo * 100)), int(round(_hi * 100)))
            _sl.setValue(int(round(float(_pt_now.get(_key, _dflt)) * 100)))
            _sl.setToolTip(_desc)
            _val.setText(self._pt_text(_key, _sl.value()))
            _sl.valueChanged.connect(
                lambda v, k=_key: self._pt_apply(k, v))
            _pet_col.addWidget(_sl)
            self._pt_sliders[_key] = _sl
            self._pt_labels[_key] = _val
        # v0.30.11 #12：一键恢复出厂外观（只回滚「小人」页，不动其它设置）
        _rst = QPushButton("恢复默认外观")
        _rst.setCursor(Qt.PointingHandCursor)
        _rst.clicked.connect(self._pt_reset_look)
        _pet_col.addWidget(_rst)
        _pet_col.addStretch(1)

        # ── 第 6 页：💳 支付（v0.30.12）
        # 小志要求：微信 / 支付宝的**商户号与 API 密钥在设置面板里填** ——
        # 不写死、不用手改 payment.json。三条铁律照旧：
        #   ① 出厂沙箱 + live=false（不误收真钱，要收钱必须显式打开）；
        #   ② 密钥只显示掩码，**没改动就连原值一起写回**（绝不把 `****` 存成密钥）；
        #   ③ 缺项 / 未配置就明确说缺什么，不静默降级成"看着能收钱"。
        _p_pay = _tab("💳 支付", cols=2)
        _pay_l, _pay_r = _p_pay[0], _p_pay[1]
        self._pay_ok = PAY is not None
        self._pay_orig = {}            # (group,key) -> 开对话框时的原值（明文，只在内存）
        self._pay_le = {}              # (group,key) -> QLineEdit
        self._pay_boxes = {}           # 渠道 -> 容器（切渠道时整组显隐）
        self._pay_pcfg = {}
        if self._pay_ok:
            try:
                self._pay_pcfg = PAY.load_config() or {}
            except Exception:
                logging.exception("读 payment.json 失败")
                self._pay_pcfg = {}
        _MASK = (PAY.mask if self._pay_ok else (lambda s, *a, **k: (s or "")))

        # v0.30.15：把这一页的**定位**摆在最上面。
        # 小志问过"有没有必要保留支付接口，没必要可以去掉" —— 结论是**保留**，
        # 但它是"给**你做出来的项目**接收单"，**不是替你付款**。这句必须一眼看到，
        # 否则很容易被理解成"PASM 要动我的钱"（那就该删了）。
        _pos = QLabel(
            "这一页是给**你做出来的项目**接收单用的（网站 / 小程序 / 应用接支付）。\n"
            "PASM 不会替你付款、也不会替你花钱 —— 那类操作在安全底线里一律不做。\n"
            "出厂是沙箱（本地模拟，不真实收款）；要真实收款必须自己开通商户，"
            "并在这里显式打开「允许真实收款」。")
        _pos.setWordWrap(True)
        _pos.setStyleSheet("color:#b45309;font-size:11px;background:#fffbeb;"
                           "border:1px solid #fde68a;border-radius:6px;padding:6px;")
        _pay_l.addWidget(_pos)

        _ph = QLabel("收款渠道")
        _ph.setStyleSheet("color:#64748b;font-size:11px")
        _pay_l.addWidget(_ph)
        self.pay_provider = QComboBox()
        self.pay_provider.addItem("沙箱（本地模拟，不真实收款）", "sandbox")
        self.pay_provider.addItem("微信支付（真实收款）", "wechat")
        self.pay_provider.addItem("支付宝（真实收款）", "alipay")
        _cur_pv = str(self._pay_pcfg.get("provider") or "sandbox").strip().lower()
        for _i in range(self.pay_provider.count()):
            if self.pay_provider.itemData(_i) == _cur_pv:
                self.pay_provider.setCurrentIndex(_i)
                break
        _h = QHBoxLayout()
        _lb0 = QLabel("当前渠道：")
        _lb0.setStyleSheet("font-size:12px;color:#334155")
        _h.addWidget(_lb0)
        _h.addWidget(self.pay_provider, 1)
        _pay_l.addLayout(_h)

        self.pay_live = QCheckBox("允许真实收款（live）")
        self.pay_live.setChecked(bool(self._pay_pcfg.get("live")))
        self.pay_live.setStyleSheet("color:#b45309;font-size:11px")
        self.pay_live.setToolTip(
            "出厂默认关闭 —— 关闭时任何渠道都不会产生真实收款。\n"
            "打开后下单会走真实渠道接口；这是**显式选择**，不做默认值。")
        _pay_l.addWidget(self.pay_live)
        _live_note = QLabel("出厂默认关闭：即使凭据填齐，也不会真的收钱；要用时才手动打开。")
        _live_note.setWordWrap(True)
        _live_note.setStyleSheet("color:#94a3b8;font-size:11px")
        _pay_l.addWidget(_live_note)

        self.pay_status = QLabel("")
        self.pay_status.setWordWrap(True)
        self.pay_status.setStyleSheet("color:#94a3b8;font-size:11px")
        _pay_l.addWidget(self.pay_status)

        _pbar = QHBoxLayout()
        _chk = QPushButton("🔎 检查就绪状态")
        _chk.setCursor(Qt.PointingHandCursor)
        _chk.clicked.connect(self._pay_check)
        _pbar.addWidget(_chk)
        _pbar.addStretch(1)
        _pay_l.addLayout(_pbar)

        _sec = QLabel("密钥只显示掩码（如 wx12****89）：不动它就按原值保存，要改就全选重填，"
                      "清空即清除该项。密钥不会进日志、不会回显。")
        _sec.setWordWrap(True)
        _sec.setStyleSheet("color:#94a3b8;font-size:11px")
        _pay_l.addWidget(_sec)
        _pay_l.addStretch(1)

        def _pay_group(key, title, note=""):
            """一个渠道一组字段；切渠道时整组 show / hide。"""
            _w = QWidget()
            _v = QVBoxLayout(_w)
            _v.setContentsMargins(0, 0, 0, 0)
            _v.setSpacing(7)
            _t = QLabel(title)
            _t.setStyleSheet("color:#1d4ed8;font-size:12px;font-weight:600")
            _v.addWidget(_t)
            if note:
                _n = QLabel(note)
                _n.setWordWrap(True)
                _n.setStyleSheet("color:#94a3b8;font-size:11px")
                _v.addWidget(_n)
            _pay_r.addWidget(_w)
            self._pay_boxes[key] = _w
            return _v

        def _pay_f(col, label, group, key, masked=False, pick="", ph=""):
            """一行字段。masked=True → 掩码回显，一编辑就转 Password 模式。"""
            _sub = (self._pay_pcfg.get(group) or {})
            _orig = str(_sub.get(key) or "")
            self._pay_orig[(group, key)] = _orig
            _hh = QHBoxLayout()
            _l2 = QLabel(label)
            _l2.setStyleSheet("font-size:12px;color:#334155")
            _hh.addWidget(_l2)
            _e = QLineEdit(_MASK(_orig) if (masked and _orig) else _orig)
            _e.setStyleSheet("font-size:12px")
            if ph:
                _e.setPlaceholderText(ph)
            if masked:
                _e.setToolTip("已保存的密钥只显示掩码；要修改请全选后重填（本框输入时会遮蔽）。")
                _e.textEdited.connect(
                    lambda _t, _w=_e: _w.setEchoMode(QLineEdit.Password))
            _hh.addWidget(_e, 1)
            if pick:
                _b = QPushButton("…")
                _b.setCursor(Qt.PointingHandCursor)
                _b.setMaximumWidth(32)
                _b.setToolTip(pick)
                _b.clicked.connect(lambda _c=False, _w=_e: self._pay_pick(_w, pick))
                _hh.addWidget(_b)
            col.addLayout(_hh)
            self._pay_le[(group, key)] = _e
            return _e

        _g = _pay_group("sandbox", "沙箱（本地模拟）",
                        "离线可跑、真 HMAC 签名、真状态机；密钥只用于本地签名，随便填也能跑通全链路。")
        _pay_f(_g, "商户号：", "sandbox", "mch_id")
        _pay_f(_g, "应用 ID：", "sandbox", "app_id")
        _pay_f(_g, "API 密钥：", "sandbox", "api_key", masked=True)
        _pay_f(_g, "回调地址：", "sandbox", "notify_url")

        _g = _pay_group("wechat", "微信支付 v3",
                        "商户平台 → 账户中心查商户号；APIv3 密钥在「API 安全」里设置；"
                        "证书序列号与商户私钥来自 apiclient_cert.pem / apiclient_key.pem。")
        _pay_f(_g, "商户号 mch_id：", "wechat", "mch_id")
        _pay_f(_g, "应用 app_id：", "wechat", "app_id")
        _pay_f(_g, "APIv3 密钥：", "wechat", "api_v3_key", masked=True)
        _pay_f(_g, "证书序列号：", "wechat", "cert_serial")
        _pay_f(_g, "商户私钥文件：", "wechat", "private_key_path",
               pick="选择商户 API 私钥 apiclient_key.pem")
        _pay_f(_g, "回调地址：", "wechat", "notify_url",
               ph="https://你的域名/pay/notify")

        _g = _pay_group("alipay", "支付宝",
                        "开放平台 → 应用信息查 AppID；签名用「应用私钥」，异步通知验签用「支付宝公钥」。")
        _pay_f(_g, "应用 AppID：", "alipay", "app_id")
        _pay_f(_g, "应用私钥文件：", "alipay", "private_key_path",
               pick="选择应用私钥文件（RSA2）")
        _pay_f(_g, "支付宝公钥文件：", "alipay", "alipay_public_key_path",
               pick="选择支付宝公钥文件")
        _pay_f(_g, "网关地址：", "alipay", "gateway")
        _pay_f(_g, "回调地址：", "alipay", "notify_url",
               ph="https://你的域名/pay/notify")
        _pay_r.addStretch(1)
        self.pay_provider.currentIndexChanged.connect(self._pay_sync_vis)
        self.pay_live.toggled.connect(self._pay_check)
        self._pay_sync_vis()

        # ── 第 7 页：🔗 接入（v0.30.14）────────────────────────────────────
        # 四个通道都是**双向**的：入站（这些平台能指挥这台电脑）+ 出站（结果推回去）。
        # 与支付页同一套打法：密钥掩码回显 + 未改动写回原值（别把真密钥覆盖成 ***）。
        # ── 第 7 页：场景导入 ────────────────────────────────────────
        # # PASM-SCENE-IMPORT v1
        # 读 <数据目录>/scenarios/*.json（由 pasm-customer-service 的
        # `pasm-cs studio` 产出），一键把「人格 + 知识源」载入本机。
        # ⚠️ 目录走 scenario.scenarios_dir()（env PASM_STUDIO_DIR 优先），
        #    必须与 Studio 其它数据同源 —— 否则隔离/第二实例态下
        #    Studio 会找不到自己生成的场景。
        _p_scene = _tab("📦 场景")
        _sc_lay = _p_scene[0]
        _sc_hint = QLabel("把外部生成的智能体场景（人格 + 知识库）一键载入本机。\n"
                          "场景文件由 pasm-customer-service 的 `pasm-cs studio` 产出。")
        _sc_hint.setWordWrap(True)
        _sc_hint.setStyleSheet("color:#64748b;font-size:11px")
        _sc_lay.addWidget(_sc_hint)
        _sc_row = QHBoxLayout()
        self.sc_list = QComboBox()
        self.sc_list.setMinimumWidth(240)
        self.sc_btn = QPushButton("导入此场景")
        self.sc_btn.clicked.connect(self._scene_import)
        _sc_reload = QPushButton("重新扫描")
        _sc_reload.clicked.connect(self._scene_refresh)
        _sc_row.addWidget(self.sc_list, 1)
        _sc_row.addWidget(self.sc_btn)
        _sc_row.addWidget(_sc_reload)
        _sc_lay.addLayout(_sc_row)
        self.sc_status = QLabel("")
        self.sc_status.setWordWrap(True)
        self.sc_status.setStyleSheet("color:#475569;font-size:11px")
        _sc_lay.addWidget(self.sc_status)
        self._sc_info = {"dir": "", "items": [], "errors": []}
        self._scene_refresh()          # 打开设置即扫描一次，省得用户先点一下
        _p_conn = _tab("🔗 接入", cols=2)
        _con_l, _con_r = _p_conn[0], _p_conn[1]
        self._con_le = {}              # (group,key) -> QLineEdit
        self._con_orig = {}            # (group,key) -> 原值（明文，只在内存）
        self._con_boxes = {}           # group -> 容器
        self._con_cfg = dict(self.cfg or {})
        _CMASK = _con_mask                                # 接入页统一用这个掩码

        _ch = QLabel("四个通道互不依赖：接哪个都行，没接的会被安静跳过。")
        _ch.setWordWrap(True)
        _ch.setStyleSheet("color:#64748b;font-size:11px")
        _con_l.addWidget(_ch)
        self.con_status = QLabel("")
        self.con_status.setWordWrap(True)
        self.con_status.setStyleSheet("color:#94a3b8;font-size:11px")
        _con_l.addWidget(self.con_status)
        _cb = QPushButton("🔎 检查就绪状态")
        _cb.setCursor(Qt.PointingHandCursor)
        _cb.clicked.connect(self._con_check)
        _con_l.addWidget(_cb)
        _tb = QPushButton("📤 试发一条（所有已配通道）")
        _tb.setCursor(Qt.PointingHandCursor)
        _tb.setToolTip("往所有配好的出站通道各发一条测试消息 —— 只有真的发出去才算通。")
        _tb.clicked.connect(self._con_test_send)
        _con_l.addWidget(_tb)
        _gu = QLabel("⚠️ 入站 = 远程指挥这台电脑的口子，所以：\n"
                     "· 服务只绑 127.0.0.1（跨网请自配内网穿透）\n"
                     "· 没配 Token/密钥时**拒绝启动**，校验不过一律 403\n"
                     "· 凭据只存本机 config.json，不进日志")
        _gu.setWordWrap(True)
        _gu.setStyleSheet("color:#b45309;font-size:11px")
        _con_l.addWidget(_gu)
        _con_l.addStretch(1)

        # ── 二级标签页：四个通道各占一页 ────────────────────────────────
        # 小志反馈：接入页把飞书/Discord/微信/通用四组字段平铺下来，面板被拉成一条长龙，
        # 想改哪一项都得往下滚半天。改成二级（通道选择器），一次只显示一个通道的字段。
        # 控件本身照旧全部构建（配置读写、掩码回写都不受影响），只是不显示而已。
        self._con_tabs = QTabWidget()
        self._con_tabs.setDocumentMode(True)
        self._con_tabs.setStyleSheet(
            "QTabWidget::pane{border:1px solid #e2e8f0;border-radius:8px;"
            "background:#ffffff;}"
            "QTabBar::tab{padding:4px 10px;margin-right:2px;font-size:12px;"
            "color:#64748b;background:#f1f5f9;border:1px solid #e2e8f0;"
            "border-bottom:none;border-top-left-radius:6px;border-top-right-radius:6px;}"
            "QTabBar::tab:selected{color:#1d4ed8;font-weight:bold;background:#ffffff;}"
            "QTabBar::tab:hover{background:#e0f2fe;}")
        _con_r.addWidget(self._con_tabs, 1)

        def _con_group(key, title, note=""):
            _w = QWidget()
            _v = QVBoxLayout(_w)
            _v.setContentsMargins(12, 10, 12, 10)
            _v.setSpacing(7)
            if note:
                _n = QLabel(note)
                _n.setWordWrap(True)
                _n.setStyleSheet("color:#94a3b8;font-size:11px")
                _v.addWidget(_n)
            # 分组标题交给二级标签页承载，页内不再重复一行 → 又省一行高度
            self._con_tabs.addTab(_w, title)
            self._con_boxes[key] = _w
            _v.addStretch(1)
            return _v

        def _con_f(col, label, group, key, masked=False, ph=""):
            _orig = str(self._con_cfg.get(key) or "")
            self._con_orig[(group, key)] = _orig
            _hh = QHBoxLayout()
            _l2 = QLabel(label)
            _l2.setStyleSheet("font-size:12px;color:#334155")
            _hh.addWidget(_l2)
            _e = QLineEdit(_CMASK(_orig) if (masked and _orig) else _orig)
            _e.setStyleSheet("font-size:12px")
            if ph:
                _e.setPlaceholderText(ph)
            if masked:
                _e.setToolTip("已保存的值只显示掩码；不改动就按原值保存（不会把真值写成 ***）。")
                _e.textEdited.connect(
                    lambda _t, _w=_e: _w.setEchoMode(QLineEdit.Password))
            _hh.addWidget(_e, 1)
            col.addLayout(_hh)
            self._con_le[(group, key)] = _e
            return _e

        def _con_combo(col, label, key, items, cur=""):
            _hh = QHBoxLayout()
            _l2 = QLabel(label)
            _l2.setStyleSheet("font-size:12px;color:#334155")
            _hh.addWidget(_l2)
            _c = QComboBox()
            for _label, _val in items:
                _c.addItem(_label, _val)
            for _i in range(_c.count()):
                if _c.itemData(_i) == cur:
                    _c.setCurrentIndex(_i)
                    break
            _hh.addWidget(_c, 1)
            col.addLayout(_hh)
            self._con_combo = getattr(self, "_con_combo", {})
            self._con_combo[key] = _c
            return _c

        _g = _con_group("feishu", "飞书 / Lark",
                        "开放平台建「自建应用」→ 开机器人能力 → 订阅 im.message.receive_v1。\n"
                        "接收方式选「长连接」时**不需要公网**（推荐）；选「事件回调」需要"
                        "公网 HTTPS（自备内网穿透）指向本机端口。")
        _con_f(_g, "App ID：", "feishu", "feishu_app_id", ph="cli_xxxxxxxxxx")
        _con_f(_g, "App Secret：", "feishu", "feishu_app_secret", masked=True)
        _con_combo(_g, "接收方式：", "feishu_mode",
                   [("关闭（只出站推送）", "off"),
                    ("长连接（推荐，免公网）", "longconn"),
                    ("事件回调（需公网）", "callback")],
                   cur=str(self._con_cfg.get("feishu_mode") or "off"))
        _con_f(_g, "站点域名：", "feishu", "feishu_domain", ph="feishu（默认）/ lark / 自建域名")
        _con_f(_g, "校验 Token：", "feishu", "feishu_verify_token",
               ph="后台「事件订阅」里的 Verification Token")
        _con_f(_g, "默认推送会话 chat_id：", "feishu", "feishu_default_chat",
               ph="oc_xxx（主动推送用；机器人不能私聊没联系过它的人）")
        _con_f(_g, "回调端口：", "feishu", "feishu_callback_port", ph="8797")

        _g = _con_group("discord", "Discord",
                        "开发者后台建 Application → Bot → Reset Token；读取消息**正文**"
                        "必须在 Privileged Gateway Intents 里勾上 MESSAGE_CONTENT。")
        _con_f(_g, "Bot Token：", "discord", "discord_bot_token", masked=True)
        _con_combo(_g, "接收方式：", "discord_mode",
                   [("关闭（只出站发消息）", "off"),
                    ("网关长连接（推荐）", "gateway")],
                   cur=str(self._con_cfg.get("discord_mode") or "off"))
        _con_f(_g, "默认频道 ID：", "discord", "discord_default_channel",
               ph="出站推送用；频道 ID 右键频道「复制 ID」")
        _con_f(_g, "只响应这些频道：", "discord", "discord_allow_channels",
               ph="留空=所有频道；多个用逗号分隔")

        _g = _con_group("wechat", "微信（官方路线）",
                        "① 企业微信群机器人：最省事的「推到微信」，填个 webhook 就行。\n"
                        "② 公众号：服务号/订阅号 → 基本配置拿 AppID/AppSecret，"
                        "服务器配置 URL 指向本机端口（需公网），**选明文模式**。\n"
                        "⚠️ 个人微信（ClawBot 那种非官方协议）**刻意不接**："
                        "违反微信条款、有封号风险，也不是能打包进桌面版的依赖。")
        _con_f(_g, "企业微信群机器人 Webhook：", "wechat", "wecom_bot_url",
               ph="https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx")
        _con_f(_g, "公众号 AppID：", "wechat", "wechat_appid")
        _con_f(_g, "公众号 AppSecret：", "wechat", "wechat_appsecret", masked=True)
        _con_f(_g, "公众号校验 Token：", "wechat", "wechat_token")
        _con_combo(_g, "公众号接收：", "wechat_mode",
                   [("关闭（只出站）", "off"), ("开启回调", "callback")],
                   cur=str(self._con_cfg.get("wechat_mode") or "off"))
        _con_f(_g, "公众号回调端口：", "wechat", "wechat_port", ph="8798")
        _con_f(_g, "默认接收人 openid：", "wechat", "wechat_default_openid",
               ph="主动推送用（客服消息有 48 小时窗口）")

        _g = _con_group("webhook", "通用 Webhook",
                        "入站：`POST http://127.0.0.1:<端口><路径>`，带 token 或 HMAC 签名。\n"
                        "出站：填一个地址即可，URL 会自动识别平台（企业微信/钉钉/飞书/"
                        "Server酱/Bark/通用）。")
        _con_f(_g, "出站地址：", "webhook", "bridge_webhook_url",
               ph="https://…（飞书/钉钉/企业微信/Server酱/Bark 都认）")
        _con_f(_g, "入站 Token：", "webhook", "webhook_in_token", masked=True,
               ph="够长就行；没配则入站拒绝启动")
        _con_combo(_g, "入站模式：", "webhook_in_mode",
                   [("关闭", "off"), ("开启（需 Token）", "on")],
                   cur=str(self._con_cfg.get("webhook_in_mode") or "off"))
        _con_combo(_g, "校验方式：", "webhook_in_hmac",
                   [("固定 Token（X-PASM-Token）", False),
                    ("HMAC-SHA256（X-PASM-Signature）", True)],
                   cur=bool(self._con_cfg.get("webhook_in_hmac")))
        _con_f(_g, "入站端口：", "webhook", "webhook_in_port", ph="8796")
        _con_f(_g, "入站路径：", "webhook", "webhook_in_path", ph="/inbox")
        # （v0.30.15：原来这里有一句 `_con_r.addStretch(1)` —— 那时四组是竖排、需要
        #   末尾留白。现在四组进了二级标签页（本身就带 stretch=1），再留白会让标签页
        #   只占半屏高。故删除。）

        # 底部固定操作区：不随分页滚动，任何一页都能直接保存
        _bar = QHBoxLayout()
        _hint = QLabel("改完点「保存」生效；试听声线在「🔊 声音」页，收款渠道与密钥在「💳 支付」页。")
        _hint.setStyleSheet("color:#94a3b8;font-size:11px")
        _bar.addWidget(_hint)
        _bar.addStretch(1)
        _cancel = QPushButton("取消")
        _cancel.setCursor(Qt.PointingHandCursor)
        _cancel.clicked.connect(self.reject)
        _bar.addWidget(_cancel)
        ok = QPushButton("保存")
        ok.setDefault(True)
        ok.setCursor(Qt.PointingHandCursor)
        ok.setMinimumWidth(96)
        ok.clicked.connect(self.save)
        _bar.addWidget(ok)
        _outer.addLayout(_bar)

    def _refresh(self):
        ms = list_ollama_models()
        cur = self.local.currentText()
        self.local.clear(); self.local.addItem("（自动选择）"); self.local.addItems(ms or ["（未检测到）"])
        if cur in ms:
            self.local.setCurrentText(cur)

    def _vp_status(self):
        """视频引擎本地就绪检查（不访问网络，纯文件/Key 检查，UI 不卡）。"""
        try:
            import videoeng as _VE
            cur = self.video_engine.itemData(self.video_engine.currentIndex()) or "none"
            self.cfg["video_provider"] = cur
            if cur == "none":
                self.ve_status.setText("当前为静态图运镜模式：成片不会“真动”，但零成本、无需任何配置。")
                self.ve_status.setStyleSheet("color:#94a3b8;font-size:11px")
                return
            if cur == "seedance":
                if not str(self.cfg.get("ark_key") or "").strip():
                    self.ve_status.setText("⚠ 还差一步：上方「方舟 API Key」填上才能出真视频"
                                           "（火山方舟 console.volcengine.com/ark 开通后创建 Key）。")
                    self.ve_status.setStyleSheet("color:#dc2626;font-size:11px")
                else:
                    self.ve_status.setText("✓ Key 已填：成片会调用即梦 Seedance 生成真动态片段。")
                    self.ve_status.setStyleSheet("color:#16a34a;font-size:11px")
                return
            # comfyui
            wf = str(self.cfg.get("comfy_workflow") or _VE._default_workflow_path())
            if not os.path.isfile(wf):
                self.ve_status.setText(
                    "⚠ workflow 文件不存在：\n" + wf +
                    "\n\n点上方「🧰 一键配置向导」：向导会检测你装的模型，"
                    "帮你取出官方示例并引导 3 步生成（不用自己搭工作流）。"
                    "没这个文件时成片只能按静态画面合成（不会动）。")
                self.ve_status.setStyleSheet("color:#dc2626;font-size:11px")
            else:
                self.ve_status.setText("✓ workflow 已就位。成片前请确认 ComfyUI 正在运行"
                                       "（" + str(self.cfg.get("comfy_url", "http://127.0.0.1:8188")) + "）。")
                self.ve_status.setStyleSheet("color:#16a34a;font-size:11px")
        except Exception:
            pass

    def _preview_voice(self):
        """按所选性别/性格/年龄段合成并朗读一句，立即验证声线切换。"""
        g = self.age_preview.currentData()
        if g is None:
            g = 3
        persona = self.persona.currentText()
        gv = dict(_GENDER_LABELS).get(self.gender.currentText(), "none")
        eng = self.voice_engine.itemData(self.voice_engine.currentIndex()) or "auto"
        name = self.name.text().strip() or self.cfg.get("name", "小U")
        lines = {0: f"咿呀～我是小宝贝{name}，要抱抱！",
                 1: f"嘿嘿，我是{name}，我今天又学会好多新知识啦！",
                 2: f"唔…我是少年{name}，这个问题我想一想再回答你。",
                 3: f"你好呀，我是{name}！很高兴认识你，我们聊聊天吧。",
                 4: f"我是{name}。有事尽管交给我，我来安排。"}
        tts_mod.speak_text(lines.get(g, lines[4]), growth=g, gender=gv,
                           persona=persona, emote="happy", engine=eng,
                           on_result=lambda m: self.voice_result.emit(m),
                           log=lambda m: logging.info("preview tts %s", m))

    def _on_audio_fix_toggle(self, on: bool):
        """设置页「允许自动调整系统音量」开关：实时生效并落盘。"""
        self.cfg["audio_autofix"] = bool(on)
        _save_json(CONFIG, self.cfg)
        audio_mod.ALLOW_TWEAK = bool(on)
        self.preview_lbl.setStyleSheet("color:#64748b;font-size:11px")
        self.preview_lbl.setText(
            "已" + ("允许" if on else "禁止") + "自动调整系统音量。"
            + ("朗读前发现静音/音量过低会帮你恢复。" if on else
               "无论朗读还是点小人，都不会再碰你电脑的音量；"
               "没声音时我会只做诊断提示，由你自己调。"))

    def _play_sys_sound(self):
        """播放系统提示音：先诊断默认端点，再把结果（设备/音量/静音）展示出来。"""
        ok, diag = tts_mod.system_sound_test()
        if ok:
            self.preview_lbl.setStyleSheet("color:#16a34a;font-size:11px")
            txt = "✓ 系统提示音已播放。\n诊断：" + diag
            if "已自动" in diag:
                txt += "\n\n刚才播放前系统输出被静音或音量过低，已自动恢复——请再点一次系统音确认能听到。"
            elif "静音" in diag or "音量" in diag:
                txt += ("\n\n（v0.18.0 起我不会再自动改你电脑的音量。若听不到，"
                        "请按上面诊断把系统音量/输出设备调好；或勾选上方"
                        "「允许自动调整系统音量」让我代劳。）")
            else:
                txt += "\n\n如果还是没声音：请在系统 设置→声音→输出设备 确认选中的不是无声的显示器/数字输出，并检查右下角音量合成器里 PASM Studio 是否被静音。"
            self.preview_lbl.setText(txt)
        else:
            self.preview_lbl.setStyleSheet("color:#dc2626;font-size:11px")
            self.preview_lbl.setText(
                "✗ 系统提示音播放失败（本机音频 API 拒绝了播放）。\n诊断：" + diag
                + "\n请打开右下角音量→音量合成器，确认系统没有总静音、PASM Studio 未被单独静音；"
                  "再检查系统 设置→声音→输出设备。仍不行请把 voice.log 发我。")

    def _on_voice_result(self, msg):
        if msg == "ok":
            self.preview_lbl.setStyleSheet("color:#16a34a;font-size:11px")
            self.preview_lbl.setText("✓ 朗读已触发。若听不到声音，点上方「🔔 系统音」区分是语音问题还是系统声音问题。")
            return
        from qt_compat import QtWidgets
        QtWidgets.QMessageBox.warning(
            self, "语音试听",
            "没听到声音：\n"
            + ("在线拟真语音(edge-tts)连不上，已回落系统语音，但系统语音也失败了。\n"
               "请检查：① 音量/扬声器 ② 系统是否装有中文语音"
               "（设置→时间和语言→语言→中文→语音，安装「Microsoft Huihui」）。"
               if msg.startswith("系统语音") else
               "语音通道不可用（系统组件缺失）。可先在聊天里发 /voice 开 再听一次。"))

    def _wake_label_sync(self, *a):
        """名字变了，唤醒开关上的提示立刻跟着改 —— 让"唤醒词跟随名称"可见。"""
        try:
            nm = self.name.text().strip() or "小U"
            self.wake.setText("语音唤醒（说「%s」叫醒我）" % nm)
        except Exception:
            pass

    def _perm_hint_sync(self):
        """档位说明随选择实时更新 —— 让用户在下拉时就看清代价，而不是保存后才发现。"""
        try:
            lv = self.perm.itemData(self.perm.currentIndex()) or "safe"
            self.perm_hint.setText(PERM.DESCRIPTIONS.get(PERM.normalize_level(lv), ""))
            if PERM.is_high_risk_level(lv):
                self.perm_hint.setStyleSheet(
                    "color:#c2410c;font-size:11px;padding-left:2px;font-weight:600;")
            else:
                self.perm_hint.setStyleSheet(
                    "color:#94a3b8;font-size:11px;padding-left:2px;")
        except Exception:
            pass

    @staticmethod
    def _pt_text(key, val):
        """滑块值（百分数）→ 人看得懂的文本。高度**按屏高百分比说**，别只说数字。"""
        x = float(val) / 100.0
        if key == "pet_fly_height":
            return "屏高的 %.0f%%" % (x * 100)
        return "%.0f%%" % (x * 100)

    def _pt_collect(self, cfg):
        """把「小人」页所有控件的值写进 cfg（滑块 + 勾选框）。

        单独成方法是为了**可测**：`save()` 里还夹着开机自启（写注册表）等副作用，
        测试不该为了验这一段去碰系统 —— 直接调这个方法就行。
        """
        for k, sl in getattr(self, "_pt_sliders", {}).items():
            cfg[k] = float(sl.value()) / 100.0
        for k, cb in getattr(self, "_pt_toggles", {}).items():
            cfg[k] = 1.0 if cb.isChecked() else 0.0
        return cfg

    def _pt_apply(self, key, val):
        """拖动即生效：写进 `pet_tuning` 的内存状态（保存才落盘）。"""
        try:
            self._pt_labels[key].setText(self._pt_text(key, val))
        except Exception:
            pass
        try:
            cur = dict(PT.all_values())
            cur[key] = float(val) / 100.0
            PT.load_from_cfg(cur)
        except Exception:
            logging.exception("应用小人设置失败")

    def _pt_apply_toggle(self, key, on):
        """头饰勾选框：勾 / 取消**立即生效**（写 pet_tuning 内存，保存才落盘）。"""
        try:
            cur = dict(PT.all_values())
            cur[key] = 1.0 if on else 0.0
            PT.load_from_cfg(cur)
        except Exception:
            logging.exception("应用小人头饰设置失败")

    def _pt_reset_look(self):
        """「恢复默认外观」：滑块 / 勾选框回出厂值，并立即生效。

        回填控件会各自触发 valueChanged / toggled → 自动走 _pt_apply，
        所以这里不需要（也不该）再手动逐项写一遍。
        """
        try:
            d = PT.defaults()
            for k, sl in getattr(self, "_pt_sliders", {}).items():
                sl.setValue(int(round(float(d.get(k, 0.0)) * 100)))
            for k, cb in getattr(self, "_pt_toggles", {}).items():
                cb.setChecked(float(d.get(k, 1.0)) >= 0.5)
        except Exception:
            logging.exception("恢复默认外观失败")

    # ---------- v0.30.12：支付渠道配置 ----------
    def _pay_pick(self, edit, tip=""):
        """选证书 / 私钥文件：只把**路径**填进输入框，不读文件内容。"""
        try:
            cur = edit.text().strip()
            _start = os.path.dirname(cur) if os.path.isabs(cur) else ""
            f, _ = QFileDialog.getOpenFileName(
                self, tip or "选择文件", _start,
                "密钥 / 证书 (*.pem *.key *.txt *.crt *.cer);;所有文件 (*)")
            if f:
                edit.setEchoMode(QLineEdit.Normal)
                edit.setText(os.path.normpath(f))
        except Exception:
            logging.exception("选择密钥文件失败")

    def _pay_sync_vis(self, *_a):
        """按当前渠道显隐字段组，并顺带刷新就绪状态。"""
        try:
            cur = self.pay_provider.currentData() or "sandbox"
        except Exception:
            cur = "sandbox"
        for k, w in getattr(self, "_pay_boxes", {}).items():
            try:
                w.setVisible(k == cur)
            except Exception:
                pass
        self._pay_check()

    def _pay_collect(self):
        """把界面上的支付配置收成一份 dict。

        **掩码陷阱**（这是本页最容易写错的地方）：密钥框里显示的是
        `mask(原值)`。用户没动它时文本恰好等于掩码 —— 此时必须写回**原值**，
        否则真密钥会被 `****` 覆盖，且界面上"看着还是配好的"。
        """
        base = self._pay_pcfg or {}
        out = {"provider": "sandbox", "live": False,
               "currency": base.get("currency") or "CNY"}
        try:
            out["provider"] = self.pay_provider.currentData() or "sandbox"
            out["live"] = bool(self.pay_live.isChecked())
        except Exception:
            pass
        for g in ("sandbox", "wechat", "alipay"):
            out[g] = dict(base.get(g) or {})
        for (g, k), le in getattr(self, "_pay_le", {}).items():
            try:
                txt = le.text().strip()
            except Exception:
                continue
            orig = str(self._pay_orig.get((g, k)) or "")
            if orig and txt == (PAY.mask(orig) if self._pay_ok else orig):
                txt = orig                      # ← 没改动：写回原值，不是写掩码
            out.setdefault(g, {})[k] = txt
        return out

    def _con_collect(self):
        """把「🔗 接入」页收成一份扁平配置。

        与支付页同款**掩码陷阱**：密钥框里显示的是 `mask(原值)`；用户没动它时
        文本恰好等于掩码 —— 必须写回原值，否则真凭据被 `****` 覆盖，
        而界面"看着还是配好的"（这类错最难查）。
        """
        out = {}
        for (g, k), le in getattr(self, "_con_le", {}).items():
            try:
                txt = le.text().strip()
            except Exception:                                # noqa: BLE001
                continue
            orig = str(self._con_orig.get((g, k)) or "")
            if orig and txt == _con_mask(orig):
                txt = orig                       # ← 没改动：写回原值
            out[k] = txt
        for k, c in (getattr(self, "_con_combo", {}) or {}).items():
            try:
                out[k] = c.currentData()
            except Exception:                                # noqa: BLE001
                pass
        return out

    def _con_apply_to_cfg(self, cfg: dict) -> int:
        """保存时把接入配置写进 cfg（含 int / bool 的类型归一）。返回写入项数。"""
        vals = self._con_collect()
        n = 0
        ints = ("feishu_callback_port", "wechat_port", "webhook_in_port")
        for k, v in vals.items():
            if k in ints:
                try:
                    v = int(v or 0)
                except Exception:                            # noqa: BLE001
                    continue
            if k == "webhook_in_hmac":
                v = bool(v)
            cfg[k] = v
            n += 1
        return n

    def _con_check(self, *_a):
        """就绪状态：每个通道"能不能用 / 缺什么"一次说清，不静默降级。"""
        try:
            import connector_hub as HUB
        except Exception as ex:                              # noqa: BLE001
            self.con_status.setText("接入模块不可用：%s" % ex)
            return
        try:
            cfg = dict(self.cfg or {})
            cfg.update(self._con_collect())
            hub = HUB.ConnectorHub(cfg=cfg)
            rows = hub.statuses()
            out = []
            for r in rows:
                if r["configured"]:
                    out.append("✅ %s：已配置（%s）" % (r["label"], r["state"]))
                else:
                    out.append("⚪ %s：未配置" % r["label"])
            self.con_status.setText("就绪状态：\n" + "\n".join(out) +
                                    "\n（保存后通道会热重启；每页都能点「保存」生效）")
        except Exception as ex:                              # noqa: BLE001
            self.con_status.setText("检查失败：%s" % ex)

    def _con_test_send(self, *_a):
        """往所有已配的出站通道各发一条 —— **真发出去**才算通。"""
        try:
            import connector_hub as HUB
            cfg = dict(self.cfg or {})
            cfg.update(self._con_collect())
            _pw = self.parent()
            hub = getattr(_pw, "_hub", None) if _pw is not None else None
            if hub is None:
                hub = HUB.ConnectorHub(cfg=cfg)
            else:
                # 用**界面上刚改的**值试发（不必先点保存），且复用运行中的那个中枢
                try:
                    hub.cfg = cfg
                except Exception:                            # noqa: BLE001
                    pass
            sent = hub.push("PASM 接入连通性测试（可以忽略这条消息）", title="PASM 测试")
            if sent:
                self.con_status.setText("📤 已发出：%s ✅\n（对方没收到的话，"
                                        "检查凭据/权限，或点下面的「🔎 检查就绪状态」）"
                                        % "、".join(sent))
            else:
                self.con_status.setText(
                    "📤 没有可用的出站通道。至少配一条：\n"
                    "· 飞书：App ID + App Secret + 默认推送会话 chat_id\n"
                    "· Discord：Bot Token + 默认频道 ID\n"
                    "· 微信：企业微信群机器人 webhook（最省事）\n"
                    "· Webhook：出站地址")
        except Exception as ex:                              # noqa: BLE001
            self.con_status.setText("试发失败：%s" % ex)

    def _pay_check(self, *_a):
        """就绪状态：缺什么就说什么，不静默降级。"""
        if not getattr(self, "_pay_ok", False):
            self.pay_status.setText("⚠ 支付模块不可用（desktop/payment.py 未加载）")
            self.pay_status.setStyleSheet("color:#dc2626;font-size:11px")
            return
        try:
            pc = self._pay_collect()
            p = PAY.get_provider(str(pc.get("provider") or "sandbox"), pc)
            ok, why = p.ready()
            st = p.status() or {}
        except Exception as e:                  # noqa: BLE001
            self.pay_status.setText("⚠ 检查失败：%s" % e)
            self.pay_status.setStyleSheet("color:#dc2626;font-size:11px")
            return
        _nm = {"sandbox": "沙箱", "wechat": "微信支付",
               "alipay": "支付宝"}.get(p.name, p.name)
        if ok:
            self.pay_status.setText(
                "✓ %s 已就绪（%s，币种 %s）"
                % (_nm, "真实收款" if st.get("live") else "不真实收款",
                   st.get("currency") or "CNY"))
            self.pay_status.setStyleSheet("color:#16a34a;font-size:11px")
        else:
            self.pay_status.setText("⚠ %s 未就绪：%s" % (_nm, why))
            self.pay_status.setStyleSheet("color:#dc2626;font-size:11px")

    def _scene_refresh(self):
        """扫描场景目录并刷新下拉列表。失败即降级为一行提示，绝不冒异常。"""
        try:
            import scenario as SC
            info = SC.list_scenarios()
        except Exception as ex:                                  # noqa: BLE001
            try:
                self.sc_list.clear()
                self.sc_status.setText("场景模块不可用：%s" % ex)
            except Exception:
                pass
            return
        self._sc_info = info
        self.sc_list.clear()
        for _it in info["items"]:
            self.sc_list.addItem("%s（%s，%d 个知识源）"
                                 % (_it["display_name"], _it["agent_id"],
                                    _it["source_count"]), _it["path"])
        if info["items"]:
            _msg = "找到 %d 个场景｜目录：%s" % (len(info["items"]), info["dir"])
            _color = "#475569"
        else:
            _msg = ("暂无场景。在 pasm-customer-service 目录执行 `pasm-cs studio` "
                    "生成后点「重新扫描」。\n目录：%s" % info["dir"])
            _color = "#94a3b8"
        if info["errors"]:
            _msg += "\n⚠ " + "；".join(info["errors"][:3])
            _color = "#d97706"
        self.sc_status.setText(_msg)
        self.sc_status.setStyleSheet("color:%s;font-size:11px" % _color)

    def _scene_import(self):
        """导入选中场景：名字填进「基本」页，知识源立即灌进资料库。

        只做**确实有效**的两件事 —— 名字（QLineEdit，保存后生效）与知识
        （真落盘进资料库）。角色/语气等描述性字段不做假动作：本机人格以
        「🎭 基本」页为准，界面上如实说明。
        """
        _path = self.sc_list.currentData()
        if not _path:
            return
        try:
            import scenario as SC
            _spec = SC.load_scenario(_path)
        except Exception as ex:                                  # noqa: BLE001
            self.sc_status.setText("⚠ 读取失败：%s" % ex)
            self.sc_status.setStyleSheet("color:#dc2626;font-size:11px")
            return
        _patch = SC.persona_patch(_spec)
        if _patch.get("name"):
            self.name.setText(str(_patch["name"]))
        try:
            import knowledge as KB
            _res = SC.ingest(_spec, os.path.dirname(_path), KB.record)
        except Exception as ex:                                  # noqa: BLE001
            self.sc_status.setText("⚠ 导入知识失败：%s" % ex)
            self.sc_status.setStyleSheet("color:#dc2626;font-size:11px")
            return
        _ok = bool(_res.get("ok"))
        _lines = ["%s 已导入 %d 条知识" % ("✓" if _ok else "⚠", _res.get("imported", 0))]
        for _s in (_res.get("sources") or []):
            _lines.append("· %s：%d 条" % (_s.get("name"), _s.get("count")))
        if _res.get("errors"):
            _lines.append("· 未导入：" + "；".join(_res["errors"][:3]))
        if _patch.get("name"):
            _lines.append("· 名字「%s」已填入「🎭 基本」页，点「保存」后生效" % _patch["name"])
        _lines.append("· 角色/语气等描述见场景文件；本机人格以「🎭 基本」页为准。")
        self.sc_status.setText("\n".join(_lines))
        self.sc_status.setStyleSheet(
            "color:%s;font-size:11px" % ("#16a34a" if _ok else "#dc2626"))

    def reject(self):
        """取消 → 把「小人」三项**回滚**成打开对话框时的值。

        为什么需要：滑块是「拖动即生效」的（小志要求改完立刻看到），
        所以取消时如果不回滚，界面上没保存的观感改动就会留在内存里 ——
        下次重启又变回去，属于"看着生效了其实没生效"的错。
        """
        try:
            PT.load_from_cfg(getattr(self, "_pt_origin", None) or PT.defaults())
        except Exception:
            pass
        super().reject()

    def save(self):
        self.cfg.update(name=self.name.text().strip() or "小U",
                        persona=self.persona.currentText(),
                        api_key=self.key.text().strip(),
                        base_url=self.base.text().strip(),
                        model=self.model.text().strip())
        # v0.29 权限档位：升到「完全访问」必须显式警告 + 二次确认
        try:
            from qt_compat import QtWidgets
            _new_pl = PERM.normalize_level(
                self.perm.itemData(self.perm.currentIndex()) or "safe")
            _old_pl = PERM.normalize_level(self.cfg.get("perm_level"))
            if PERM.is_high_risk_level(_new_pl) and not PERM.is_high_risk_level(_old_pl):
                _r = QtWidgets.QMessageBox.warning(
                    self, "开启「完全访问」？",
                    "完全访问后它**不再逐次询问**：\n"
                    "· 删除文件、执行命令、改系统设置都会直接执行\n"
                    "· 不可逆的操作没有二次确认\n\n"
                    "建议只在你盯着它干活时开启。确定要开吗？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if _r != QtWidgets.QMessageBox.Yes:
                    # 用户反悔：下拉拨回原档，配置保持不动
                    for _i in range(self.perm.count()):
                        if self.perm.itemData(_i) == _old_pl:
                            self.perm.setCurrentIndex(_i)
                            break
                    _new_pl = _old_pl
            self.cfg["perm_level"] = _new_pl
        except Exception:
            self.cfg.setdefault("perm_level", "safe")
        g = dict(_GENDER_LABELS).get(self.gender.currentText(), "none")
        self.cfg["gender"] = g
        self.cfg["voice_engine"] = self.voice_engine.itemData(
            self.voice_engine.currentIndex()) or "auto"
        self.cfg["video_provider"] = self.video_engine.itemData(
            self.video_engine.currentIndex()) or "none"
        self.cfg["ark_key"] = self.ark_key.text().strip()
        self.cfg["ark_model"] = self.ark_model.currentText().strip()
        self.cfg["ark_audio"] = bool(self.ark_audio.isChecked())
        self.cfg["comfy_url"] = self.comfy_url.text().strip()
        self.cfg["comfy_manage"] = bool(self.comfy_manage.isChecked())
        self.cfg["local_think_mode"] = self.think_mode.currentData() or "auto"
        self.cfg["local_think"] = (self.cfg["local_think_mode"] == "on")
        try:
            GW.set_local_think(self.cfg["local_think_mode"])
        except Exception:
            pass
        try:
            _st = GROWTH.pet_state()
            _st["gender"] = g
            GROWTH.save_state(_st)
        except Exception:
            pass
        if self.local.currentIndex() > 0:
            self.cfg["local_model"] = self.local.currentText()
            self.cfg["model_locked"] = True      # 用户显式选择 → 锁定该模型
        else:
            self.cfg.pop("model_locked", None)   # 自动 → 始终用最小已装模型
        # v0.29：开机自启（失败只记日志、不阻断保存 —— 它不影响其它设置项可用）
        try:
            _ok, _msg = AUTOSTART.set_enabled(bool(self.autostart.isChecked()))
            if not _ok:
                logging.warning("autostart 设置失败: %s", _msg)
        except Exception as _ex:
            logging.warning("autostart 设置异常: %s", _ex)
        self.cfg["wake_enabled"] = bool(self.wake.isChecked())
        # v0.30.9：「小人」三项观感（拖动时已在内存生效，这里落盘）
        try:
            self._pt_collect(self.cfg)
            PT.load_from_cfg(self.cfg)
        except Exception:
            logging.exception("保存小人设置失败（其它设置不受影响）")
        # v0.30.12：支付渠道配置落 payment.json。
        # 独立文件 = 单一来源（不在 CONFIG 里再存一份副本，免得两处不一致）。
        try:
            if getattr(self, "_pay_ok", False):
                _pc = self._pay_collect()
                _was_live = bool((self._pay_pcfg or {}).get("live"))
                # 关 → 开：真实收款是**显式选择**，必须二次确认
                if _pc.get("live") and not _was_live:
                    _r = QMessageBox.warning(
                        self, "打开真实收款？",
                        "打开后该渠道下的订单会**真实发起收款**（用户会真的被扣钱）。\n\n"
                        "建议先确认：商户号 / 密钥 / 证书都填对，回调地址可被公网访问。\n"
                        "确定要打开吗？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if _r != QMessageBox.Yes:
                        _pc["live"] = False
                        self.pay_live.setChecked(False)
                PAY.save_config(_pc)
                # 回读校验：写盘后重读一遍，不一致就如实说，不假装保存成功
                _back = PAY.load_config()
                _bad = [k for k in ("provider", "live")
                        if _back.get(k) != _pc.get(k)]
                for _g in ("sandbox", "wechat", "alipay"):
                    for _k, _v in (_pc.get(_g) or {}).items():
                        if str((_back.get(_g) or {}).get(_k) or "") != str(_v or ""):
                            _bad.append("%s.%s" % (_g, _k))
                self._pay_pcfg = _back
                if _bad:
                    logging.warning("支付配置回读不一致: %s", _bad)
                    QMessageBox.warning(
                        self, "保存校验",
                        "这几项写盘后回读不一致，请再存一次：\n%s" % "、".join(_bad))
        except Exception:
            logging.exception("保存支付设置失败（其它设置不受影响）")
        # ── v0.30.14：接入配置写进 cfg + 通道热重启（改完凭据不用重开应用）──
        try:
            _n = self._con_apply_to_cfg(self.cfg)
            if _n:
                logging.info("接入配置已写入 %d 项", _n)
        except Exception:
            logging.exception("保存接入设置失败（其它设置不受影响）")
        _save_json(CONFIG, self.cfg)
        # 通知主窗口：名字/唤醒开关可能变了 → 同步监听（含唤醒词热更新）
        # v0.30.14：接入凭据改完 → **热重启**对应通道（否则用户要重开应用才生效，
        # 而界面文案说的是"保存即生效" —— 说了就得做到）。
        try:
            _pw = self.parent()
            if _pw is not None and hasattr(_pw, "sync_wakeword"):
                _pw.sync_wakeword()
            _hub = getattr(_pw, "_hub", None) if _pw is not None else None
            if _hub is not None:
                for _p in ("feishu", "discord", "wechat", "webhook"):
                    try:
                        _hub.restart(_p)
                    except Exception:                        # noqa: BLE001
                        logging.exception("热重启 %s 通道失败", _p)
        except Exception:
            pass
        self.accept()


class ComfyWizard(QDialog):
    """v0.22.2 ComfyUI 图生视频「自动配置向导」。

    普通用户不用自己搭工作流：① 检测 ComfyUI 与已装模型 → ② 取出内置官方示例
    （Wan2.2 5B / LTX-Video，拖进 ComfyUI 即可跑）→ ③ 把导出的 API workflow
    导入 / 自检。全程按钮化，看不懂的步骤有可点入口。
    """

    def __init__(self, cfg: dict, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("🧰 ComfyUI 图生视频 · 自动配置向导")
        self.resize(720, 560)
        lay = QVBoxLayout(self)
        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        self.log.setStyleSheet(
            "QTextBrowser{border:1px solid #e2e8f0;border-radius:8px;"
            "background:#ffffff;padding:10px;font-size:13px;color:#0f172a;}"
            "QTextBrowser:focus{border-color:#5b8def;}")
        lay.addWidget(self.log, 1)
        btns = QHBoxLayout()
        for label, slot in (("① 检测 ComfyUI 与模型", self._do_detect),
                            ("② 取出官方示例(给 ComfyUI 用)", self._do_sample),
                            ("③ 导入 workflow 文件", self._do_import),
                            ("④ 自检当前配置", self._do_verify)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns.addWidget(b)
        btns.addStretch(1)
        close_b = QPushButton("关闭")
        close_b.clicked.connect(self.accept)
        btns.addWidget(close_b)
        lay.addLayout(btns)
        # v0.22.4：ComfyUI 本机托管（查找/启动/停止）
        btns2 = QHBoxLayout()
        for label, slot in (("▶ 启动 ComfyUI（自动找）", self._do_start),
                            ("🔎 手动定位 main.py…", self._do_locate),
                            ("⏹ 停止 ComfyUI", self._do_stop)):
            b = QPushButton(label)
            b.clicked.connect(slot)
            btns2.addWidget(b)
        btns2.addStretch(1)
        lay.addLayout(btns2)
        self._paint("欢迎！跟着①②③④走一遍，就不用自己搭工作流了：\n\n"
                    "① 先点「① 检测」确认 ComfyUI 正在运行、并看它装没装图生视频模型；\n"
                    "② 点「② 取出官方示例」把官方 Wan2.2 / LTX 图生视频工作流存到电脑，\n"
                    "　 在 ComfyUI 网页里把它拖进去 → 点「Queue 运行」试出片；\n"
                    "③ 确认能出片后：ComfyUI 菜单 Workflow → Save (API Format)，\n"
                    "　 把下载的 json 用「③ 导入」选进来（自动校验并放到引擎要的位置）；\n"
                    "④ 点「④ 自检」确认一切就绪，就可以回来发「火柴人战斗」了！\n\n"
                    "还没装 ComfyUI？点「▶ 启动」会自动找；找不到它会提示去官网下便携版。\n"
                    "装好后勾选设置里的「随 PASM 启动/退出 ComfyUI」，以后就不用手动开了。\n\n"
                    "目标文件：%APPDATA%\\PASMStudio\\comfy_i2v_workflow.json")

    def _paint(self, txt):
        self.log.append(html.escape(txt) + "<br>")
        self.log.verticalScrollBar().setValue(self.log.verticalScrollBar().maximum())

    def _url(self):
        return str(self.cfg.get("comfy_url") or "http://127.0.0.1:8188").rstrip("/")

    def _do_detect(self):
        self._paint("…正在检测 ComfyUI（%s），请稍候" % self._url())
        QApplication.processEvents()
        try:
            probe = VE.comfy_probe(self.cfg)
            if not probe.get("ok"):
                self._paint("✗ " + str(probe.get("error") or "连接失败"))
                self._paint("请先启动 ComfyUI（网页能打开 %s 即可），再点「①」重新检测。"
                            % self._url())
                return
            ver = probe.get("version") or "未知"
            nodes = probe.get("nodes") or []
            self._paint("✓ ComfyUI 已连接，版本 " + str(ver))
            if nodes:
                self._paint("已装图生视频相关节点：%s" % "、".join(nodes))
            else:
                self._paint("注意：没检测到图生视频节点（WanImageToVideo / "
                            "Wan22ImageToVideoLatent / LTXVImgToVideo…）。"
                            "请把 ComfyUI 升级到最新版（自带 Wan2.2 原生支持），"
                            "或安装 Wan/LTX 扩展后再试。")
            try:
                models = VE.comfy_model_files(self.cfg, 20)
            except Exception as ex:
                models = []
                self._paint("模型目录读取失败：" + str(ex))
            if models:
                self._paint("检测到本机 i2v 相关模型文件（供确认）：")
                for m in models:
                    self._paint("  · " + m)
            else:
                self._paint("还没检测到 Wan/LTX 图生视频模型文件。"
                            "常见下载（HF 官方仓库，拷进 ComfyUI 对应 models 目录）：\n"
                            "  · Wan2.2 5B TI2V → Wan-AI/Wan2.2-TI2V-5B\n"
                            "  · LTX-Video → Lightricks/LTX-Video")
        except Exception as ex:
            self._paint("检测出错：" + str(ex))

    def _do_sample(self):
        try:
            names = VE.list_samples()
        except Exception:
            names = []
        if not names:
            self._paint("内置示例缺失（可能打包不完整）")
            return
        self._paint("本向导内置官方示例：" + "、".join(names))
        kind = "wan22" if any("wan22" in n for n in names) else "ltx"
        fn = VE._SAMPLE_NAMES.get(kind, names[0])
        from qt_compat import QtWidgets
        import os
        home = os.path.expanduser("~")
        dst, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "把官方示例存到哪里（给 ComfyUI 用）",
            os.path.join(home, "Desktop", fn), "JSON 工作流 (*.json)")
        if not dst:
            return
        ok, msg = VE.save_sample_to(self.cfg, kind, dst)
        if not ok:
            self._paint("✗ " + msg)
            return
        self._paint("✓ 官方示例已存到：\n" + msg +
                    "\n\n接下来在浏览器打开的 ComfyUI 页面里：\n"
                    "  1) 把这个 json 文件【拖进】ComfyUI 窗口（自动加载整套工作流）；\n"
                    "  2) 给 LoadImage 选一张你的图（或直接 Queue 试跑官方示例图）；\n"
                    "  3) 点右侧「Queue」运行，确认能生成视频；\n"
                    "  4) 点顶部菜单 Workflow → Save (API Format)，会下载一个 json；\n"
                    "  5) 回到本向导点「③ 导入 workflow 文件」，选那个下载的文件即可。")

    def _do_start(self):
        """v0.22.4：自动查找并启动本机 ComfyUI（含等待就绪）。"""
        self._paint("…查找并启动 ComfyUI，请稍候（首次启动较慢）")
        QApplication.processEvents()
        try:
            ok, msg = VE.start_comfy(self.cfg, wait=45)
            self._paint(("✓ " if ok else "✗ ") + msg)
            if ok:
                self._paint("现在点「① 检测」确认节点与模型是否齐全。")
        except Exception as ex:
            self._paint("启动出错：" + str(ex))

    def _do_locate(self):
        from qt_compat import QtWidgets
        main, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 ComfyUI 的 main.py（在 ComfyUI 安装目录里）",
            "", "Python (main.py)")
        if main:
            self.cfg["comfy_main"] = main
            self._paint("已记住 ComfyUI：\n" + main +
                        "\n点「▶ 启动 ComfyUI」即可拉起它。")

    def _do_stop(self):
        try:
            self._paint(VE.stop_comfy(self.cfg))
        except Exception as ex:
            self._paint("停止出错：" + str(ex))

    def _do_import(self):
        from qt_compat import QtWidgets
        src, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择 ComfyUI 导出的 API workflow json", "", "JSON (*.json)")
        if not src:
            return
        ok, msg = VE.import_workflow(src, self.cfg)
        if not ok:
            self._paint("✗ " + msg)
            return
        self._paint("✓ 已导入并自检通过：\n" + msg +
                    "\n\n现在回聊天重发一次「火柴人战斗」等需求，就会出真视频了。")

    def _do_verify(self):
        import os
        wf = str(self.cfg.get("comfy_workflow") or
                 VE._default_workflow_path())
        ok, probs = VE.verify_workflow(wf)
        if ok:
            self._paint("✓ 当前 workflow 自检通过：" + wf)
        else:
            self._paint("✗ 自检不通过：\n- " + "\n- ".join(probs) +
                        "\n\n按①②③④重来一遍，或点「③ 导入」重新导入。")
        try:
            probe = VE.comfy_probe(self.cfg)
            self._paint("✓ ComfyUI 可达，版本 " +
                        str(probe.get("version") or "?"))
        except Exception as ex:
            self._paint("✗ ComfyUI 连不上：" + str(ex))


class EngineDialog(QDialog):
    """创作引擎接入框：首次用 图像/视频/漫剧 时懒弹出（不必提前配）；
    填一次自动存进设置 cfg["engines"]，下次创作直接沿用。
    出图引擎被三种创作共用；漫剧可另选配音音色（默认 Xiaoxiao 女声）。"""

    def __init__(self, cfg, kind="image", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.setWindowTitle("接入创作引擎（图像/视频/漫剧共用）")
        self.setMinimumWidth(560)
        conf = dict(CRE.get_engine(cfg, "image") or {})
        lay = QVBoxLayout(self)
        tip = QLabel("「真出图」需要一个文生图引擎。已接入状态会保存在设置里，之后不再问。\n"
                     "最省事：选「硅基流动」→ 填 API Key → 点测试 → 保存。")
        tip.setWordWrap(True)
        tip.setStyleSheet("color:#475569;font-size:12px")
        lay.addWidget(tip)
        lay.addWidget(QLabel("服务商："))
        self.provider = QComboBox()
        for p in CRE.PROVIDERS:
            self.provider.addItem(p["label"], p["id"])
        cur_p = conf.get("provider", "siliconflow")
        for i in range(self.provider.count()):
            if self.provider.itemData(i) == cur_p:
                self.provider.setCurrentIndex(i)
                break
        self.provider.currentIndexChanged.connect(self._on_provider)
        lay.addWidget(self.provider)
        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(self.hint)

        def row(label, edit):
            h = QHBoxLayout(); h.addWidget(QLabel(label)); h.addWidget(edit, 1)
            lay.addLayout(h); return edit
        self.base = row("Base URL：", QLineEdit(conf.get("base_url", "")))
        self.base.setPlaceholderText("例如 https://api.siliconflow.cn/v1")
        self.key = row("API Key：", QLineEdit(conf.get("api_key", "")))
        self.key.setEchoMode(QLineEdit.Password)
        self.key.setPlaceholderText("服务商控制台里申请的 Key")
        self.model = row("出图模型：", QLineEdit(conf.get("model", "")))
        self.model.setPlaceholderText("例如 Kwai-Kolors/Kolors")
        self.size = QComboBox()
        for lab, val in CRE.SIZE_PRESETS:
            self.size.addItem(lab, val)
        for i in range(self.size.count()):
            if self.size.itemData(i) == conf.get("size", "1024x1024"):
                self.size.setCurrentIndex(i)
                break
        row("画幅：", self.size)
        self.voice = row("漫剧解说音色(可空)：",
                         QLineEdit(conf.get("voice", CRE.DEFAULT_VOICE)))
        self.voice.setPlaceholderText(CRE.DEFAULT_VOICE)
        tb = QHBoxLayout()
        self.probe_lbl = QLabel("")
        self.probe_lbl.setStyleSheet("color:#16a34a;font-size:11px")
        tb.addWidget(self.probe_lbl, 1)
        probe_b = QPushButton("🔌 测试连接")
        probe_b.clicked.connect(self._probe)
        tb.addWidget(probe_b)
        lay.addLayout(tb)
        btns = QHBoxLayout()
        btns.addStretch(1)
        cancel = QPushButton("取消（这次先不接）")
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)
        ok = QPushButton("✅ 保存并继续")
        ok.clicked.connect(self.save)
        ok.setDefault(True)
        btns.addWidget(ok)
        lay.addLayout(btns)
        self._on_provider()

    def _on_provider(self):
        pid = self.provider.currentData()
        p = next((x for x in CRE.PROVIDERS if x["id"] == pid), None)
        self.hint.setText(p["hint"] if p else "")
        conf = dict(self.cfg.get("engines", {}).get("image", {}) or {})
        conf["provider"] = pid
        conf = CRE.apply_preset(conf, pid)
        if not self.base.text().strip() and conf.get("base_url"):
            self.base.setText(conf["base_url"])
        if not self.model.text().strip() and conf.get("model"):
            self.model.setText(conf["model"])

    def _current(self) -> dict:
        return {"provider": self.provider.currentData(),
                "base_url": self.base.text().strip(),
                "api_key": self.key.text().strip(),
                "model": self.model.text().strip(),
                "size": self.size.currentData() or "1024x1024",
                "voice": self.voice.text().strip() or CRE.DEFAULT_VOICE}

    def _probe(self):
        conf = self._current()
        self.probe_lbl.setText("⏳ 测试中…")
        self.probe_lbl.setStyleSheet("color:#d97706;font-size:11px")

        def run():
            ok_, msg = CRE.probe(conf)
            def put():
                self.probe_lbl.setText(("✅ " if ok_ else "✗ ") + msg)
                self.probe_lbl.setStyleSheet(
                    ("color:#16a34a;" if ok_ else "color:#dc2626;") + "font-size:11px")
            ui_dispatch(put)
        threading.Thread(target=run, daemon=True).start()

    def save(self):
        conf = self._current()
        if not conf["base_url"]:
            self.probe_lbl.setStyleSheet("color:#dc2626;font-size:11px")
            self.probe_lbl.setText("✗ 请填写服务地址（Base URL）。")
            return
        if conf["provider"] != "sdwebui" and \
                not (conf["api_key"] and conf["model"]):
            self.probe_lbl.setStyleSheet("color:#dc2626;font-size:11px")
            self.probe_lbl.setText("✗ 云端引擎需要 API Key 和出图模型；若用本地 SD WebUI 请在上方切换服务商。")
            return
        en = CRE.engines(self.cfg)
        en["image"] = conf                       # 三种创作共用图像引擎
        _save_json(CONFIG, self.cfg)
        self.accept()


class MemDialog(QDialog):
    """记忆管理器：查看/删除它记住的关于你的事。"""
    def __init__(self, notes, on_change, parent=None):
        super().__init__(parent)
        self.setWindowTitle("它记住的关于你")
        self.notes, self.on_change = notes, on_change
        self.resize(420, 360)
        lay = QVBoxLayout(self)
        self.lst = QListWidget()
        for i, n in enumerate(self.notes):
            item = QListWidgetItem(f"[{n.get('t','')}] {n.get('tag','')}  （右击删除）")
            item.setData(0x0100, i)
            self.lst.addItem(item)
        self.lst.itemDoubleClicked.connect(self._del)
        lay.addWidget(self.lst)
        btns = QHBoxLayout()
        b1 = QPushButton("删除选中"); b1.clicked.connect(self._del_sel)
        b2 = QPushButton("清空全部"); b2.clicked.connect(self._clear)
        b3 = QPushButton("关闭"); b3.clicked.connect(self.accept)
        btns.addWidget(b1); btns.addWidget(b2); btns.addStretch(1); btns.addWidget(b3)
        lay.addLayout(btns)

    def _del_sel(self):
        idx = self.lst.currentRow()
        if 0 <= idx < len(self.notes):
            self._del(self.lst.item(idx))

    def _del(self, item):
        i = item.data(0x0100)
        if isinstance(i, int) and 0 <= i < len(self.notes):
            self.notes.pop(i)
            self.on_change(self.notes)
            self.lst.takeItem(self.lst.row(item))

    def _clear(self):
        self.notes.clear()
        self.on_change(self.notes)
        self.lst.clear()


class TaskDialog(QDialog):
    """v0.27.4 工作详情窗：看某条工作任务的进度/产物，并能**就地讨论**它的修改与进度。

    真机需求（小志）："聊天和工作关联起来；聊天内容不变；工作中也能聊天，
    主要讨论相关工作的修改和进度。" —— 讨论独立记录在这条任务的 chat 里，
    主聊天区完全不受影响。
    """

    def __init__(self, win, tid: str, parent=None):
        super().__init__(parent or win)
        self.win = win
        self.tid = tid
        # ⚠️ v0.30.15 修：以前这里**没有** `self.cfg`，而 `_render()` 与 `_send()`
        # 都读 `self.cfg.get("name")` —— 于是双击左侧「工作任务」里的任何一条，
        # 工作详情窗里**立刻 AttributeError**（被 excepthook 收进 pasm.log：
        # `'TaskDialog' object has no attribute 'cfg'`）。用户看到的是
        # 「刚完成的工作点不开 / 找不到」——入口其实一直在，是它一开就崩。
        self.cfg = dict(getattr(win, "cfg", None) or {})
        self.setWindowTitle("🛠 工作详情")
        self.resize(580, 640)
        try:
            import worklog as _W
        except Exception:
            _W = None
        self.W = _W
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setWordWrap(True)
        self.head.setStyleSheet("color:#0f172a;font-size:13px;font-weight:bold;")
        lay.addWidget(self.head)
        self.info = QLabel()
        self.info.setWordWrap(True)
        self.info.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.info.setStyleSheet("color:#334155;font-size:12px;")
        lay.addWidget(self.info)
        _lab = QLabel("产物 / 相关路径（双击打开）")
        _lab.setStyleSheet("color:#64748b;font-size:11px;margin-top:6px;")
        lay.addWidget(_lab)
        self.art = QListWidget()
        self.art.setFixedHeight(88)
        self.art.itemDoubleClicked.connect(self._open_art)
        lay.addWidget(self.art)
        _lab2 = QLabel("就该工作讨论（修改 / 进度）—— 不影响主聊天")
        _lab2.setStyleSheet("color:#64748b;font-size:11px;margin-top:6px;")
        lay.addWidget(_lab2)
        self.chat = QTextBrowser()
        self.chat.setStyleSheet("QTextBrowser{border:1px solid #e2e8f0;"
                                "border-radius:8px;background:#ffffff;color:#0f172a;"
                                "padding:6px;}")
        lay.addWidget(self.chat, 1)
        row = QHBoxLayout()
        self.inp = QLineEdit()
        self.inp.setPlaceholderText("问进度、说修改，例如：把首页改成深色主题")
        self.inp.returnPressed.connect(self._send)
        sb = QPushButton("发送")
        sb.clicked.connect(self._send)
        row.addWidget(self.inp, 1)
        row.addWidget(sb)
        lay.addLayout(row)
        self._render()

    def _render(self):
        if not self.W:
            return
        t = self.W.get(self.tid) or {}
        icon = {"running": "🔄 进行中", "done": "✅ 已完成",
                "failed": "❌ 失败"}.get(t.get("status"), "🛠")
        self.head.setText("%s　%s　%s" % (icon, self.W.kind_label(t.get("kind")),
                                          t.get("title", "")))
        when = time.strftime("%Y-%m-%d %H:%M",
                             time.localtime(t.get("updated") or time.time()))
        self.info.setText("类型：%s　状态：%s\n进度：%s　最近更新：%s" %
                          (self.W.kind_label(t.get("kind")), t.get("status", ""),
                           t.get("progress", ""), when))
        self.art.clear()
        for p in t.get("artifacts", []):
            self.art.addItem(p)
        parts = []
        for c in self.W.get_chat(self.tid):
            who = "你" if c.get("role") == "user" else self.cfg.get("name", "小U")
            color = "#0f172a" if c.get("role") == "user" else "#0369a1"
            parts.append("<p style='margin:3px 0;'><b style='color:%s'>%s：</b>%s</p>"
                         % (color, who, html.escape(c.get("content", "")).replace("\n", "<br>")))
        self.chat.setHtml("".join(parts) or
                          "<span style='color:#94a3b8'>还没有讨论，来说点什么吧。</span>")

    def _open_art(self, item):
        p = item.text()
        try:
            if os.path.exists(p):
                # 跨平台：Windows=os.startfile / macOS=open / Linux=xdg-open
                PLATFORM_OPS.open_path(p)
        except Exception:
            pass

    def _send(self):
        text = self.inp.text().strip()
        if not text or not self.W:
            return
        self.inp.clear()
        self.W.add_chat(self.tid, "user", text)
        self._render()
        t = self.W.get(self.tid) or {}
        sys_p = ("你是" + str(self.cfg.get("name", "小U")) +
                 "，正在和用户讨论一个**具体的工作任务**，只聊这个工作。\n"
                 "工作：%s（类型 %s，状态 %s，进度 %s）\n产物：%s\n"
                 "要求：结合上面信息回答；涉及改代码/改文件时给出明确下一步；"
                 "不说空话、不编造没做过的动作。" %
                 (t.get("title", ""), self.W.kind_label(t.get("kind")),
                  t.get("status", ""), t.get("progress", ""),
                  "；".join(t.get("artifacts", [])[:5]) or "（暂无）"))
        hist = self.W.get_chat(self.tid)[:-1]

        def job():
            try:
                ep = self.win._endpoint()
                if ep:
                    ans = self.win._llm_call(sys_p, text, ep[0], ep[1], ep[2],
                                             hist=hist, task="chat", max_tokens=900)
                else:
                    ans = "（当前没有可用模型，先在底部「大脑」选一个或配置云端 Key。）"
            except Exception as ex:
                ans = "（讨论时出错：%s）" % ex

            def done():
                self.W.add_chat(self.tid, "assistant", ans)
                self._render()
            try:
                self.win._ui(done)
            except Exception:
                done()

        threading.Thread(target=job, daemon=True).start()


def _con_mask(v, *_a, **_k) -> str:
    """「🔗 接入」页的密钥掩码：**保持长度**、只露头尾。

    为什么独立成一个函数：展示与保存**必须用同一个掩码**。一边用 A 算展示值、
    另一边用 B 判"有没有改过"，判等就会失败 → 真凭据被 `****` 写回（支付页踩过同款）。
    """
    s = str(v or "")
    if not s:
        return ""
    if len(s) <= 6:
        return "*" * len(s)
    return s[:4] + "*" * max(4, len(s) - 6) + s[-2:]


class ChatBrowser(QTextBrowser):
    """聊天正文浏览器：**永不导航**（v0.30.15 修，v0.30.16 换实现）。

    为什么需要单独一个子类 —— 这是 Qt 一个很坑的语义：
    我们的「📋 复制」按钮只能用锚点实现（QTextBrowser 里放不了真控件），
    href 是 `pasm://copy/<n>` —— 它不是真实资源。于是**点一下复制键，
    整个聊天正文会被清空**（不是变空白页，是 `toPlainText()` 直接变成 `''`）。
    真机症状（小志原话）：「点复制，上面的文字或其他内容全没有了」，
    后面又说「复制是可以复制了，但复制时聊天框中的内容也消失了」。

    ⚠️ **v0.30.15 的第一版修法是错的**，记录在这里免得再走一遍：
    那一版覆写了 `setSource` 让它什么都不做。**根本没被调到** ——
    实测（Qt 6.7 + PySide6）点锚点时 Qt 走的是
    **`loadResource(QTextDocument::HtmlResource, url)`**，不经过 `setSource`
    → 覆写它等于没修（这也是"为什么用户说还是消失"的原因）。
    验证这种"覆写了没生效"的唯一办法：**给候选方法都装上探针，看点击时到底谁被调**。

    现在两条防线：
      ① **鼠标释放时截住 `pasm://` 链接**：自己发 `anchorClicked` 并 `accept()` 事件，
         基类收不到这次点击 → 不会触发任何导航（主修）；
      ② `loadResource` 对 `pasm://` 返回**当前正文**（万一还有别的路径触发导航，
         等于把文档设成它自己，画面不变）；**其它 scheme 一律交给 super** ——
         聊天里的内联图走 `data:image/png;base64,…`，拦掉它图就不显示了。
    """

    #: 由我们接管的 scheme（其余交给 Qt）
    OWN_SCHEMES = ("pasm://",)

    def _href_at(self, ev):
        try:
            p = ev.position().toPoint()
        except Exception:                                        # noqa: BLE001
            try:
                p = ev.pos()
            except Exception:                                    # noqa: BLE001
                return ""
        try:
            return self.anchorAt(p) or ""
        except Exception:                                        # noqa: BLE001
            return ""

    def mouseReleaseEvent(self, ev):                             # noqa: N802
        """**任何**锚点点击都自己处理、不交给基类。

        为什么连 http 也拦：基类拿到点击就会去 `loadResource` 并**替换正文**
        （`http(s)` 也一样，会尝试去下载那个地址）。而这个控件在本应用里是
        **只读的对话记录**，所有链接都由 `anchorClicked` 的接收方处理
        （`pasm://*` 走 `_on_anchor` / `_wp_panel_anchor`，`http(s)` 交给系统浏览器），
        所以"不让它自己导航"才是正确语义。内联图不是锚点，不受影响。
        """
        href = self._href_at(ev)
        if href:
            try:
                self.anchorClicked.emit(QtCore.QUrl(href))
            except Exception:                                    # noqa: BLE001
                logging.exception("发出 anchorClicked 失败")
            ev.accept()
            return
        return super().mouseReleaseEvent(ev)

    def loadResource(self, rtype, url):                          # noqa: N802
        try:
            s = url.toString()
        except Exception:                                        # noqa: BLE001
            s = str(url or "")
        if s.startswith(self.OWN_SCHEMES):
            # 第二道防线：把"导航到自己"变成无操作（返回当前正文）
            try:
                return self.toHtml()
            except Exception:                                    # noqa: BLE001
                return ""
        return super().loadResource(rtype, url)


class OutTabBar(QTabBar):
    """右侧产出区的标签栏（v0.31.0）。

    ★ **为什么不是 QTabWidget**：产出页的效果区必须是**同一个**渲染视图 ——
      实测「每个 QWebEngineView = 一个独立 Chromium 渲染进程」，所以
      "每页一个控件"的 QTabWidget 模型不成立（开 6 个产出页 = 6 个浏览器进程）。
      这里标签只是**选择器**，内容区是唯一且共享的。

    保留 QTabWidget 的那一小撮 API（addTab / count / tabText / indexOf /
    widget / removeTab / setCurrentIndex / currentWidget / setTabText），
    是为了让 `_right_toggle`、`_panel_*` 与既有回归用例**不用改** ——
    换的是实现，不是对外契约。注意 `widget(i)` 返回的是**该页的数据字典**，
    不是控件（页本身没有自己的控件了）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages = []

    # ---- QTabWidget 的那一小撮 API ----
    def addTab(self, page, title):                      # noqa: N802
        i = super().addTab(title)
        self._pages.insert(i, page)
        return i

    def removeTab(self, i):                             # noqa: N802
        super().removeTab(i)
        if 0 <= i < len(self._pages):
            self._pages.pop(i)

    def widget(self, i):
        try:
            return self._pages[i]
        except Exception:                               # noqa: BLE001
            return None

    def currentWidget(self):                             # noqa: N802
        return self.widget(self.currentIndex())

    def indexOf(self, page):                             # noqa: N802
        for i, p in enumerate(self._pages):
            if p is page:
                return i
        return -1

    def setCurrentWidget(self, page):                    # noqa: N802
        i = self.indexOf(page)
        if i >= 0:
            self.setCurrentIndex(i)

    def tabBar(self):                                    # noqa: N802
        """兼容旧调用（老代码里 `right_tabs.tabBar()` 拿的就是自己）。"""
        return self


class FitBrowser(ChatBrowser):
    """降级渲染器：没有 WebEngine 时用它 —— **自适应高度、没有内层滚动条**。

    v0.31.0 起因（小志原话）：「文案居然这么小还带滚动条……不需要滚动条，
    全显示即可，整个页面可以带滚动条，超出部分下拉查看即可」。
    所以这里把两个方向的滚动条全关掉，并让控件高度**跟着文档实际高度走**，
    滚动交给外面唯一的容器 —— 一个页面只有一条滚动条。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.NoFrame)
        self.setStyleSheet("QTextBrowser{background:transparent;border:none;}")
        try:
            self.document().setDocumentMargin(0)
        except Exception:                               # noqa: BLE001
            pass
        self._fit_pending = False

    def setHtml(self, html):                            # noqa: N802
        super().setHtml(html or "")
        self._fit()

    def resizeEvent(self, ev):                          # noqa: N802
        super().resizeEvent(ev)
        if not self._fit_pending:
            self._fit_pending = True
            QTimer.singleShot(0, self._fit)

    def _fit(self):
        self._fit_pending = False
        try:
            d = self.document()
            d.setTextWidth(max(120, self.viewport().width()))
            self.setFixedHeight(int(d.size().height()) + 4)
        except Exception:                               # noqa: BLE001
            pass


class CompanionWindow(QMainWindow):
    result_ready = Signal(str)
    # v0.27.1 真实系统操作：worker 线程请求 UI 线程弹"授权确认"框（跨线程安全）
    ask_confirm = Signal(str, str)
    confirm_closed = Signal()

    def __init__(self):
        super().__init__()
        self._confirm_ev = None          # sysops 跨线程确认：Event + 结果槽
        self._confirm_ok = False
        self._confirm_shown = False      # v0.27.2：确认窗是否真的显示过
        self._confirm_timeout = False    # v0.27.2：是否属于超时未确认
        self._confirm_box = None
        self.ask_confirm.connect(self._on_ask_confirm)
        self.confirm_closed.connect(self._on_confirm_closed)
        self.cfg = _load_json(CONFIG, {"api_key": os.environ.get("DEEPSEEK_API_KEY", ""),
                                       "base_url": "https://api.deepseek.com/v1",
                                       "model": "deepseek-chat", "local_model": "",
                                       "name": "小U", "persona": "温和沉稳",
                                       # v0.29 权限三档：默认「安全」——任何写入/
                                       # 删除/执行都要先问，用户主动升级才放宽。
                                       "perm_level": "safe",
                                       # v0.29 语音唤醒：默认关 —— 常驻监听占着
                                       # 麦克风是隐私敏感行为，用户主动开才开。
                                       "wake_enabled": False})
        # v0.30.9：把「小人」观感三项交给 pet_tuning（**单一来源**）。
        # 聊天页头像、桌面浮窗飞天、设置面板三处都读它 —— 没人各自存一份。
        try:
            PT.load_from_cfg(self.cfg)
        except Exception:
            logging.exception("pet_tuning 载入失败（继续用默认观感参数）")
        # v0.27.12：本地模型思考链开关（默认关）——影响的是"首字要等多久"
        try:
            GW.set_local_think(self.cfg.get("local_think_mode") or (
                "on" if self.cfg.get("local_think") else "auto"))
        except Exception:
            pass
        if not self.cfg.get("ws_dir"):
            self.cfg["ws_dir"] = ""
        if not self.cfg.get("work_cats"):
            self.cfg["work_cats"] = ["全部", "文档", "演示", "表格", "脚本", "项目", "代码"]
        if "创作" not in self.cfg.get("work_cats", []):
            self.cfg["work_cats"] = list(self.cfg.get("work_cats") or ["全部"]) + ["创作"]
            _save_json(CONFIG, self.cfg)
        # v0.18.0：音频自愈开关（默认关=绝不自动改系统音量/静音，只读诊断）
        audio_mod.ALLOW_TWEAK = bool(self.cfg.get("audio_autofix", False))
        self.notes = _load_json(NOTES, [])
        self.prefs = _load_json(PREFS, [])
        self.work_ctx = {}          # 工作上下文：最近读取的文件 {path: text}
        self.path_refs = []         # 本次会话里出现过的路径（文件/文件夹），支持"刚才那个路径"
        self.dev = {}               # v0.26 开发会话：{project, lang, root, last_ts, last_req}
        # v0.31.3 上下文继承：`_chip_req` = 各栏目上一轮的需求（落盘，跨重启）；
        #   `_inherit_req/_inherit_src` = 本轮从"指代型指令"里解析出的真实需求与来源。
        self._chip_req = {}
        self._inherit_req = ""
        self._inherit_src = ""
        self.turns = 0
        # 交互模式（工种分栏）：chat=聊天（永不自动开工）其余=对应工种（输入即干活）
        # 必须先于槽位会话初始化，因为 _slot_key() 依赖 mode/_chip
        self._chip = None
        self.chip_btns = {}
        # v0.30.13：`_sync_turn_ui()` 会读 `self._status_base`，而它原本**只在**
        # `_set_status()` 里被赋值 → 刚启动、还没干过活就点栏目按钮时，
        # `_set_chip → _sync_turn_ui` 抛 AttributeError（异常被 excepthook 收进
        # pasm.log，界面表现为"点了栏目但提示/占位没跟着更新"）。
        self._status_base = ""
        _cm = self.cfg.get("chat_mode")
        if _cm in ("chat", "work") or _cm not in dict(self.WORK_CHIPS):
            _cm = "chat" if _cm != "work" else "work"
        self.mode = "work" if _cm != "chat" else "chat"
        self._chip = None if _cm in ("chat", "work") else _cm
        # v0.17.2 槽位化会话：每个栏目各自维护自己的对话线程，切栏目=换对话信息框，
        # 互不残留。slot_map: 栏目 → 当前会话文件 id；会话归属栏目 = 文件 chip 字段
        # = 左侧列表图标，三者始终一致；空会话（无录入）不出现在左侧列表。
        self.slot_map = self._load_slots()
        # 多会话：优先载入最近一次会话（聊天内容全程落盘，重启不丢、不截断）
        self.conv_id = None
        self._load_latest_conv()
        # 当前会话的创作产物目录（同会话内"优化上一幅图/重做成片"复用同一文件夹，
        # 不每次另起新项目；随会话文件持久化，切会话/重启各自归位）
        self._ses_img_dir = getattr(self, "_ses_img_dir", None)
        self._ses_story_dir = getattr(self, "_ses_story_dir", None)
        self._ses_story = getattr(self, "_ses_story", None)   # 漫剧分步企划（story.json 镜像）
        # 认知皮层：恢复上次会话的话题与正在推进的目标（跨重启不失忆）
        self.cog = COG.CogState(persona=self.cfg.get("persona", "温和沉稳"))
        self.cog.from_dict(ML.working_state())
        # v0.17.5 修补：接入 PASM × LLM 双脑路由器（v0.4.1）
        # 每次回复都会让 PASM 真的"思考一步"，把 emotion/personality/记忆
        # 注入 LLM system prompt，让大模型真正感受到"它"的内心。
        # v0.30.7 更正一处**错误归因**（朋友机排查时真被它带偏过）：
        # 这里原本是 `from pasm.pasmlink import DualBrainRouter`，但全仓根本没有
        # `pasm/pasmlink.py`，也没有任何人再定义 DualBrainRouter / build_context_prompt
        # —— 导入**必然失败**，于是每次启动都打一行
        #   "PASM 完整引擎不可用（桌面包不含 torch），已启用轻量链路 pasm_light"
        # 这句话是错的：装不装 torch 都失败，真实原因是**这个模块已经不在了**。
        # 能力并没有因此缺失：性格/情绪/成长阶段现在由 `_build_system` 直接拼进系统提示
        # （见其中的「规则2 性格与此刻内心」），不再需要这个中间层。
        self.brain = None
        self._episode_turn = 0          # 本次运行内已自动归档轮次
        self.busy = False
        # —— v0.17.5 回合表：slot -> turn。一个回合=一次"提问→模型回复"。
        # 同一栏目同时最多 1 个回合；不同栏目可并发。线程只读冻结快照，
        # 结果回主线程按归属落位（还在原会话→正常展示；已切走/新建→收进原会话）。
        self._turns: dict = {}
        self._turn_seq = 0
        self._kb_running = False        # 后台学习是否在跑（不锁聊天，只挡重复提交）
        self._pending_plan = None       # v0.22 待确认计划 {"text","steps"}
        self._pend = []                 # v0.23 同栏排队消息（完成自动接续）
        self._ses_gen = {}              # v0.26.2 产物记忆：{kind: {"path","md","title"}} 同会话续改同一文件
        self.local = {}
        # 拟人事件总线：给桌面小人与头像情绪反应用的 (事件, 时间戳)
        # 取值：praise 被夸 / scold 被凶 / aggrieved 委屈 / pardon 被哄(骂错) /
        #       call 被叫回 / quiet 让它别打扰
        self.pet_event = ("", 0.0)
        self._hurt_until = 0.0          # 委屈冷却：被哄后短暂"闹别扭"
        self._last_ai_do = (0.0, "")    # (时间, 它刚做过的活) 供"骂错"归因
        self._done_once = False
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} · 会成长的 AI 伙伴")
        # v0.30.10：默认窗口放大 —— 原来 980 宽时「nav 198 + 右栏 300」
        # 把中间挤到 482，方案/广告在右栏里根本没法看。
        self.resize(1180, 760)
        self.setWindowIcon(self._icon())
        self._apply_persona(reset_agent=True)
        self._build()
        # v0.17.5：回合结果改走 _turn_done 回调（带回合归属），不再经 result_ready 信号
        QTimer.singleShot(0, self._boot)
        # v0.28.x：延迟 2.5s 再拉起纯后台的定时轮询 / 跨端接收，绝不抢开机资源
        QTimer.singleShot(2500, self._start_background_jobs)
        check_async(lambda latest: self._ui(lambda: self._note_latest(latest)))
        # v0.29：延迟同步语音唤醒（不拖慢启动；配置为开才真启动监听）
        QTimer.singleShot(2000, self.sync_wakeword)

    def _apply_persona(self, reset_agent=False):
        seed = int(time.time()) % 10000
        # v0.30.14：**不许**再硬索引 `cfg["persona"]`。实测（2026-09-17）：
        # 配置里缺这个键（老版本留下的配置 / 手工改过 / 写盘被截断）→ `__init__` 里
        # 直接 KeyError → **应用启动即崩，界面上没有任何提示**（异常被 excepthook
        # 收进 pasm.log），用户只会觉得"双击了但打不开"。这类"起不来又没说为什么"
        # 最难排查，所以这里**自愈**：认不出就用默认性格并写回配置。
        #
        # 是怎么发现的：做"冻结版带接入配置启动验证"时，我种了一份**最小配置**
        # （只有接入相关的键）→ 应用起不来 → 顺着 pasm.log 才看到是 persona，
        # **跟接入毫无关系**。属于歪打正着，但确实是真 bug。
        _p = str(self.cfg.get("persona") or "")
        _healed = False
        if _p not in PERSONALITIES:
            if _p:
                logging.warning("配置里的性格 %r 不认识 → 回落默认", _p)
            _p = ("温和沉稳" if "温和沉稳" in PERSONALITIES
                  else next(iter(PERSONALITIES)))
            self.cfg["persona"] = _p                 # 写回内存
            _healed = True
        if _healed:
            try:
                _save_json(CONFIG, self.cfg)         # 落盘：下次启动不再走这条路
            except Exception:                        # noqa: BLE001
                logging.debug("自愈写盘失败（不影响本次启动）", exc_info=True)
        if reset_agent or _p != getattr(self, "_persona_now", None):
            # 统一走引擎接口创建（v0.28.6）：完整引擎不可用时由接口层如实降级，
            # 调用方无感；返回对象保证满足 engine_api 契约（act/learn/snapshot/...）。
            self.agent = EF.make_engine(
                personality_seed=PERSONALITIES[_p],
                seed=seed, plan_samples=8, plan_iters=1)
            self.env = EF.make_env(seed=seed)
            self.env.reset()
            self.agent.reset_episode()
            self._persona_now = _p
            # v0.17.5 修补：agent 重置时同步重建 brain（v0.4.1）
            # v0.30.7：pasm.pasmlink 已不存在（见上），保持 None；能力在 _build_system 里。
            self.brain = None

    # ---------- UI（左侧三栏：上=功能页+对话 / 中=对话列表 / 下=设置） ----------
    # 「对话」放最前 = 随时点回当前正在聊的内容（任务/工作台等切走后再回来不丢）
    # v0.30.17：「🧭 工作流」搬到这里（原先在右侧栏当不可关闭的固定页）。
    #   小志的判断依据（2026-09-17）：「工作流本质是『发起一件多步的事』，
    #   属左栏任务/自发行为这一类语义，不属于右侧『看结果』。」它原先占着右栏
    #   第一个位置且永不关闭，把**产出效果**挤到了后面 —— 这就是要搬的原因。
    #   ⚠️ 顺序与 stack 索引**故意不一致**：这里排第 4 个好找，stack 里追加在最后
    #   （索引 8），免得改动既有页面索引（_switch_page 用的是显式字典，不受影响）。
    NAV_ITEMS = [("chat", "💬  对话"), ("task", "✅  任务"), ("work", "🛠  工作台"),
                 ("flow", "🧭  工作流"),
                 ("team", "👥  团队"), ("auto", "⚡  自发行为"), ("kb", "📚  资料库"),
                 ("mem", "🧠  记忆"), ("skill", "🎓  技能")]
    # 工种分栏（仿 WorkBuddy：动手前由用户显式选工种，而不是靠猜意图）。
    # 「干活」不是一个工种——它被拆成了具体的 文档类 / 开发创作类：
    #   · 文档类：文案 / 表格 / PPT / Word（产出文档）
    #   · 开发创作类：开发 / 视频 / 图像 / 漫剧（代码与创意）
    # 自学与技能不属于这些（自学在聊天里说"上网学…"即触发；技能库在左侧
    # 「🎓 技能」管理，聊天/干活中按需自动检索调用）。
    _WORK_GROUP = {
        "copy": "📄 文档", "xls": "📄 文档", "ppt": "📄 文档", "doc": "📄 文档",
        "project": "🚀 开发与创作", "video": "🚀 开发与创作",
        "image": "🚀 开发与创作", "manga": "🚀 开发与创作",
        "ad": "🚀 开发与创作",
    }
    WORK_CHIPS = [
        ("chat", "💬 聊天"),                 # 普通对话，绝不自动开工
        ("copy", "✍️ 文案"), ("xls", "📊 表格"), ("ppt", "📽 PPT"), ("doc", "📄 Word"),
        ("project", "🖥 开发"), ("video", "🎬 视频"), ("image", "🎨 图像"),
        ("manga", "📖 漫剧"), ("ad", "📣 广告设计"),
    ]
    # 各工种「强制开工」的规范种子句（确保意图规则一定命中，不再弹能力清单）
    CHIP_SEED = {
        "copy": "直接帮我写一段{要求}（这是文案创作，输出成稿即可，不要发能力清单）",
        "xls": "帮我做一个 Excel 表格：{要求}",
        "ppt": "帮我做一份 PPT 演示文稿：{要求}",
        "doc": "帮我写一份 Word 文档：{要求}",
        "project": "帮我开发一个项目：{要求}",
        "ad": "帮我做一版广告设计：{要求}（先给标题/卖点/行动号召，"
              "再出主视觉画面）",
        "image": "请把下面这个画面需求写成可直接交给文生图模型的英文提示词包"
                 "（含 主体/风格/构图/光线/镜头，一段话可直接出图）：\n{要求}",
    }

    # ── 栏目整合（v0.29）：文档类 4 个工种在**界面上**收进一个入口 ──────────
    # 文案 / 表格 / PPT / Word 的本质是"同一份内容的不同交付格式"，
    # 摆成 4 个并列按钮等于逼用户先做一次格式决策。
    #
    # ⚠️ 上面 WORK_CHIPS 是「全部合法 chip key」的权威清单，**一个都不能删**：
    #    `_set_chip` 的守卫、`__init__` 对老配置 chat_mode 的校验、`/mode` 命令
    #    都依赖它。删 key 会让老用户配置被静默重置、老会话打不开。
    #    下面这张表只决定"按钮怎么摆"，内部 _chip 仍是 copy/xls/ppt/doc。
    _DOC_SUITE_KEYS = ("copy", "xls", "ppt", "doc")
    _CHIP_BAR = [
        ("chat", "💬 聊天"),
        ("docsuite", "📄 文档与演示"),      # UI 容器：展开下面这行子标签
        ("project", "🖥 开发"),
        ("video", "🎬 视频"),
        ("image", "🎨 图像"),
        ("ad", "📣 广告设计"),
        ("manga", "📖 漫剧"),
    ]
    # (key, 图标+名称, 一句说明) —— 说明那列给浮层卡片当副标题，
    # 一句话说清"选它会发生什么"，比纯格式名更好决策。
    _DOC_SUITE_LABELS = [("copy", "✍️ 文案", "出成稿"),
                         ("xls", "📊 表格", "Excel 表"),
                         ("ppt", "📽 PPT", "演示稿"),
                         ("doc", "📄 Word", "文档")]

    _BTN_CSS = ("QPushButton{text-align:left;padding:0 10px;border:none;border-radius:8px;"
                "color:#334155;background:transparent;font-size:13px}"
                "QPushButton:hover{background:#e2e8f0}"
                "QPushButton:checked{background:#0ea5e9;color:white;font-weight:bold}")
    _BTN_CSS2 = ("QPushButton{text-align:left;padding:0 10px;border:none;border-radius:8px;"
                 "color:#475569;background:transparent;font-size:12px}"
                 "QPushButton:hover{background:#e2e8f0}")

    #: v0.30.13：工作流页的**空态引导**。
    #  真机反馈（2026-09-17）：「工作流状态里进度永远是 0%，这个工作流如何操作？还有存在的必要吗」
    #  —— 面板原本空着什么都不说，用户不知道要"先输入目标、再点启动、再点执行下一步"。
    #  空态必须自己把这三步讲清楚，而不是让用户猜。
    _WP_EMPTY_HINT = (
        "<div style='line-height:1.7;color:#475569'>"
        "<b>还没有工作流。</b><br><br>"
        "① 在上面输入框写下目标（例：<i>做一个奶茶店夏季推广方案</i>）<br>"
        "② 回车或点 <b>▶ 启动工作流</b> —— 它会自动拆成几步，并"
        "<b>立刻开跑第一步</b><br>"
        "③ 每跑完一步，点 <b>✅ 完成此步 → 下一步</b> 继续<br><br>"
        "<span style='color:#94a3b8'>每一步都是<b>真执行</b>（真出文件），"
        "不是只记个进度条。</span>"
        "</div>")

    #: 右侧产出栏最小宽度（再窄就放不下方案正文）；v0.30.10
    _RIGHT_MIN = 300
    #: 右侧栏最大宽度（再宽中间就没法看了）
    _RIGHT_MAX = 900
    #: 中间主区的保底宽度（拖右栏时不许把它压没）
    _MAIN_MIN = 360

    def _build(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ===== 左侧三栏导航 =====
        navw = QWidget()
        navw.setFixedWidth(198)
        navw.setStyleSheet("background:#eef2f7;border-right:1px solid #e2e8f0;")
        nav = QVBoxLayout(navw)
        nav.setContentsMargins(8, 10, 8, 8)
        nav.setSpacing(3)
        brand = QLabel(self.cfg.get("name", "小U"))
        bf = brand.font()
        bf.setBold(True)
        bf.setPointSize(12)
        brand.setFont(bf)
        brand.setAlignment(Qt.AlignCenter)
        self.brand_lbl = brand
        nav.addWidget(brand)
        sub = QLabel("PASM Studio")
        sub.setAlignment(Qt.AlignCenter)
        sub.setStyleSheet("color:#94a3b8;font-size:10px")
        nav.addWidget(sub)
        nav.addSpacing(6)
        # —— 上栏：功能页面入口 ——
        self.nav_btns = {}
        for key, label in self.NAV_ITEMS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(32)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._BTN_CSS)
            b.clicked.connect(lambda _=False, k=key: self._switch_page(k))
            nav.addWidget(b)
            self.nav_btns[key] = b
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet("color:#dbe3ee;")
        nav.addWidget(sep)
        # —— 中栏：对话列表（聊天永不丢，直接在主入口常驻）——
        ct = QLabel("对话")
        ct.setStyleSheet("color:#94a3b8;font-size:11px;padding:2px 6px;font-weight:bold;")
        nav.addWidget(ct)
        self.conv_new = QPushButton("＋ 新对话")
        self.conv_new.setFixedHeight(28)
        self.conv_new.setCursor(Qt.PointingHandCursor)
        self.conv_new.setStyleSheet(
            "QPushButton{border:1px solid #0ea5e9;border-radius:14px;color:#0c4a6e;"
            "background:#e0f2fe;font-size:12px;font-weight:bold;}"
            "QPushButton:hover{background:#bae6fd}")
        self.conv_new.clicked.connect(self._new_session)
        nav.addWidget(self.conv_new)
        self.conv_list = QListWidget()
        # 无滚动条（视觉清爽；滚轮仍可翻阅），长会话用 … 单行截断
        self.conv_list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.conv_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.conv_list.setItemDelegate(ElideListDelegate(self.conv_list))
        self.conv_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.conv_list.customContextMenuRequested.connect(self._conv_menu)
        self.conv_list.itemClicked.connect(self._conv_open_item)
        self.conv_list.setStyleSheet(
            "QListWidget{background:transparent;border:none;color:#1e293b;}"
            "QListWidget::item{padding:6px 6px;border-radius:6px;"
            "border-bottom:1px solid #e8edf5;}"
            "QListWidget::item:hover{background:#e2e8f0}"
            "QListWidget::item:selected{background:#cfe8fb;color:#0c4a6e;font-weight:bold}")
        nav.addWidget(self.conv_list, 1)
        # —— 下栏：设置等 ——
        nav.addSpacing(4)
        for text, cb in [("⚙  设置", self._settings), ("📊  数据", self._pref_dialog),
                         ("📡  模型", self._detect_ui)]:
            b = QPushButton(text)
            b.setFixedHeight(30)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(self._BTN_CSS2)
            b.clicked.connect(cb)
            nav.addWidget(b)

        # ===== 中部页面栈 =====
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_chat())    # 0 对话（主聊天，初始显示）
        self.stack.addWidget(self._page_task())    # 1 任务
        self.stack.addWidget(self._page_work())    # 2 工作台
        self.stack.addWidget(self._page_auto())    # 3 自动化
        self.stack.addWidget(self._page_kb())      # 4 资料库
        self.stack.addWidget(self._page_mem())     # 5 记忆
        self.stack.addWidget(self._page_skill())   # 6 技能
        self.stack.addWidget(self._page_team())    # 7 团队（v0.24 多智能体）
        self.stack.addWidget(self._page_flow())    # 8 工作流（v0.30.17 从右侧栏搬来）
        root.addWidget(navw)
        # v0.30.10：中部与右侧栏之间加**可拖动分隔条**。
        # 小志的反馈是「右侧栏宽度不够，方案/广告看起来不方便」——
        # 单纯加宽治不了本：不同内容要的宽度不一样（一句广告 vs 一份长方案），
        # 所以做成**能拖 + 记住**，这才是真解决。
        self.split = QSplitter(Qt.Horizontal)
        self.split.setChildrenCollapsible(False)   # 别让手抖把某一侧拖没了
        self.split.setHandleWidth(6)
        self.split.setStyleSheet(
            "QSplitter::handle{background:#e2e8f0;}"
            "QSplitter::handle:hover{background:#93c5fd;}"
            "QSplitter::handle:pressed{background:#60a5fa;}")
        self.split.addWidget(self.stack)
        self.stack.setMinimumWidth(self._MAIN_MIN)
        root.addWidget(self.split, 1)

        # ===== 右侧：工作台（v0.30.9 重排）=====
        # 小志的逐条要求，对应关系写在这（下次改这里请先读一遍）：
        #   ① 「产物/自动化/工作」三个固定页没必要 → **按产出开标签**，像浏览器那样：
        #      出一份方案就多一个「📋 …」页，出一张图就多一个「🖼 …」页，可关、可拖动。
        #   ② 「预览应该包括文案、产物」→ 每个产出页里**既有文案区，也有产物预览区**。
        #   ③ （v0.30.17 作废）原先是"工作流收进右栏固定的「🤖 工作流」页"——
        #      小志 2026-09-17 改判：工作流属"发起多步任务"，已搬到左栏「🧭 工作流」；
        #      右栏不再有任何固定页与工具控制台。
        #   ④ 「工作板块不需要了」「复盘是人工作的不需要显示」→ 该页连同目录筛选、
        #      工作列表、复盘区一并删除，只留一行「当前工作」与手头这件事关联。
        #   ⑤ 整体参考 WorkBuddy：顶上一个标题 + 收起键，下面是标签内容区。
        # 注意：内心（self.mind）在小人卡片里创建，这里绝不能再建一次。
        right = QWidget()
        right.setStyleSheet("background:#fbfdff;border-left:1px solid #e2e8f0;")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(8, 8, 8, 8)
        rl.setSpacing(6)
        self.right = right
        # v0.30.10：宽度不写死了（原来固定 300）。
        #   · 默认 420（比原来宽 40%），能被分隔条拖动；
        #   · 拖动结果写进 config.json（键 `right_w`），下次开机还是它；
        #   · 下限 300 / 上限 900 —— 越界值一律钳制（配置可能被手改坏）。
        try:
            _rw = int(self.cfg.get("right_w") or 420)
        except Exception:
            _rw = 420
        self._right_w = max(self._RIGHT_MIN, min(self._RIGHT_MAX, _rw))
        right.setMinimumWidth(self._RIGHT_MIN)
        hdr = QHBoxLayout()
        hdr.setSpacing(4)
        self.right_title = QLabel("产出")
        self.right_title.setStyleSheet("color:#334155;font-size:12px;font-weight:bold;")
        hdr.addWidget(self.right_title)
        self.right_hint = QLabel("")
        self.right_hint.setStyleSheet("color:#94a3b8;font-size:10px;")
        hdr.addWidget(self.right_hint)
        hdr.addStretch(1)
        _fold_css = ("QPushButton{border:1px solid #cbd5e1;border-radius:6px;"
                     "background:#f8fafc;color:#475569;font-size:11px;}"
                     "QPushButton:hover{background:#e2e8f0;}")
        self.show_clear = QPushButton("✕ 清空")
        self.show_clear.setFixedSize(46, 22)
        self.show_clear.setCursor(Qt.PointingHandCursor)
        self.show_clear.setToolTip("关掉全部产出标签页")
        self.show_clear.setStyleSheet(_fold_css)
        self.show_clear.clicked.connect(self._show_clear)
        hdr.addWidget(self.show_clear)
        self.right_fold = QPushButton("▶")
        self.right_fold.setFixedSize(24, 22)
        self.right_fold.setCursor(Qt.PointingHandCursor)
        self.right_fold.setToolTip("收起工作台（腾出聊天宽度）")
        self.right_fold.setStyleSheet(_fold_css)
        self.right_fold.clicked.connect(lambda: self._right_toggle(False))
        hdr.addWidget(self.right_fold)
        self.right_show = QPushButton("◀")
        self.right_show.setFixedSize(24, 22)
        self.right_show.setCursor(Qt.PointingHandCursor)
        self.right_show.setToolTip("展开工作台")
        self.right_show.setStyleSheet(_fold_css)
        self.right_show.clicked.connect(lambda: self._right_toggle(True))
        self.right_show.setVisible(False)
        hdr.addWidget(self.right_show)
        rl.addLayout(hdr)
        # ===== 右侧：产出（v0.31.0 重构：标签只是"选择器"，内容区唯一）=====
        #   为什么把 QTabWidget 换掉：效果区必须是**同一个**渲染视图
        #   （每个 QWebEngineView = 一个 Chromium 渲染进程，实测），
        #   所以不能"每页一个控件"。标签负责选，内容区负责画。
        self.right_tabs = OutTabBar()
        self.right_tabs.setTabsClosable(True)        # 浏览器味儿：产出可关
        self.right_tabs.setMovable(True)
        self.right_tabs.setExpanding(False)
        self.right_tabs.setDrawBase(False)
        self.right_tabs.setUsesScrollButtons(True)   # 标签多了能横滚，不撑破右栏
        self.right_tabs.setElideMode(Qt.ElideRight)
        self.right_tabs.setStyleSheet(
            "QTabBar{background:transparent;}"
            "QTabBar::tab{padding:4px 10px;margin:0 3px 0 0;font-size:11.5px;"
            "color:#64748b;background:#f1f5f9;border:1px solid #e2e8f0;"
            "border-bottom:none;border-top-left-radius:7px;border-top-right-radius:7px;"
            "max-width:150px;}"
            "QTabBar::tab:selected{color:#0369a1;font-weight:bold;background:#ffffff;"
            "border-color:#bae6fd;}"
            "QTabBar::tab:hover{background:#e0f2fe;}")
        self.right_tabs.tabCloseRequested.connect(self._panel_close)
        self.right_tabs.currentChanged.connect(self._panel_switch)

        # —— v0.30.17：右栏不再有固定页（工作流控制台已搬到左栏「🧭 工作流」）。
        #    v0.31.0：没产出时由**空态**占位；有产出时下面的内容区登场。
        self.right_empty = QLabel(
            "<div style='line-height:1.9;color:#64748b;padding:14px 12px'>"
            "<div style='font-size:13px;font-weight:bold;color:#0f172a;"
            "margin-bottom:10px'>这里显示你生成的东西的效果</div>"
            "<div style='background:#ffffff;border:1px solid #e2e8f0;border-radius:10px;"
            "padding:11px 13px'>"
            "在左侧选一个工种（文档与演示 / 开发 / 视频 / 图像 / 广告设计 / 漫剧）"
            "说清需求，做完会自动在这里开一页：<br>"
            "<span style='color:#0369a1'>· 真浏览器渲染</span>"
            "—— 站点页面、图片、视频都能直接看<br>"
            "<span style='color:#0369a1'>· 一个产物一页</span>"
            "—— 之后每提一次要求，新效果**往下接着排**，旧的不删不覆盖<br>"
            "<span style='color:#0369a1'>· 每页底部</span>"
            "—— 确认留存 / 删除 / 下一步（就着效果说要改什么）"
            "</div>"
            "<div style='color:#94a3b8;margin-top:10px;font-size:11.5px'>"
            "要发起多步任务，去左栏「🧭 工作流」。</div>"
            "</div>")
        self.right_empty.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.right_empty.setWordWrap(True)
        self.right_empty.setStyleSheet("background:transparent;")

        # ---- 唯一内容区 ----
        self.right_pane = QWidget()
        self.right_pane.setStyleSheet("background:transparent;")
        _pv = QVBoxLayout(self.right_pane)
        _pv.setContentsMargins(0, 6, 0, 0)
        _pv.setSpacing(6)
        _pv.addWidget(self.right_tabs)
        # 信息行：这是哪个产物 + 已经到第几轮 + 定位/打开
        _irow = QHBoxLayout()
        _irow.setSpacing(6)
        self.p_info = QLabel("")
        self.p_info.setWordWrap(True)
        self.p_info.setStyleSheet(
            "QLabel{color:#0f172a;font-size:12px;font-weight:bold;}")
        _irow.addWidget(self.p_info, 1)
        _mini = self._mini_btn_css()
        self.p_reveal = QPushButton("📂 定位")
        self.p_reveal.setFixedHeight(24)
        self.p_reveal.setCursor(Qt.PointingHandCursor)
        self.p_reveal.setToolTip("在资源管理器里打开所在文件夹并选中这个文件")
        self.p_reveal.setStyleSheet(_mini)
        _irow.addWidget(self.p_reveal)
        self.p_open = QPushButton("↗ 打开")
        self.p_open.setFixedHeight(24)
        self.p_open.setCursor(Qt.PointingHandCursor)
        self.p_open.setToolTip("用系统默认程序打开这个产物")
        self.p_open.setStyleSheet(_mini)
        _irow.addWidget(self.p_open)
        _pv.addLayout(_irow)
        # 效果区（懒建：没人看效果时**不建**，不给启动加负担）
        self.p_eff_holder = QWidget()
        self.p_eff_holder.setStyleSheet("background:transparent;")
        self.p_eff_lay = QVBoxLayout(self.p_eff_holder)
        self.p_eff_lay.setContentsMargins(0, 0, 0, 0)
        self.p_eff_lay.setSpacing(0)
        _pv.addWidget(self.p_eff_holder, 1)
        self.p_eff = None                     # (模式, 控件)；模式 ∈ {"we","fit"}
        self._eff_mode = ""
        self._eff_why = ""
        # 操作区（针对**最新一轮**的产物）
        _orow = QHBoxLayout()
        _orow.setSpacing(6)
        self.p_ok = QPushButton("✅ 确认留存")
        self.p_ok.setFixedHeight(26)
        self.p_ok.setCursor(Qt.PointingHandCursor)
        self.p_ok.setToolTip("标记这一轮的产出已留存，并记进工作台账。\n"
                             "**不动文件** —— 搬动会让路径变、聊天里的旧路径就失效了。")
        self.p_ok.setStyleSheet(
            "QPushButton{border:1px solid #86efac;border-radius:7px;background:#f0fdf4;"
            "color:#15803d;font-size:11px;}"
            "QPushButton:hover{background:#dcfce7;}"
            "QPushButton:disabled{color:#16a34a;background:#f0fdf4;border-color:#bbf7d0;}")
        _orow.addWidget(self.p_ok)
        self.p_del = QPushButton("🗑 删除")
        self.p_del.setFixedHeight(26)
        self.p_del.setCursor(Qt.PointingHandCursor)
        self.p_del.setToolTip("把最新一轮的产物移入**回收站**（可恢复）。会先问你一次。")
        self.p_del.setStyleSheet(
            "QPushButton{border:1px solid #fecaca;border-radius:7px;background:#fef2f2;"
            "color:#b91c1c;font-size:11px;}"
            "QPushButton:hover{background:#fee2e2;}"
            "QPushButton:disabled{color:#94a3b8;background:#f8fafc;border-color:#e2e8f0;}")
        _orow.addWidget(self.p_del)
        self.p_next = QPushButton("💬 下一步…")
        self.p_next.setFixedHeight(26)
        self.p_next.setCursor(Qt.PointingHandCursor)
        self.p_next.setToolTip("就着当前效果说下一步怎么改 —— 输入行在下面就地展开，"
                               "新效果会**接着往下排**")
        self.p_next.setStyleSheet(
            "QPushButton{border:1px solid #bae6fd;border-radius:7px;background:#f0f9ff;"
            "color:#0369a1;font-size:11px;}"
            "QPushButton:hover{background:#e0f2fe;}")
        _orow.addWidget(self.p_next)
        _orow.addStretch(1)
        _pv.addLayout(_orow)
        self.p_note = QLabel("")
        self.p_note.setWordWrap(True)
        self.p_note.setStyleSheet("QLabel{color:#64748b;font-size:11px;}")
        _pv.addWidget(self.p_note)
        # 讨论区（默认收起）
        self.p_ask = QWidget()
        self.p_ask.setStyleSheet("background:transparent;")
        _av = QHBoxLayout(self.p_ask)
        _av.setContentsMargins(0, 0, 0, 0)
        _av.setSpacing(6)
        self.p_ask_edit = QLineEdit()
        self.p_ask_edit.setPlaceholderText("就着上面的效果说要改什么（回车或点发送）")
        self.p_ask_edit.setMinimumHeight(28)
        _av.addWidget(self.p_ask_edit, 1)
        self.p_ask_send = QPushButton("发送")
        self.p_ask_send.setFixedHeight(28)
        self.p_ask_send.setCursor(Qt.PointingHandCursor)
        self.p_ask_send.setStyleSheet(
            "QPushButton{border:1px solid #0ea5e9;border-radius:7px;background:#e0f2fe;"
            "color:#0c4a6e;font-size:11px;font-weight:bold;}"
            "QPushButton:hover{background:#bae6fd;}")
        _av.addWidget(self.p_ask_send)
        self.p_ask.setVisible(False)
        _pv.addWidget(self.p_ask)
        self.p_reveal.clicked.connect(self._panel_reveal_cur)
        self.p_open.clicked.connect(self._art_open_cur)
        self.p_ok.clicked.connect(self._panel_confirm_cur)
        self.p_del.clicked.connect(self._panel_delete_cur)
        self.p_next.clicked.connect(self._panel_ask_toggle_cur)
        self.p_ask_send.clicked.connect(self._panel_ask_send_cur)
        self.p_ask_edit.returnPressed.connect(self._panel_ask_send_cur)

        # 空态(0) ↔ 产出区(1)
        self.right_stack = QStackedWidget()
        self.right_stack.addWidget(self.right_empty)
        self.right_stack.addWidget(self.right_pane)
        rl.addWidget(self.right_stack, 1)
        # 产出页登记表：key -> _panel_new() 那个 dict（一个产物一页）
        self._out_tabs = {}
        self._art_paths = []            # 产物历史（仅用于统计/兼容）
        # v0.30.17：右栏「产出」的接线三件套
        self._effect = None             # 本轮产出的结构化登记（由各工种主动登记）
        self._effect_ctx = ""           # 产出页讨论区待注入的上下文（用完即清）
        self._cur_wid = ""              # 本轮工作台账 id（「确认留存」写回它）
        self._cur_req = ""              # v0.31.0：本轮用户原话（写进块头"第 N 轮"那行）
        self._wp_picked_tid = ""
        self.split.addWidget(right)
        self.split.setStretchFactor(0, 1)      # 多余空间给主区
        self.split.setStretchFactor(1, 0)      # 右栏保持用户设定的宽度
        self.split.splitterMoved.connect(self._right_on_drag)
        self._right_apply()
        # 首次布局完成后再对一次：窗口还没 show 时拿不到分隔条的真实宽度
        QTimer.singleShot(0, self._right_apply)
        # v0.30.6：把"用当前大脑写文案"的入口交给执行器（广告/媒体会用到）。
        # 不注入的话它们只能走离线模板 —— 功能"能跑"，但产出的不是真东西。
        try:
            EXEC.set_llm_fn(self._work_llm())
        except Exception:
            logging.exception("set_llm_fn failed")
        self._switch_page("chat")          # 启动默认停在对话页并高亮「对话」
        self._refresh_conv_list()
        self._reload_model_combo()
        # v0.31.3：读回"各栏目上一轮需求 + 开发会话"（跨重启续上「请立即开始」）
        self._load_work_state()
        self._heal_autoscan()              # v0.24 C1 哨兵：启动后静默巡检一次

    # ---------- v0.29：语音唤醒 ----------
    def _wake_listen(self, timeout=4.0):
        """给唤醒引擎用的识别通道 —— 复用现成的 asr，不另起一套录音。"""
        # 助手正在说话时不听：否则会把自己的声音识别成唤醒词（自唤醒）
        if getattr(self, "speaking", False):
            return ""
        import asr as _asr
        if not _asr.has_recognizer():
            raise RuntimeError("未检测到中文语音识别引擎")
        return _asr.listen_once(timeout=timeout)

    def _on_wake_hit(self, cmd):
        """被叫醒了：带指令就直接发出去，只喊了名字就应一声。"""
        def go():
            try:
                c = str(cmd or "").strip()
                if c:
                    self._append("你", html.escape(c))
                    self.input.setPlainText(c)
                    self.send()
                else:
                    self._append("系统", "🔔 我在，说吧～")
            finally:
                # 处理完再把耳朵打开（延迟 2.6s，避开助手语音播报，防自唤醒）
                try:
                    if bool(self.cfg.get("wake_enabled")):
                        QTimer.singleShot(2600, self.sync_wakeword)
                except Exception:
                    pass
        try:
            self._ui(go)
        except Exception:
            pass

    def sync_wakeword(self):
        """按配置同步唤醒监听：开就启、关就停、**改名即时生效**。

        唤醒词就是 cfg["name"]，所以"改名后唤醒也跟着变"是结构上成立的，
        不依赖任何重训或重启。
        """
        try:
            import wakeword as WW
        except Exception as e:
            return False
        if getattr(self, "_wake", None) is None:
            self._wake = WW.WakeWord(
                name_getter=lambda: self.cfg.get("name", "小U"),
                on_wake=self._on_wake_hit,
                listen_fn=self._wake_listen)
        w = self._wake
        w.set_name(str(self.cfg.get("name", "小U")))     # 改名即时生效
        want = bool(self.cfg.get("wake_enabled"))
        if want and not w.running:
            if w.start():
                self._append("系统", "🔔 语音唤醒已开启：叫一声「%s」我就来。"
                             % html.escape(w._name or "名字"))
            else:
                self._append("系统", "🔔 语音唤醒开启失败：%s"
                             % html.escape(w.last_error or "未知原因"))
        elif not want and w.running:
            w.stop()
            self._append("系统", "🔔 语音唤醒已关闭。")
        return w.running

    def _switch_page(self, key):
        # v0.30.17：新增 "flow"（工作流，stack 索引 8）。
        # ⚠️ 这个字典是 key→stack 索引的**唯一权威**；NAV_ITEMS 的顺序只决定按钮
        #    排列，两者刻意解耦（新页一律追加到 stack 末尾，不动既有索引）。
        idx = {"chat": 0, "task": 1, "work": 2, "auto": 3,
               "kb": 4, "mem": 5, "skill": 6, "team": 7, "flow": 8}.get(key)
        if idx is None:
            return
        # v0.29 页面转场动效（Qt 原生，零新依赖）。
        # 转场引擎是**一个窗口建一个、反复用** —— 每次新建动画对象会在连切时
        # 留下未停止的动画，表现为"切页闪一下/停在半路"。
        # 动效任何环节出问题都降级为硬切，功能绝不受影响。
        try:
            if getattr(self, "_trans", None) is None:
                import transition as TRANS
                self._trans = TRANS.PageTransition(self.stack)
            self._trans.go(idx, "inform")
        except Exception:
            try:
                self.stack.setCurrentIndex(idx)
            except Exception:
                pass
        if key in self.nav_btns:
            for k, b in self.nav_btns.items():
                b.setChecked(k == key)
        # 切页时刷新对应数据
        if key == "task":
            self._task_refresh()
        elif key == "work":
            self._work_refresh()
        elif key == "auto":
            self._auto_refresh()
        elif key == "kb":
            self._kb_refresh()
        elif key == "mem":
            self._mem_refresh()
        elif key == "skill":
            self._skill_refresh()
        elif key == "team":
            self._team_refresh()
        elif key == "flow":
            # v0.30.17：进页面就把当前进度刷出来（以前它在右栏是"永远显示 0%"
            # 就是因为没人主动刷；现在切页即刷）。
            self._wp_render_panel()

    NAV_CSS = ("QListWidget{border:1px solid #e2e8f0;border-radius:10px;"
               "padding:4px;background:#fafbfc;}"
               "QListWidget::item{padding:6px;border-radius:6px;}"
               "QListWidget::item:selected{background:#e0f2fe;color:#0c4a6e;}")

    def _page_title(self, text):
        t = QLabel(text)
        t.setStyleSheet("font-size:15px;font-weight:bold;color:#0f172a")
        return t

    def _page_chat(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 10, 14, 8)
        lay.setSpacing(6)
        # 头部：小人 + 名字 / 心情 / 内心 —— v0.30.9 重排为**紧凑两行**。
        # 上一版把 名字/格言/心情/波形/内心 五行竖着堆、头像 78px，整个头部占了小半屏，
        # 与下面聊天区比例失衡（小志反馈"小人与整体比例不符""上部分不需要占这么大"）。
        # 现在：① 名字 + 格言 + 今日心情 同一行；② 内心**单行**（无滚动条）。
        self.pet_card = UT.TechCard()
        head = QHBoxLayout(self.pet_card)
        head.setContentsMargins(12, 8, 12, 8)
        head.setSpacing(10)
        from pet_avatar import PetAvatar
        # 78 → 52：头部整体压缩（52 × 1.40 = 72，正好装进 76px 的卡片）。
        # ⚠️ 别再想着把气泡那 0.28× 的高度压掉：实测**成年档本体就有 74px 高**，
        #    压高度会让它在聊天页变小（56 宽时 74 → 59），那是改坏了不是美化。
        #    想让它更小就调这个数字（宽度），别动 PetAvatar 的内部几何。
        self.avatar = PetAvatar(64)          # 52 → 64：小志反馈"还是小了"
        self.avatar.on_click = self._pet_clicked
        self.avatar.set_bubble(False)            # 聊天页不要头顶气泡（会顶出卡片外）
        head.addWidget(self.avatar, 0, Qt.AlignVCenter)
        vbox = QVBoxLayout()
        vbox.setSpacing(2)
        self.name_lbl = QLabel(self.cfg.get("name", "小U"))
        f = self.name_lbl.font()
        f.setPointSize(11)
        f.setBold(True)
        self.name_lbl.setFont(f)
        self.motto_lbl = QLabel("会成长、记得你、能干活")
        self.motto_lbl.setStyleSheet("color:#94a3b8;font-size:10px")
        self.mood_lbl = QLabel("")
        self.mood_lbl.setStyleSheet("color:#0ea5e9;font-size:11px;font-weight:bold")
        _trow = QHBoxLayout()
        _trow.setSpacing(8)
        _trow.addWidget(self.name_lbl)
        _trow.addWidget(self.motto_lbl)
        _trow.addStretch(1)
        vbox.addLayout(_trow)
        # v0.30.11 #3：今日心情 单独成行（不再与 名字/格言 挤同一行）
        self.mood_lbl.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        vbox.addWidget(self.mood_lbl)
        # v0.30.9：内心改用 **QLabel 单行** —— 原来用 QTextBrowser，它必然带滚动条
        # （小志反馈"内心等不要滚动条"）。单行放不下的部分**省略**，完整内容挂 tooltip：
        # 头部要的是"一眼看完"，细节不该在这里铺开。
        self.mind = QLabel("—")
        self.mind.setTextFormat(Qt.RichText)
        self.mind.setStyleSheet(
            "QLabel{color:#475569;font-size:11px;background:transparent;"
            "border-left:2px solid #bae6fd;padding-left:7px;}")
        self.mind.setFixedHeight(17)
        self.mind.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        vbox.addWidget(self.mind)
        head.addLayout(vbox, 1)
        # 语音三键：贴卡片右上（原来横排在窗口最右边，离小人很远、看不出是一组）
        self.speak_btn = QPushButton()
        self.speak_btn.setFixedHeight(26)
        self.speak_btn.setCursor(Qt.PointingHandCursor)
        self.speak_btn.setToolTip("自动朗读：开=每轮回复自动念出来；关=不朗读")
        self.speak_btn.clicked.connect(self._toggle_speak)
        # v0.27.3：暂停/继续朗读（保留进度）
        self.pause_btn = QPushButton("⏸ 暂停")
        self.pause_btn.setFixedHeight(26)
        self.pause_btn.setCursor(Qt.PointingHandCursor)
        self.pause_btn.setToolTip("暂停 / 继续 朗读（保留进度；换对话或点 ⏹ 会彻底停掉）")
        self.pause_btn.clicked.connect(self._toggle_pause_speak)
        # v0.27.3：停止朗读（保留"朗读:开"的前提下，把当前这句掐断）
        # ⚠️ 名字必须是 tts_stop_btn，不能再叫 stop_btn：v0.30.6 查出——
        #    输入区那颗「停止本轮」按钮**晚于**它赋给同一个名字，于是头部这颗
        #    从此没人认得；`_refresh_voice_btns()` 里改的 stop_btn 其实是输入区
        #    那颗（症状：朗读时输入区按钮莫名变红，而真正的朗读停止键永不变色）。
        self.tts_stop_btn = QPushButton("⏹ 停止")
        self.tts_stop_btn.setFixedHeight(26)
        self.tts_stop_btn.setCursor(Qt.PointingHandCursor)
        self.tts_stop_btn.setToolTip("立刻停止朗读（不影响下一句继续朗读）")
        self.tts_stop_btn.clicked.connect(self._stop_speak_now)
        brow = QHBoxLayout()
        brow.setSpacing(4)
        brow.addWidget(self.speak_btn)
        brow.addWidget(self.pause_btn)
        brow.addWidget(self.tts_stop_btn)
        rcol = QVBoxLayout()
        rcol.setSpacing(0)
        rcol.addStretch(1)
        rcol.addLayout(brow)
        rcol.addStretch(1)               # 上下各留一份伸缩 → 与头像垂直居中
        head.addLayout(rcol)
        lay.addWidget(self.pet_card)
        # v0.30.15：卡片高度**由小人反推**，不再写死。（小志反馈"聊天框中的小人应该
        # 与上部的高度对齐"。）实测：PetAvatar 用 `setFixedSize(size, size*1.40)`，
        # 64 宽 = **89 高**；而写死的 96 - 上下内边距 16 = 内高只有 **80** →
        # 小人被压掉 9px（下沿被切），看着就是"和上部没对齐"。
        # 现在让内高 == 小人高，小人的上下沿正好贴齐卡片内沿。
        # ⚠️ 别再来写死数字：改小人尺寸（`PetAvatar(64)`）时这里自动跟着走。
        _hm = head.contentsMargins()
        _ah = self.avatar.height() or int(64 * 1.40)
        self.pet_card.setFixedHeight(_ah + _hm.top() + _hm.bottom())
        self._refresh_voice_btns()
        # 聊天区
        # ⚠️ 必须用 ChatBrowser（见其文档）：原生 QTextBrowser 会在点锚点后
        #    `setSource(url)`，把整块聊天正文换成 "Cannot open pasm://copy/0" 错误页
        #    —— 真机表现就是"点复制后上面的文字全没了"。这里的子类把导航关掉。
        self.chat = ChatBrowser()
        self.chat.setOpenExternalLinks(False)
        self.chat.anchorClicked.connect(self._on_anchor)
        self.chat.setStyleSheet("QTextBrowser{border:1px solid #e2e8f0;border-radius:10px;"
                                "background:#ffffff;color:#0f172a;padding:6px;}")
        lay.addWidget(self.chat, 1)
        # 工种分栏（在聊天内容下方、输入框上方；按钮直接排列，不留组标题，
        # 一排排得下就一排——文档/创作分组的语义保留在悬停提示里）
        flow = FlowLayout(margin=0, hspacing=6, vspacing=6)
        for key, label in self._CHIP_BAR:
            main = (key == "chat")
            b = QPushButton(label)
            b.setCheckable(True)
            b.setFixedHeight(26)
            b.setCursor(Qt.PointingHandCursor)
            b.setToolTip(self._CHIP_HINT.get(key, ""))
            b.setStyleSheet(
                "QPushButton{border:1px solid %s;border-radius:13px;padding:0 13px;"
                "background:%s;color:%s;font-size:12px;font-weight:bold;}"
                "QPushButton:hover{background:%s;border-color:%s}"
                "QPushButton:checked{background:#0ea5e9;color:#fff;"
                "border-color:#0ea5e9;}"
                % (("#93c5fd", "#eff6ff", "#0369a1", "#dbeafe", "#60a5fa")
                   if main else ("#cbd5e1", "#f8fafc", "#475569", "#e2e8f0", "#94a3b8")))
            b.clicked.connect(lambda _=False, k=key: self._set_chip(k))
            flow.addWidget(b)
            self.chip_btns[key] = b
        lay.addLayout(flow)
        # 文档类的子格式不再占一行布局 —— 改由**弹出式展示框**承担
        # （见 `_open_docsuite`）：平时不占高度，点了才展开成卡片浮层。
        mhint = QLabel("")
        mhint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(mhint)
        self.chip_hint = mhint
        # 输入行（多行大输入框：Enter 发送 / Shift+Enter 换行）
        # v0.30.6：输入框在上、工具条在下（原来按钮竖排贴在输入框右边）
        row = QVBoxLayout()
        row.setSpacing(4)
        box = QVBoxLayout()
        box.setSpacing(0)
        self.input = ChatInput()
        self.input.setFixedHeight(76)
        self.input.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.input.submit.connect(self.send)
        self.input.slash_blocks = self._slash_blocks   # 斜杠快选数据源（动态取）
        self.input.slash_run = self._slash_run         # 选中的指令/技能 → 执行
        # v0.26.4 @引用：WorkBuddy 式——候选弹层 + 输入框上方引用条 chip
        self.input.at_blocks = self._at_blocks
        self.input.at_picked.connect(self._at_pick)
        self.input.textChanged.connect(self._at_sync)
        self.input.images_given.connect(self._on_img_given)   # v0.27 Phase B 看图
        self._pending_imgs = []         # [{"path","desc"}] 下条消息注入的图片理解
        self._pending_files = []        # v0.29 [{"path","text"}] 下条消息注入的附件正文
        self._copy_store = {}           # v0.30.6 「📋 复制」内容仓（token → 原文）
        self._right_open = True         # v0.30.6 右侧工作侧栏开合状态
        self._at_refs = []              # [(显示名, 引用写法)] 当前引用条目
        self.at_bar = QFrame()
        self.at_bar.setStyleSheet(
            "QFrame{background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;}")
        self._at_lay = QHBoxLayout(self.at_bar)
        self._at_lay.setContentsMargins(8, 3, 8, 3)
        self._at_lay.setSpacing(6)
        self._at_lay.addStretch(1)
        self.at_bar.setVisible(False)
        box.addWidget(self.at_bar)
        box.addWidget(self.input)
        keytip = QLabel("Enter 发送 · Shift+Enter 换行")
        keytip.setStyleSheet("color:#cbd5e1;font-size:10px;padding-right:2px;")
        row.addLayout(box)
        # 工具条（v0.30.6 重排）：左＝附件 / 语音，右＝提示 + 停止 / 发送。
        # 参考 DeepSeek / WorkBuddy 的输入区：按钮和输入框**同底**成一体，
        # 不再各占一角、高矮不一。
        tools = QHBoxLayout()
        tools.setSpacing(6)
        # v0.29：「＋」附件入口 —— 图片 / 文档 / 任意文件 / 截图。
        # 图片走原有的视觉理解通道；文档走 attachments 提文本进上下文。
        self.plus_btn = QPushButton("＋ 附件")
        self.plus_btn.setToolTip("添加附件：图片 / 文档（Word、PPT、Excel、PDF、文本）/ 任意文件 / 截图")
        self.plus_btn.setFixedHeight(30)
        self.plus_btn.setCursor(Qt.PointingHandCursor)
        self.plus_btn.setStyleSheet(
            "QPushButton{font-size:12px;font-weight:600;color:#0369a1;"
            "background:#f0f9ff;border:1px solid #bae6fd;border-radius:8px;padding:0 12px;}"
            "QPushButton:hover{background:#e0f2fe;border-color:#7dd3fc;}")
        self.plus_btn.clicked.connect(self._plus_menu)
        tools.addWidget(self.plus_btn)
        self.mic_btn = QPushButton("🎤 语音")
        self.mic_btn.setToolTip("点一下开始说话（有提示音），说完自动识别发送")
        self.mic_btn.setFixedHeight(30)
        self.mic_btn.setCursor(Qt.PointingHandCursor)
        self.mic_btn.setStyleSheet(
            "QPushButton{font-size:12px;color:#475569;background:#f8fafc;"
            "border:1px solid #e2e8f0;border-radius:8px;padding:0 12px;}"
            "QPushButton:hover{background:#eef2f7;border-color:#cbd5e1;}")
        self.mic_btn.clicked.connect(self._voice_input)
        tools.addWidget(self.mic_btn)
        tools.addStretch(1)
        tools.addWidget(keytip)
        # v0.17.5：思考中可随时「⏹ 停止」——立即恢复界面，可新建/切换/再发
        self.stop_btn = QPushButton("⏹ 停止")
        self.stop_btn.setToolTip("等得不耐烦了？点这里立即停止等待，界面马上恢复可用")
        self.stop_btn.setFixedHeight(30)
        self.stop_btn.setMinimumWidth(74)
        self.stop_btn.setCursor(Qt.PointingHandCursor)
        self.stop_btn.setStyleSheet(
            "QPushButton{background:#fff1f0;color:#cf1322;border:1px solid #ffa39e;"
            "border-radius:8px;font-size:12px;}"
            "QPushButton:hover{background:#ffccc7;}")
        self.stop_btn.clicked.connect(self._stop_turn)
        self.stop_btn.setVisible(False)
        tools.addWidget(self.stop_btn)
        self.send_btn = QPushButton("发送")
        self.send_btn.setFixedHeight(30)
        self.send_btn.setMinimumWidth(84)
        self.send_btn.setCursor(Qt.PointingHandCursor)
        self.send_btn.setStyleSheet(
            "QPushButton{background:#0ea5e9;color:#ffffff;border:none;"
            "border-radius:8px;font-size:13px;font-weight:bold;}"
            "QPushButton:hover{background:#0284c7;}"
            "QPushButton:disabled{background:#cbd5e1;color:#f8fafc;}")
        self.send_btn.clicked.connect(self.send)
        tools.addWidget(self.send_btn)
        row.addLayout(tools)
        lay.addLayout(row)
        # 底部：模型状态（与切换下拉放同一行）
        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.status = QLabel("")
        self.status.setStyleSheet("color:#64748b;font-size:12px")
        foot.addWidget(self.status, 1)
        mdlab = QLabel("大脑")
        mdlab.setStyleSheet("color:#94a3b8;font-size:11px")
        foot.addWidget(mdlab)
        self.model_combo = QComboBox()
        self.model_combo.setFixedHeight(26)
        self.model_combo.setMinimumWidth(210)
        self.model_combo.setToolTip("切换大脑模型：云端 / 本地 Ollama / 自动选择")
        self.model_combo.activated.connect(self._on_model_pick)
        foot.addWidget(self.model_combo)
        lay.addLayout(foot)
        self._sync_mode_ui()
        return page

    _CHIP_LABEL = dict(WORK_CHIPS)
    _CHIP_HINT = {
        "chat": "聊天模式：提到'项目/网站/脚本'也不会擅自动手；想干活就点上排的工种",
        "docsuite": "文档与演示：文案 / 表格 / PPT / Word 四种格式收在一个入口，"
                    "点一下展开选格式（说「给我个 Word」这类明确要求也会直接走）",
        "copy": "文案模式：直接说写什么（广告语/种草文案/口播稿…），回车即出稿",
        "xls": "表格模式：直接说要统计什么数据，生成 Excel 文件",
        "ppt": "PPT 模式：直接说主题，生成演示文稿",
        "doc": "Word 模式：直接说文档主题，生成 Word 文件",
        "project": "开发模式：直接说要做什么系统/网站/应用，生成完整项目",
        "video": "视频模式：说主题直接出「图文配音短片」成片（脚本→分镜图→配音→播放器/MP4），首次使用会先让你接入出图引擎",
        "image": "图像模式：描述想要的画面，直接调用你接入的出图引擎生成图片（首次会先让你配置引擎，之后免配置）",
        "ad": "广告设计模式（专业流程）：说清「产品 + 卖点 + 风格」→ 自动产出"
              "① 标题/卖点/行动号召文案 ② 主视觉提示词 ③ 真出图；"
              "每版都记进工作面板，可按目录复盘、可续做下一步",
        "manga": "漫剧模式（分步·专业流程）：①给题材先出「企划分镜」→ ②满意说「出图」、想改说「第2镜改成…」→ ③「配音」→ ④「合成」出成片；不想分步就说「一条龙」",
    }

    def _sync_mode_ui(self):
        """按当前 chip 刷新分栏选中态、输入框占位与提示行（无副作用）。"""
        if hasattr(self, "chip_btns"):
            _act = self._active_chip_key()
            for k, b in self.chip_btns.items():
                b.setChecked(_act == k)
        if hasattr(self, "input"):
            if self._chip:
                self.input.setPlaceholderText(
                    f"「{self._CHIP_LABEL.get(self._chip, '')}」模式：直接给要求，回车就干")
            elif self.mode == "work":
                self.input.setPlaceholderText(
                    "干活模式：直接说要做什么，例如「帮我开发一个记账应用」「写个 python 脚本…」")
            else:
                self.input.setPlaceholderText(
                    "和它说点什么…（聊天模式不自动开工；读文件/记待办等轻活可直接说）")
        if hasattr(self, "chip_hint"):
            key = self._active_chip_key()
            self.chip_hint.setText(self._CHIP_HINT.get(key, ""))

    def _active_chip_key(self):
        """UI 上应高亮的主按钮键（无具体工种时不高亮任何 chip）。

        栏目整合后，文档类 4 个工种共用「📄 文档与演示」一个入口，
        所以它们统一返回**容器键** docsuite；具体是哪种格式由子标签行体现
        （见 `_active_sub_key`）。内部 `_chip` 依旧是 copy/xls/ppt/doc，
        会话槽位与开工种子句完全不受影响。
        """
        if self._chip:
            return "docsuite" if self._chip in self._DOC_SUITE_KEYS else self._chip
        return "chat" if self.mode == "chat" else None

    def _active_sub_key(self):
        """当前选中的文档格式；不在文档类工种时返回 None（浮层不高亮任何卡片）。"""
        return self._chip if self._chip in self._DOC_SUITE_KEYS else None

    def _open_docsuite(self, quiet: bool = False):
        """在「文档与演示」按钮下方弹出格式展示框（不占布局高度）。

        面板复用（一个窗口只建一个）：每次新建都重新连信号、重新排布，
        连点几次会闪。面板自身用 `Qt.Popup`，点外部自动关，不用管关闭逻辑。

        面板任何异常都不能让"点了没反应" —— 兜底直接落到 Word。
        """
        btn = self.chip_btns.get("docsuite")
        try:
            import docsuite as DS
            if getattr(self, "_ds_panel", None) is None:
                # 不传 parent：Qt.Popup 是独立顶层窗口，挂到主窗口下反而
                # 会被布局/父子删除逻辑牵扯；生命周期由 self._ds_panel 自己持有
                # （一个窗口只建一个，复用）。
                self._ds_panel = DS.DocSuitePanel(self._DOC_SUITE_LABELS)
                # 选中即走正常切栏目流程（会话槽位/种子句/提示全部照旧）
                self._ds_panel.picked.connect(self._set_chip)
            gp = (btn.mapToGlobal(QPoint(0, btn.height() + 4)) if btn is not None
                  else QPoint(200, 200))
            self._ds_panel.popup_at(gp, current=self._active_sub_key(),
                                    anchor_width=btn.width() if btn is not None else 0)
        except Exception as e:
            # 留痕：否则"点了没弹出"只能靠猜
            try:
                self._perm_audit("docsuite-panel", "error: %s" % e, "low")
            except Exception:
                pass
            self._set_chip("doc", quiet)

    def _set_chip(self, key: str, quiet: bool = False):
        """点工种分栏：切到该栏目自己的对话线程（信息框一起换，别的栏目记录不残留）。

        chat→聊天模式；work→通用干活；其余→锁定该工种直接干。
        """
        if key == "docsuite":
            # 「📄 文档与演示」是 UI 容器、本身不是工种：弹出格式展示框。
            return self._open_docsuite(quiet)
        if key not in dict(self.WORK_CHIPS) and key not in ("chat", "work"):
            return
        if key == self._slot_key():
            self._sync_mode_ui()
            self._sync_turn_ui()
            return
        # v0.17.5：切换栏目不再被思考中的回合拦截——别的栏目各自独立；
        # 若当前栏目本来有回合在跑，切走即"让位"，回复会收进它原属的会话。
        self._detach_turn()
        # 切 UI 高亮与占位（无副作用）
        if key == "chat":
            self._chip, self.mode = None, "chat"
        elif key == "work":
            self._chip, self.mode = None, "work"
        else:
            self._chip, self.mode = key, "work"
        self.cfg["chat_mode"] = key
        _save_json(CONFIG, self.cfg)
        self._sync_mode_ui()
        # 换对话信息框：载入该栏目自己的线程（上次没聊完的）；没有就空的新对话
        self._enter_slot(key, quiet)
        self._sync_turn_ui()
        if key != "chat" and hasattr(self, "input"):
            self.input.setFocus()

    def _enter_slot(self, key: str, quiet: bool):
        """切到 key 栏目的对话线程：载入该栏目上次的会话；没有则空对话。"""
        try:
            self._auto_episode()                 # 离开前把当前这段沉淀成长期记忆（不丢）
        except Exception:
            pass
        # 切栏目/开新对话前先把打字机停掉：否则上一栏目的回复会继续
        # 往**新栏目**的聊天区写（_patch_last_block 是按"最后一块"定位的）。
        # 补完的内容马上会被下面的 clear() 清掉，这里只需要它顺手停 timer。
        try:
            self._finish_reveal()
        except Exception:
            logging.exception("finish_reveal on enter_slot failed")
        self.chat.clear()
        cid = self._slot_conv(key)
        if cid:
            self._load_conv_into(cid)
            self.cog = COG.CogState(persona=self.cfg.get("persona", "温和沉稳"))
            self._episode_turn = 0
            self._replay_history()
        else:
            self.history, self.conv_id = [], None
            self._ses_img_dir = self._ses_story_dir = None
            self._ses_story = None
            self._ses_img_prompt = self._ses_img_req = None
            self._ses_proj_dir = None
        # v0.18.0：切栏目不再弹提醒（UI 高亮已说明一切）——聊天/工作互不打扰

    # 兼容旧引用（历史按钮语义）
    def _set_mode(self, m: str):
        self._set_chip(m)

    # ---------- 大模型切换（云端 / 本地 Ollama / 自动） ----------
    def _model_label(self) -> str:
        """当前实际生效的"大脑"文字描述。"""
        try:
            ep = self._endpoint()
        except Exception:
            ep = None
        if not ep:
            return "离线微脑"
        base, model, ak = ep
        if ak:
            return f"云端 {model}"
        if "11434" in (base or ""):
            return f"本地 {model}（Ollama）"
        return f"本地 {model}"

    def _reload_model_combo(self):
        if not hasattr(self, "model_combo"):
            return
        cb = self.model_combo
        key = self.cfg.get("model_choice", "auto")
        cb.blockSignals(True)
        cb.clear()
        cb.addItem("🔄 自动选择", "auto")
        if self.cfg.get("api_key"):
            cb.addItem(f"☁️ 云端 {self.cfg['model']}", "cloud")
        loc = self.local or detect_local_llm()
        if loc:
            for m in (list_ollama_models() or []):
                cb.addItem("🤖 " + m, "local:" + m)
        found = False
        for i in range(cb.count()):
            if cb.itemData(i) == key:
                cb.setCurrentIndex(i)
                found = True
                break
        if not found:
            cb.setCurrentIndex(0)
        cb.blockSignals(False)

    def _on_model_pick(self, idx: int):
        key = self.model_combo.itemData(idx) or "auto"
        self.cfg["model_choice"] = key
        if key == "auto" or key == "cloud":
            self.cfg.pop("model_locked", None)
        elif key.startswith("local:"):
            self.cfg["local_model"] = key[len("local:"):]
            self.cfg["model_locked"] = True
        _save_json(CONFIG, self.cfg)
        self._refresh_model_status()
        # v0.17.3：切大脑用独立弹框提示，绝不写聊天框、也不在聊天区上浮层
        # （底部状态行会一直显示当前生效的大脑）
        try:
            QMessageBox.information(
                self, "大脑已切换", f"当前大脑：{self._model_label()}\n\n"
                "在底部「大脑」下拉可随时再切换（云端 / 本地模型 / 自动）。")
        except Exception:
            pass
        # 本地大模型后台预热，避免首条回复慢
        if key.startswith("local:"):
            try:
                self._warm_local(key[len("local:"):])
            except Exception:
                pass

    def _refresh_model_status(self):
        self._reload_model_combo()
        try:
            self._set_status(self._model_label())
        except Exception:
            pass

    def _endpoint(self):
        """按 model_choice 返回当前可用模型三元组 (base_url, model, api_key)；无可用则 None。

        auto：有云端 Key → 云端；否则本地最小已装模型；再否则 None（走离线微脑）。
        cloud：固定云端（Key 缺失时自动退回 auto 规则）。
        local:<name>：固定某个已装本地模型（不在线/未装时自动退回 auto 规则）。
        """
        cfg = self.cfg
        key = cfg.get("api_key") or ""
        choice = cfg.get("model_choice", "auto")
        if choice == "cloud" and key:
            return (cfg["base_url"], cfg["model"], key)
        if choice.startswith("local:"):
            name = choice[len("local:"):]
            loc = self.local or detect_local_llm()
            if loc:
                if name in list_ollama_models():
                    return (loc["base_url"], name, "")
                logging.warning("本地模型 %s 当前不可用，回退自动选择", name)
        if key:
            return (cfg["base_url"], cfg["model"], key)
        loc = self.local or detect_local_llm()
        if loc:
            self.local = loc
            return (loc["base_url"], self._valid_local_model(), "")
        return None

    _DIRECT_ORDER_RE = re.compile(
        r"^\s*(?:帮我?|请|麻烦|劳驾|给我|我要|我想|能不能|可以帮|来|开始|开工|直接|立刻|马上|"
        r"写|做|画|生成|出|开发|制作|整理|翻译|总结|分析|设计|编排|编|拟|起草|排|算|列|爬|抓)"
        r"[^，。！？]{0,30}")

    def _is_direct_order(self, text: str) -> bool:
        """是否是明确的开工指令（而非只是聊到相关词）。

        v0.18.0：聊天模式去掉"要不要开工"提示卡后的分流依据——
        命中 → 直接开工不打扰；不命中 → 纯聊天。
        判据：句首出现引导词（帮我/请/给我/我要…）或干活动词（写/做/画/生成/开发…），
        且句子较短（真正的指令很少超过 60 字）。
        """
        s = (text or "").strip()
        if not s or len(s) > 60:
            return False
        return bool(self._DIRECT_ORDER_RE.match(s))

    # ---- v0.31.2 开工指令判定（不设 60 字上限）----
    #: 开工动词（"要我动手做东西"）
    _ORDER_VERB = (r"(?:开发|做一个|做个|做一?款|做一?套|搭一?个|搭个|建一?个|建个|"
                   r"写一?个|写个|写一?份|设计一?个|生成一?个|搞一?个|整一?个|"
                   r"来一?个|做|写|生成)")
    #: 句首/句中引导词（有它才算"对我下的命令"，防叙述句被误判）
    _ORDER_LEAD = (r"(?:帮我?|给我|替我|请|麻烦|拜托|劳驾|我想|我要|想要|"
                   r"能不能|可不可以|可以帮|你会不会|会不会)")
    #: 软件形态产物（→ 开发项目）。**刻意不含**脚本/代码/程序（那些归"写代码"工种）
    _SOFT_GOODS = (r"(?:网站|官网|网页应用|网页|系统|平台|小程序|前后端|全栈|后台|"
                   r"管理工具|工具软件|应用软件|应用|工具|软件|博客|商城|留言板|"
                   r"管理系统|登录系统|计算器|画板|小游戏|机器人|客户端|页面|"
                   r"数据库|服务端|前端|记账|待办|项目)")
    #: 全部产物形态（→ 确认墙用，避免长需求被静默降级成聊天）
    _ANY_GOODS = _SOFT_GOODS[:-1] + (
        r"|文档|报告|方案|总结|说明|材料|纪要|清单|文稿|笔记|Word|word|PPT|ppt|"
        r"Excel|excel|表格|脚本|代码|程序|图片|图像|视频|短片|漫剧|海报)")

    @staticmethod
    def _order_narrative(text: str) -> bool:
        """叙述句守卫：转述他人 / 过去式自述 / 求推荐求搜索 —— 都不是对我下的命令。"""
        t = text or ""
        if re.search(r"(?:今天|昨天|刚才|之前|上次|以前|刚刚)[^，。！？]{0,18}?"
                     r"(?:我)?[^，。！？]{0,6}(?:开发|做|搭|建|写|生成)"
                     r"[^，。！？]{0,4}(?:了|过|完)", t):
            return True
        if re.search(r"(?:让|叫|请|托)(?:它|他|她|别人|电脑|系统|同事|朋友)"
                     r"[^，。！？]{0,14}(?:开发|做|搭|建|写|生成)", t):
            return True
        # 「帮[我]找/搜/推荐/介绍/下载/安装…」是在**要东西/要答案**，不是要我造软件。
        # 反例对照（必须抓住）：「帮我找一个开发工具」里的"开发"是名词的一部分，
        # 旧写法会把它当开工动词 → 误判成开发项目。
        if re.search(r"(?:帮我?|给我|替我)(?:找|搜|查一?下|推荐|介绍|下载|安装|了解|看看)", t):
            return True
        if re.search(r"怎么|如何|怎样|为什么|为何|是什么|啥意思", t):
            return True
        return False

    def _order_hit(self, text: str, goods: str) -> bool:
        """「引导词/句首动词 + 产物」是否同时成立（形态判定，供上面两条规则复用）。

        为什么窗口放到 40 字且**不许跨标点**：真机事故里
        「帮我开发一个能自动抓取价格、对比历史最低价、并在降价时提醒我的比价系统」
        在旧的 `[^，。！？!?\n]{0,22}?` 窗口下 **0 命中** → 掉进普通聊天且无提示。
        需求写得越具体反而越不可能开工，这是反的。
        """
        s = (text or "").strip()
        if not s or len(s) > 200 or self._order_narrative(s):
            return False
        # 形态①：有引导词 —— 引导词 → 10 字内动词 → 40 字内产物
        if re.search(self._ORDER_LEAD + r"[^，。！？!?\n]{0,10}?" + self._ORDER_VERB +
                     r"[^，。！？!?\n]{0,40}?" + goods, s):
            return True
        # 形态②：句首即动词（祈使句可无引导词）—— "开发一个记账系统"
        if re.match(r"^\s*" + self._ORDER_VERB + r"[^，。！？!?\n]{0,40}?" + goods, s):
            return True
        # 形态③：「开发 + 一个/一款/一套/个 + 自定名」——产物名认不出来也算。
        #   "帮我开发一个记账本" 里的"记账本"是用户自造的名字，任何词表都盖不全；
        #   而"开发 + 量词"本身就是软件开工的强信号。**只认"开发"**，不认"做/写" ——
        #   否则「帮我做个海报」（该出走图）会被误抢成开发项目。
        if re.search(self._ORDER_LEAD + r"[^，。！？!?\n]{0,6}?开发"
                     r"[^，。！？!?\n]{0,6}?(?:一个|一款|一套|一版|个)"
                     r"[\u4e00-\u9fa5A-Za-z]{2,}", s):
            return True
        return False

    def _looks_like_build_order(self, text: str) -> bool:
        """是不是"要我做一个**软件产品**"的开工指令（→ 直通 project）。

        为什么必须单独有这道**高优先级早守卫**（小志 2026-09-23 真机反馈
        「我让桌面应用帮我开发时，为什么没有任何动作」）—— 实测三条死路：
          ① 含"能不能/你会不会" → 先被 `_cap_question` 抢成 `askhelp`，
             只回一段能力菜单，**不写任何文件**；
          ② 「帮我开发一个数据分析平台」先被 `tableana` 抢走、
             「帮我开发一个微信小程序」先被 `remote_push` 抢走 ——
             这两条规则在 `_detect_agent` 里都**排在 project 之前**（先命中者胜）；
          ③ 长需求撑不住旧的 22 字窗口 → agent=None → 纯聊天。
        所以开发需求必须在 `_detect_agent` **最前面**就认领。
        """
        return self._order_hit(text, self._SOFT_GOODS)

    def _looks_like_order(self, text: str) -> bool:
        """任意产物形态的开工指令（含文档/PPT/表格/脚本/图片…）。

        只给"确认墙"用 —— 目的是别让 >60 字的具体需求被 `_is_direct_order`
        的 60 字上限**静默降级**成普通聊天（同样是"什么都没发生"）。
        """
        return self._order_hit(text, self._ANY_GOODS)

    def _strong_order(self, text: str) -> bool:
        """本轮算不算"明确开工指令"（v0.31.2：把开发类的 60 字上限拿掉）。"""
        return bool(self._is_direct_order(text) or self._looks_like_order(text))

    def _imperative(self, text: str) -> bool:
        """v0.27.2 串词守卫：这句话是不是**对我下的命令**？

        真机痛点：聊天里提到"清理/删除/图片/病毒/搜索"等词，哪怕只是叙述
        （"我昨天清理了""让电脑自动清理"），也会被关键词路由劫持去真删真清。
        守卫规则——只有"命令句"才放行路由：
          ① ≤70 字（长句多为描述）；
          ② 转述他人 / 过去式自述 / 疑问探讨 → 一律当聊天；
          ③ 句首祈使引导词（帮我/请/把…）或句首即干活动词 → 命令；
          ④ 呼名下令（按设置里的角色名，如「小U，帮我…」）→ 命令。
        """
        t = (text or "").strip()
        if not t or len(t) > 70:
            return False
        # —— ② 叙述特征：一律当聊天，绝不劫持 ——
        # 转述他人/他物执行：我让电脑自动清理/小U帮我删了（是描述不是命令）
        if re.search(r"(?:让|叫|请|托)(?:它|他|她|别人|电脑|系统|同学|朋友|小[Uu伴])"
                     r"[^，。！？]{0,14}(?:删除|清理|删掉|搜索|体检|查杀|病毒)", t):
            return False
        # 过去式自述：我今天清理了 / 我刚才把图片删了
        if re.search(r"(?:今天|昨天|刚才|之前|上次|以前|刚刚)[^，。！？]{0,18}?"
                     r"(?:我)?[^，。！？]{0,6}(?:删|清|搜|查|体检)[^，。！？]{0,4}"
                     r"(?:了|过|掉|除)", t):
            return False
        # 疑问/探讨：…好吗 / 怎么删 / 要不要清理 / 为什么病毒…
        if re.search(r"(?:删|清|搜|查|体检|病毒|中毒|垃圾|缓存|浏览器|文件夹|图片)"
                     r"[^，。！？]{0,12}(?:好不好|怎么样|是什么|咋样|行不行|可以吗|"
                     r"该怎么|为何|为什么|会不会|能不能|是否|怎么办)", t) or \
           re.search(r"(?:为什么|为何|怎么才能|要不要|该不该|是不是|如何)[^，。]{0,16}"
                     r"(?:删|清|搜|查|病毒|中毒|垃圾|缓存|体检|图片)", t):
            return False
        # —— ③ 句首祈使 ——
        if re.match(r"^\s*(?:帮我?|给我|替我|请|麻烦|拜托|劳驾|把|去|快|赶紧|马上|"
                    r"立刻|继续|接着|再|帮我查|帮我删|帮我清|帮我搜)", t):
            return True
        # —— ③ 句首即干活动词（祈使句可无引导词："删除桌面上的XX"）——
        if re.match(r"^\s*(?:删除|删掉|删了|移除|清掉|清除|清理|打开浏览器|用浏览器|"
                    r"在?(?:网上|浏览器|百度|必应|谷歌)[里上]?|搜索|查一?下|查查|"
                    r"安全体检|杀毒|查杀|体检|看看电脑)", t):
            return True
        # —— ④ 呼名下令 ——
        if re.match(r"^\s*(?:小[Uu伴]|霖伴|宝宝)[，,！!]?\s*(?:帮我|给我|把|去)?"
                    r"(?:删|清|搜|查|体检|打开浏览器)", t):
            return True
        if re.search(r"(?:你|小[Uu伴]|霖伴)(?:能|可以)?(?:帮我|给我|把|去|删|清|搜|查|体检)",
                     t) and re.match(r"^\s*(?:你|小[Uu伴]|霖伴)", t):
            return True
        return False

    # v0.27.3 搜索词抽取（真机实锤修复）
    #   旧版正则里 `[^，。！？\n]{0,6}?` 禁止跨逗号，于是
    #   「帮我打开浏览器，并搜索'诛仙'」里"浏览器"和"搜索"中间隔了个逗号 →
    #   匹配失败 → 整句话被当成搜索词，浏览器打开的是一长串废话。
    _SEARCH_VERB_RE = re.compile(
        r"(?:检索|搜索一下|搜索|搜一下|搜|查找|查一下|查查|查|找一找|找)")

    def _extract_search_query(self, text: str) -> str:
        """从"帮我打开浏览器，并搜索'诛仙'"里准确抽出 `诛仙`。

        三条递进规则：
          ① 引号/书名号里的内容优先（用户显式包起来的就是要搜的）；
          ② 否则取**最后一个**搜索动词之后的内容（"并搜索 X"→X）；
          ③ 再把残留的"浏览器/网页/帮我/并/然后"等零碎前缀剥干净。
        """
        t = (text or "").strip()
        if not t:
            return ""
        # ① 引号优先 —— 中文引号 / 直角 / 书名号 / 英文引号 全支持
        m = re.search(
            r"[「『“\"'‘'《<【]([^」』”\"'’'》>】]{1,60})[」』”\"'’'》>】]", t)
        if m and m.group(1).strip():
            return m.group(1).strip()
        # ② 最后一个搜索动词之后的内容
        s = re.sub(r"^\s*(?:帮我|给我|替我|请|麻烦|麻烦你|能不能|可以)\s*", "", t)
        hits = list(self._SEARCH_VERB_RE.finditer(s))
        if hits:
            q = s[hits[-1].end():]
        else:
            q = s
        # ③ 剥残留零碎（连接词 / 引导词 / 渠道名 / 介词），可反复剥
        q = q.strip(" ，。！？：:、\t")
        for _ in range(4):
            before = q
            q = re.sub(
                r"^(?:并|然后|再|接着|顺便|帮我|给我|替我|请|麻烦|去|来|一下|"
                r"打开|启动|用|在|上|系统|默认|的)+", "", q, flags=re.I).strip()
            q = re.sub(
                r"^(?:浏览器|网页|网上|互联网|百度|必应|谷歌|bing|google)"
                r"(?:里|上|中|里面)?", "", q, flags=re.I).strip()
            q = q.strip(" ，。！？：:、\t")
            if q == before:
                break
        # 若动词后面啥也没有（"帮我搜一下"），退回剥掉引导词的原句
        if not q:
            q = re.sub(
                r"^(?:帮我|给我|替我|请|麻烦)?\s*(?:打开|启动|用)?(?:一下)?"
                r"(?:系统)?(?:默认)?(?:的)?(?:浏览器|网页|网上|互联网)?"
                r"(?:里|上|中)?(?:并|然后|再|接着)?", "", s, flags=re.I).strip()
            q = self._SEARCH_VERB_RE.sub("", q).strip(" ，。！？：:、")
        return q.strip(" ，。！？：:、")

    def _askish(self, text: str) -> bool:
        """v0.27.2 附加守卫：想要"推荐/方法/哪个好"的知识类提问，
        不是让我对这台电脑动手（例如"帮我推荐清理垃圾的软件"）。"""
        return bool(re.search(
            r"推荐|介绍|哪(?:款|个|些)|什么(?:软件|工具|方法|东西)|有没有.{0,8}"
            r"(?:软件|工具|办法|推荐)|教程|该用|用什么", (text or "")))

    def _chatmode_tip(self, kind: str) -> str:
        """聊天模式下命中开工类意图 → 提示 + 可点击的开工入口，绝不执行。"""
        label, anchor = _KIND_ANCHOR.get(kind, ("干活", "project"))
        return (f"你这句话听起来是想让我**开工**（{label}）？我现在在💬聊天模式，"
                f"不会因为提到这些字就擅自动手。\n\n"
                f"如果你确实要我做，点下面的入口——会自动切到🔧干活模式并先跟你确认范围：\n\n"
                f"[🔧 {label}](pasm://quick/{anchor})（会自动切到干活模式）")

    def _skill_tip(self, name: str) -> str:
        """聊天模式下命中技能 → 提示技能名与用途 + 可点开工入口。"""
        s = SKL.load(name)
        desc = s["description"] if s else "未知技能"
        return (f"这件事在我**技能库**里有对口技能：`{name}`（{desc}）。\n"
                f"我现在在💬聊天模式，不会擅自动手。点下面的入口会自动切到🔧干活模式，"
                f"按技能规程开始做：\n\n"
                f"[🎓 用技能开工](pasm://quick/skill)　[📚 查看/添加技能](pasm://skillmgmt)")

    # ---------- 任务页 ----------
    def _page_task(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self._page_title("✅ 任务 · 待办与提醒"))
        lay.addSpacing(6)
        self.task_list = QListWidget()
        self.task_list.setStyleSheet(self.NAV_CSS)
        self.task_list.itemDoubleClicked.connect(self._task_done)
        lay.addWidget(self.task_list, 1)
        hint = QLabel("双击任务 = 标记完成；时间写法：明天10点 / 14:30 / 后天9点30")
        hint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(hint)
        h = QHBoxLayout()
        self.task_edit = QLineEdit()
        self.task_edit.setPlaceholderText("添加任务，例：交周报（明天10点）")
        self.task_edit.setFixedHeight(30)
        self.task_edit.returnPressed.connect(self._task_add)
        addb = QPushButton("添加")
        addb.setFixedHeight(30)
        addb.clicked.connect(self._task_add)
        doneb = QPushButton("完成选中")
        doneb.setFixedHeight(30)
        doneb.clicked.connect(lambda: self._task_done(self.task_list.currentItem()))
        h.addWidget(self.task_edit, 1)
        h.addWidget(addb)
        h.addWidget(doneb)
        lay.addLayout(h)
        return page

    def _task_refresh(self):
        self.task_list.clear()
        items = todo_mod.open_items()
        for it in items:
            due = f"  ⏰ {it['due'][5:16]}" if it.get("due") else ""
            self.task_list.addItem(f"• {it.get('text','')}{due}")

    def _task_add(self):
        txt = self.task_edit.text().strip()
        if not txt:
            return
        due = todo_mod.parse_time(txt)
        body = txt
        if due:
            body = re.sub(r"(?:今天|明天|后天)?\s*\d{1,2}\s*[:：点时]\s*\d{0,2}分?",
                          "", txt).strip(" ，。：:") or txt
        todo_mod.add(body, due)
        self.task_edit.clear()
        self._task_refresh()

    def _task_done(self, item):
        if not item:
            return
        text = item.text().lstrip("• ").split("  ⏰")[0].strip()
        todo_mod.done(text)
        self._task_refresh()

    # ---------- 工作台页 ----------
    def _page_work(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        head.setSpacing(8)
        head.addWidget(self._page_title("🛠 工作台 · 它正在干的活"))
        head.addStretch(1)
        _tool = ("QPushButton{border:1px solid #d8e0ea;border-radius:13px;"
                 "background:#ffffff;color:#334155;font-size:12px;padding:0 14px;}"
                 "QPushButton:hover{border-color:#0ea5e9;color:#0284c7;background:#f0f9ff;}")
        rb = QPushButton("🔄 刷新")
        rb.setFixedHeight(_CatPill.H)
        rb.setCursor(Qt.PointingHandCursor)
        rb.setStyleSheet(_tool)
        rb.clicked.connect(self._work_refresh)
        ob = QPushButton("📂 打开工作空间")
        ob.setFixedHeight(_CatPill.H)
        ob.setCursor(Qt.PointingHandCursor)
        ob.setStyleSheet(_tool)
        ob.clicked.connect(self._work_open_dir)
        head.addWidget(rb)
        head.addWidget(ob)
        lay.addLayout(head)
        # —— 工作空间（脚本/项目都落在所选目录下）——
        wsrow = QHBoxLayout()
        wsrow.setSpacing(8)
        wslab = QLabel("📁 工作空间：")
        wslab.setStyleSheet("color:#475569;font-size:12px;font-weight:bold;")
        wsrow.addWidget(wslab)
        self.ws_lbl = QLabel("")
        self.ws_lbl.setStyleSheet("color:#64748b;font-size:12px")
        self.ws_lbl.setWordWrap(False)
        wsrow.addWidget(self.ws_lbl, 1)
        _mini = ("QPushButton{border:1px solid #d8e0ea;border-radius:11px;"
                 "background:#f8fafc;color:#334155;font-size:11px;padding:0 10px;}"
                 "QPushButton:hover{border-color:#0ea5e9;color:#0284c7;}")
        pb = QPushButton("选择…")
        pb.setFixedHeight(24)
        pb.setCursor(Qt.PointingHandCursor)
        pb.setStyleSheet(_mini)
        pb.setToolTip("选一个目录作为**工作根**。之后所有产物都放它下面、按类型分文件夹：\n"
                      "project 项目 / script 脚本 / image 图片 / video 视频 /\n"
                      "copy 文案 / doc 文档 / team 团队")
        pb.clicked.connect(self._ws_pick)
        rb2 = QPushButton("恢复默认")
        rb2.setFixedHeight(24)
        rb2.setCursor(Qt.PointingHandCursor)
        rb2.setStyleSheet(_mini)
        rb2.clicked.connect(self._ws_reset)
        wsrow.addWidget(pb)
        wsrow.addWidget(rb2)
        lay.addLayout(wsrow)
        # —— v0.30.11：工作根下的 7 个分类（产物一律按类型进对应文件夹）——
        self.cat_lbl = QLabel("")
        self.cat_lbl.setWordWrap(True)
        self.cat_lbl.setStyleSheet("color:#94a3b8;font-size:11px;padding-left:2px")
        lay.addWidget(self.cat_lbl)
        # —— 整理旧产物：老版本产物散在四处（数据目录 projects/scripts/works、
        #    team/runs、桌面\PASM创作）→ 收进工作根。做法"复制 → 校验 → 通过才删原"。
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        self.mig_lbl = QLabel("")
        self.mig_lbl.setStyleSheet("color:#b45309;font-size:11px")
        mrow.addWidget(self.mig_lbl, 1)
        self.mig_btn = QPushButton("🧹 整理旧产物")
        self.mig_btn.setFixedHeight(24)
        self.mig_btn.setCursor(Qt.PointingHandCursor)
        self.mig_btn.setStyleSheet(_mini)
        self.mig_btn.setToolTip(
            "把老位置散落的产物收进当前工作根，按类型分文件夹。\n"
            "做法：复制 → 逐项校验（大小 + sha256）→ 通过才删原位置。\n"
            "任一项校验不过就保留原件；全程写回滚清单。\n"
            "资料库笔记与团队角色配置不会被搬动（那是知识/配置，不是产物）。")
        self.mig_btn.clicked.connect(self._ws_migrate)
        mrow.addWidget(self.mig_btn)
        lay.addLayout(mrow)
        # —— 分类条（点分类筛选工作记录；＋ 新增；每个分类右上角「−」删除整个分类）——
        self._wf = getattr(self, "_wf", "全部")
        self._cat_row = FlowLayout(margin=0, hspacing=4, vspacing=4)
        lay.addLayout(self._cat_row)
        lay.addSpacing(4)
        self.work_list = QListWidget()
        self.work_list.setStyleSheet(self.NAV_CSS)
        self.work_list.itemDoubleClicked.connect(self._work_open_item)
        self.work_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.work_list.customContextMenuRequested.connect(self._work_menu)
        lay.addWidget(self.work_list, 1)
        hint = QLabel("右键条目 = 删除这条成果；右键空白处 = 删除当前分类全部内容。"
                      "对话里说「读一下 <路径>」「帮我写个xx脚本」「帮我开发一个xx」，"
                      "成果都会出现在这里并按类型分好类。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(hint)
        self._cats_rebuild()
        self._ws_labels()
        self._work_refresh()
        return page

    def _cats_rebuild(self):
        while self._cat_row.count():
            it = self._cat_row.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
        cats = self.cfg.get("work_cats") or ["全部"]
        for c in cats[:10]:
            pill = _CatPill(c, selected=(c == self._wf),
                            deletable=(c != "全部"),
                            on_pick=(lambda cc=c: self._set_cat(cc)),
                            on_del=(lambda cc: self._cat_del(cc)))
            self._cat_row.addWidget(pill)
        ab = QPushButton("＋ 分类")
        ab.setFixedHeight(_CatPill.H)
        ab.setCursor(Qt.PointingHandCursor)
        ab.setStyleSheet(
            "QPushButton{border:1px dashed #7dd3fc;border-radius:13px;"
            "background:#f0f9ff;color:#0284c7;font-size:11px;padding:0 13px;}"
            "QPushButton:hover{background:#e0f2fe;}")
        ab.clicked.connect(self._cat_add)
        self._cat_row.addWidget(ab)

    def _set_cat(self, cat: str):
        self._wf = cat
        self._cats_rebuild()
        self._work_refresh()

    def _cat_add(self):
        from qt_compat import QtWidgets
        name, ok = QtWidgets.QInputDialog.getText(self, "添加分类", "分类名称：")
        if ok and name.strip():
            cats = self.cfg.get("work_cats") or ["全部"]
            if name.strip() not in cats:
                cats.append(name.strip())
                self.cfg["work_cats"] = cats
                _save_json(CONFIG, self.cfg)
            self._set_cat(name.strip())

    def _cat_del(self, cat: str):
        """删除分类（右上角 −）：下方有内容时先确认，确认后连同其内容一并删除。"""
        if cat == "全部":
            self._append("系统", "「全部」是默认分类，不能删除；选一个自建分类再删。")
            return
        rows = [r for r in self._work_rows() if self._work_keep(r, cat)]
        if rows:
            from qt_compat import QtWidgets
            ret = QtWidgets.QMessageBox.question(
                self, "删除分类",
                f"分类「{cat}」下有 {len(rows)} 条工作成果，\n"
                f"删除分类会把这些成果（文件/项目）一并删除，确定吗？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if ret != QtWidgets.QMessageBox.Yes:
                return
        self._work_delete_rows(rows)
        cats = [c for c in (self.cfg.get("work_cats") or []) if c != cat]
        self.cfg["work_cats"] = cats or ["全部"]
        _save_json(CONFIG, self.cfg)
        self._wf = "全部"
        self._cats_rebuild()
        self._work_refresh()
        self._append("系统", f"已删除分类「{cat}」" + (f"及其 {len(rows)} 条成果。" if rows else "。"))

    def _ws_pending(self) -> list:
        """还留在老位置的产物（dry-run 计划，一个字节都不动）。"""
        try:
            import workspace as WS
            return WS.migrate(self.cfg, dry_run=True).get("plan") or []
        except Exception:  # noqa: BLE001
            return []

    def _ws_labels(self):
        """刷新工作根显示：真实路径 + 7 个分类 + 「整理旧产物」入口。

        以前这里显示的是"（默认：应用数据目录）"—— 而实际默认早就该是
        桌面\\PASM工作。显示真值，免得用户按提示去错地方翻文件。
        """
        try:
            import workspace as WS
            r = WS.root(self.cfg)
            own = bool((self.cfg.get("ws_dir") or "").strip())
            self.ws_lbl.setText("%s（%s）" % (r, "自定义" if own else "默认"))
            self.ws_lbl.setToolTip(r)
            self.cat_lbl.setText("按类型分：　" + "　".join(
                "%s %s/" % (WS.CAT_DESC[c][0], c) for c in WS.CATS))
            self.cat_lbl.setToolTip("\n".join(
                "%s（%s/）　%s" % (WS.CAT_DESC[c][0], c, WS.CAT_DESC[c][1])
                for c in WS.CATS))
        except Exception:  # noqa: BLE001
            logging.exception("ws labels failed")
            self.ws_lbl.setText(self.cfg.get("ws_dir") or "（默认）")
        try:
            n = len(self._ws_pending())
            self.mig_btn.setVisible(n > 0)
            self.mig_lbl.setText(("发现 %d 处老位置的产物可以整理进来" % n) if n else "")
        except Exception:  # noqa: BLE001
            pass

    def _ws_migrate(self, auto: bool = False):
        """把老位置散落的产物收进工作根（复制 → 校验 → 通过才删原；可回滚）。

        `auto=True` 是启动后的自动整理：**不弹窗**、只写一条日志，失败也静默
        —— 原件一律保留，不会丢东西。
        """
        from qt_compat import QtWidgets
        try:
            import workspace as WS
        except Exception as ex:  # noqa: BLE001
            if not auto:
                self._append("系统", "整理失败：%s" % ex)
            return
        plan = self._ws_pending()
        if not plan:
            if not auto:
                QtWidgets.QMessageBox.information(
                    self, "整理旧产物",
                    "没有需要整理的东西 —— 产物已经在工作根里了。")
            return
        if not auto:
            head = "\n".join("· %s　→　%s/" % (os.path.basename(m["src"]),
                                              m.get("cat") or "?")
                             for m in plan[:12])
            more = "\n…还有 %d 项" % (len(plan) - 12) if len(plan) > 12 else ""
            ret = QtWidgets.QMessageBox.question(
                self, "整理旧产物",
                "将把下面 %d 项收进工作根、按类型分文件夹：\n\n%s%s\n\n"
                "做法：复制 → 逐项校验（大小 + sha256）→ 通过才删原位置；\n"
                "任一项校验不过就保留原件并跳过；会话里的路径引用会同步改写。\n"
                "资料库笔记、团队角色配置不会被搬动。\n\n要现在整理吗？"
                % (len(plan), head, more),
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.Yes)
            if ret != QtWidgets.QMessageBox.Yes:
                return
        r = WS.migrate(self.cfg)
        if auto and not r["moved"]:
            return
        msg = "✅ 已把 %d 项旧产物整理进工作根" % len(r["moved"])
        if (r.get("refs") or {}).get("changed"):
            msg += "，%d 个会话的路径引用已同步" % r["refs"]["changed"]
        if r["skipped"]:
            msg += "；%d 项目标已存在、跳过（原件保留）" % len(r["skipped"])
        if r["failed"]:
            msg += "；⚠️ %d 项失败（**原件保留**）：%s" % (
                len(r["failed"]), r["failed"][0].get("reason", ""))
        if r.get("manifest"):
            msg += "\n可回滚（清单）：%s" % r["manifest"]
        self._append("系统", msg)
        self._ws_labels()
        self._work_refresh()

    def _ws_auto_migrate(self):
        """启动后自动整理老产物（只做一次；之后设置页可随时手动再跑）。

        在工作根留 `.migrated` 标记 → 不重复动用户的目录。任何异常都吞掉：
        开机路径绝不能被它拖垮，而这件小事失败也没有任何破坏性（原件保留）。
        """
        try:
            import workspace as WS
            r = WS.root(self.cfg)
            mark = os.path.join(r, ".migrated")
            if os.path.exists(mark):
                return
            if self._ws_pending():
                self._ws_migrate(auto=True)
            with open(mark, "w", encoding="utf-8") as f:
                f.write(str(int(time.time())))
        except Exception:  # noqa: BLE001
            logging.exception("ws auto migrate skipped")

    def _ws_pick(self):
        from qt_compat import QtWidgets
        d = QtWidgets.QFileDialog.getExistingDirectory(self, "选择工作空间目录")
        if d:
            self.cfg["ws_dir"] = d
            _save_json(CONFIG, self.cfg)
            AT.set_workspace(d)
            self._ws_labels()
            self._work_refresh()
            self._append("系统", "工作根已切换到：" + d +
                         "（以后的项目/图片/视频/文案/文档/团队/脚本都放这里，按类型分文件夹）")

    def _ws_reset(self):
        self.cfg["ws_dir"] = ""
        _save_json(CONFIG, self.cfg)
        AT.set_workspace(None)
        self._ws_labels()
        self._work_refresh()
        self._append("系统", "已恢复默认工作根（桌面\\PASM工作）。")

    # ---------- v0.27.4 工作任务（聊天 ↔ 工作关联） ----------
    def _work_open(self, text: str, agent) -> str:
        """一次聊天干活 → 记一条工作任务，返回任务 id（失败返回空串）。"""
        if not WORKLOG:
            return ""
        try:
            kind = agent[0] if agent else "general"
            t = WORKLOG.create(text or "工作", kind, getattr(self, "conv_id", ""))
            return t.get("id", "")
        except Exception:
            return ""

    def _work_close(self, tid: str, reply: str, ok: bool = True):
        """任务收尾：写状态 + 提产物路径 + 通知工作台/桌面小人刷新。"""
        if not WORKLOG or not tid:
            return
        try:
            WORKLOG.finish(tid, reply or "", ok=ok)
        except Exception:
            pass

        def _upd():
            try:
                if hasattr(self, "work_list"):
                    self._work_refresh()
            except Exception:
                pass
            try:
                self._sync_pet_work()
            except Exception:
                pass
        try:
            self._ui(_upd)
        except Exception:
            pass

    def _sync_pet_work(self):
        """把「当前正在做的工作」同步给桌面小人显示（无则清空）。"""
        if not hasattr(self, "avatar"):
            return
        try:
            label = WORKLOG.current_label() if WORKLOG else ""
        except Exception:
            label = ""
        try:
            self.avatar.set_work(label)
        except Exception:
            pass

    def _work_rows(self):
        """工作台全部记录行（最近在前）：工作任务 / 创作产出 / 已读文件 / 脚本 / 项目。

        每次刷新都和磁盘对账：已读文件、项目/创作目录若已被删除（含你在电脑里手动删），
        对应条目自动消失，不留"僵尸记录"。
        """
        rows = []
        # ⓪ 工作任务（v0.27.4）：一等实体，带状态/进度；双击进"工作详情"就地讨论
        try:
            if WORKLOG:
                for t in WORKLOG.recent(12):
                    icon = {"running": "🔄", "done": "✅", "failed": "❌"}.get(
                        t.get("status"), "🛠")
                    rows.append(
                        f"{icon} 任务#{t['id']}  {WORKLOG.kind_label(t['kind'])}  "
                        f"{t.get('title', '')}  ·  {t.get('progress', '')}")
        except Exception:
            pass
        # ⓪ 创作产出（v0.18.0）：出图/短片/漫剧的成品目录，按最近修改排序。
        #    此前只列脚本/项目，图片视频"生成完就不见"——现在直接在工作台呈现。
        try:
            import workspace as WS
            # v0.30.11：这里的一级目录不再是"工种"，而是**7 个分类**
            # （project/script/image/video/copy/doc/team）。分类名与中文名统一
            # 取自 workspace（唯一来源），本处只负责配图标。
            _ICO = {"project": "🧱", "script": "🧾", "image": "🎨", "video": "🎬",
                    "copy": "✍️", "doc": "📄", "team": "👥"}
            root = CRE.pick_out_root(self.cfg)
            subs = []
            for cat in sorted(os.listdir(root)):
                cpath = os.path.join(root, cat)
                if not os.path.isdir(cpath) or cat not in WS.CATS:
                    continue
                for d in os.listdir(cpath):
                    dpath = os.path.join(cpath, d)
                    if os.path.isdir(dpath):
                        try:
                            subs.append((os.path.getmtime(dpath), dpath, cat))
                        except OSError:
                            continue
            subs.sort(reverse=True)
            for _mt, dpath, cat in subs[:14]:
                rows.append("%s %s  %s" % (_ICO.get(cat, "📁"),
                                           WS.CAT_DESC.get(cat, (cat,))[0], dpath))
        except Exception:
            pass
        # ① 已读文件：源文件不存在 → 只清记录，不显示
        gone = [p for p in list(self.work_ctx) if not os.path.exists(p)]
        for g in gone:
            self.work_ctx.pop(g, None)
        for pth in list(self.work_ctx)[-8:]:
            rows.append(f"📄 已读文件  {pth}")
        # ② 脚本：直接列磁盘真实文件（天然与磁盘一致）
        try:
            for fn in sorted(os.listdir(AT.SCRIPTS_DIR), reverse=True)[:12]:
                rows.append(f"🧾 脚本  {os.path.join(AT.SCRIPTS_DIR, fn)}")
        except Exception:
            pass
        # ③ 项目：目录已不存在 → 移除记录（磁盘已删，界面不该再显示）
        proj_path = os.path.join(DATA_DIR, "projects.json")
        projs = _load_json(proj_path, [])
        alive, pruned = [], 0
        for p in projs:
            d = (p.get("dir") or "").strip()
            if d and not os.path.isdir(d):
                pruned += 1
                continue
            alive.append(p)
        if pruned:
            _save_json(proj_path, alive)
        for p in alive[:10]:
            rows.append(f"📦 项目  {p.get('name','')}  ·  {p.get('t','')}  ·  "
                        f"{p.get('dir','')}")
        return rows

    @staticmethod
    def _work_keep(line, wf):
        if wf == "全部":
            return True
        rule = {"文档": ("docx", ".doc", "Word"), "演示": (".pptx", ".ppt", "幻灯片"),
                "表格": (".xlsx", ".xls", ".csv"), "脚本": ("🧾",),
                "代码": ("🧾", ".py", ".js", ".html", ".css", ".vue", ".go",
                         ".java", ".c"), "项目": ("📦",),
                "创作": ("🎨", "🎬", "📖", "创作")}.get(wf)
        if rule and any(tok in line for tok in rule):
            return True
        return wf in line

    def _work_refresh(self):
        self.work_list.clear()
        wf = getattr(self, "_wf", "全部")
        n = 0
        for line in self._work_rows():
            if self._work_keep(line, wf):
                self.work_list.addItem(line)
                n += 1
        if n == 0:
            if wf == "全部":
                self.work_list.addItem("（还没有工作记录：让它读文件 / 写脚本 / 开发项目吧）")
            else:
                self.work_list.addItem(f"（分类「{wf}」下还没有记录；换个分类看看，或让它干点活）")

    def _work_menu(self, pos):
        """右键：条目=打开/删除该条（按类型提供"仅删记录"或"连文件删"）；空白处=删除分类。"""
        from qt_compat import QtWidgets
        it = self.work_list.itemAt(pos)
        txt = it.text() if it is not None else ""
        is_placeholder = txt.startswith("（")     # 占位提示行按"空白处"处理
        wf = getattr(self, "_wf", "全部")
        menu = QMenu(self)
        on_item = it is not None and not is_placeholder
        act_open = act_del = act_del2 = act_del_all = None
        if on_item:
            act_open = menu.addAction("📂 打开 / 打开所在位置")
            if txt.startswith("📄"):
                # 已读文件：只可能是阅读记录（源文件在你自己电脑任何位置，绝不物理删）
                act_del = menu.addAction("🗑 删除这条阅读记录（保留源文件）")
            elif txt.startswith("🧾"):
                # 脚本：记录=文件（实时从脚本夹扫描），删除即删文件，明确告知
                act_del = menu.addAction("🗑 删除脚本文件（永久删除，不可恢复）")
            else:
                # 项目：两种都提供
                act_del = menu.addAction("仅删除记录（保留项目目录）")
                act_del2 = menu.addAction("🗑 删除项目与目录（不可恢复）")
        else:
            act_del_all = menu.addAction(
                f"🗑 删除分类「{wf}」下全部内容" if wf != "全部" else "🗑 清空全部工作记录")
        chosen = menu.exec(self.work_list.mapToGlobal(pos))
        if chosen is None:
            return
        if not on_item:
            if chosen == act_del_all:
                rows = [r for r in self._work_rows() if self._work_keep(r, wf)]
                self._ask_delete_rows(rows, bulk=True)
            return
        # ---- 条目动作 ----
        if chosen == act_open:
            self._work_open_item(it)
            return
        if txt.startswith("📄"):
            if chosen == act_del:
                ret = QtWidgets.QMessageBox.question(
                    self, "删除阅读记录",
                    "仅删除这条阅读记录，文件保留在你电脑原位置。确定？",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if ret == QtWidgets.QMessageBox.Yes:
                    self._work_delete_rows([txt], files=False)
                    self._work_refresh()
            return
        if txt.startswith("🧾"):
            if chosen == act_del:
                ret = QtWidgets.QMessageBox.question(
                    self, "删除脚本",
                    "将永久删除这个脚本文件（脚本夹里的 .py），不可恢复。\n" + txt,
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                    QtWidgets.QMessageBox.No)
                if ret == QtWidgets.QMessageBox.Yes:
                    msg = self._work_delete_rows([txt], files=True)
                    self._work_refresh()
                    self._append("系统", msg)
            return
        # 项目行
        if chosen == act_del2:
            ret = QtWidgets.QMessageBox.question(
                self, "删除项目",
                "将删除项目记录 + 整个项目目录（含全部代码文件），不可恢复！\n" + txt,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if ret == QtWidgets.QMessageBox.Yes:
                msg = self._work_delete_rows([txt], files=True)
                self._work_refresh()
                self._append("系统", msg)
        elif chosen == act_del:
            msg = self._work_delete_rows([txt], files=False)
            self._work_refresh()
            self._append("系统", msg)

    def _ask_delete_rows(self, rows, bulk=False):
        """空白/分类删除：弹窗二选一——仅清记录 / 连文件一起删（先确认）。"""
        from qt_compat import QtWidgets
        if not rows:
            QtWidgets.QMessageBox.information(self, "删除", "当前分类下没有可删除的内容。")
            return
        kind = "当前分类" if not bulk else ""
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle("删除" + kind + "内容")
        box.setText(
            f"将删除 {len(rows)} 条内容。请选择删除方式：\n\n"
            "· 仅删除记录：移除列表记录，脚本/项目文件保留在电脑上；\n"
            "  阅读记录本就只删记录。\n"
            "· 删除文件与记录：脚本文件 / 项目目录一并物理删除（不可恢复）。")
        b_keep = box.addButton("仅删除记录（保留文件）", QtWidgets.QMessageBox.AcceptRole)
        b_file = box.addButton("🗑 连文件一起删除", QtWidgets.QMessageBox.DestructiveRole)
        box.addButton("取消", QtWidgets.QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_file:
            ret = QtWidgets.QMessageBox.question(
                self, "确认删除文件",
                "将物理删除这些条目对应的脚本文件/项目目录，不可恢复。\n"
                "（已读文件与工作区外的内容始终只移除记录）\n确定继续？",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            if ret != QtWidgets.QMessageBox.Yes:
                return
            done = self._work_delete_rows(rows, files=True)
        elif clicked is b_keep:
            done = self._work_delete_rows(rows, files=False)
        else:
            return
        self._work_refresh()
        self._append("系统", done or "删除完成。")

    def _work_del_row(self, line):
        from qt_compat import QtWidgets
        ret = QtWidgets.QMessageBox.question(
            self, "删除这条成果",
            "删除这条成果及其文件/项目目录？\n" + line,
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No)
        if ret != QtWidgets.QMessageBox.Yes:
            return
        msg = self._work_delete_rows([line])
        self._work_refresh()
        self._append("系统", msg)

    def _work_delete_rows(self, rows, files=True):
        """删除记录对应内容。

        files=True：脚本/项目文件物理删除（仅限应用自己的工作区/脚本目录内）；
        files=False：只移除记录，文件一律保留；
        已读文件记录只是阅读历史——无论哪种都绝不删源文件，只移除该条展示。
        """
        if not rows:
            return ""
        notes = []
        proj = [p for p in _load_json(os.path.join(DATA_DIR, "projects.json"), [])]
        proj_left = list(proj)
        ws = (self.cfg.get("ws_dir") or "").strip()
        # v0.30.11：产物统一到工作根下按分类放，白名单也要跟着覆盖 ——
        # 否则"默认工作根"下的项目/脚本会被判成"不在工作区内"，删不掉。
        safe = [os.path.realpath(AT.SCRIPTS_DIR), os.path.realpath(AT.PROJECTS_DIR)]
        try:
            import workspace as WS
            safe.append(os.path.realpath(WS.root(self.cfg)))
        except Exception:  # noqa: BLE001
            pass
        if ws and os.path.isdir(ws):
            safe.append(os.path.realpath(ws))
        removed_read = 0
        removed_proj = 0
        for line in rows:
            m = re.search(r"[A-Za-z]:\\[^\s]+$|/[\w\-./]+$", line)
            path = m.group(0).strip() if m else ""
            if line.startswith("📄"):
                # 阅读记录：指向用户任意文件，绝不物理删
                if path:
                    self.work_ctx.pop(path, None)
                    removed_read += 1
                continue
            if line.startswith("🧾"):
                if path and files:
                    try:
                        rp = os.path.realpath(path)
                        if any(rp.startswith(s) for s in safe) and os.path.isfile(rp):
                            os.remove(rp)
                            notes.append(f"已删脚本：{os.path.basename(rp)}")
                        elif os.path.exists(rp):
                            notes.append(f"脚本不在工作区内，跳过文件：{path}")
                        else:
                            notes.append(f"脚本文件已不存在：{os.path.basename(rp)}")
                    except Exception as ex:
                        notes.append(f"删脚本失败：{path}（{ex}）")
                continue
            if line.startswith("📦"):
                # 从 projects.json 移除记录（两种模式都做）；目录仅 files=True 时删（工作区内）
                name = (line.split("📦 项目")[1].split("·")[0].strip()
                        if "📦 项目" in line else "")
                if files and path:
                    try:
                        rp = os.path.realpath(path)
                        if any(rp.startswith(s) for s in safe) and \
                                os.path.isdir(rp) and rp != AT.SCRIPTS_DIR:
                            import shutil
                            shutil.rmtree(rp, ignore_errors=True)
                            notes.append(f"已删项目目录：{os.path.basename(rp)}")
                        elif os.path.isdir(rp) or os.path.isfile(rp):
                            notes.append(f"项目在工作区外，仅移除记录：{rp}")
                    except Exception as ex:
                        notes.append(f"删项目目录失败：{path}（{ex}）")
                if name:
                    proj_left = [p for p in proj_left
                                 if (p.get("name", "") != name)]
                    removed_proj += 1
        _save_json(os.path.join(DATA_DIR, "projects.json"), proj_left)
        msg = "；".join(notes)
        if removed_read:
            msg += (("；" if msg else "") + f"移除 {removed_read} 条阅读记录（文件已保留）")
        if removed_proj:
            msg += (("；" if msg else "") + f"移除 {removed_proj} 条项目记录")
        return msg or "（没有可删除的内容）"

    def _work_open_dir(self):
        try:
            ws = self.cfg.get("ws_dir")
            target = ws if (ws and os.path.isdir(ws)) else AT.SCRIPTS_DIR
            PLATFORM_OPS.startfile(target)
        except Exception:
            pass

    def _work_open_item(self, item):
        text = item.text()
        # v0.27.4：任务行 → 打开"工作详情"（看进度/产物，并就地讨论修改）
        mt = re.search(r"任务#(t\d+)", text)
        if mt:
            self._open_task(mt.group(1))
            return
        # v0.18.0：路径允许含空格（创作目录名来自需求原文，可能带空格）——贪婪取到行尾
        m = re.search(r"[A-Za-z]:\\.*$|/[\w\-./]+$", text)
        path = m.group(0).strip() if m else ""
        if path and os.path.exists(path):
            try:
                if os.path.isdir(path):
                    PLATFORM_OPS.startfile(path)
                else:
                    PLATFORM_OPS.startfile(path)
            except Exception:
                pass

    def _open_task(self, tid: str):
        """v0.27.4：打开工作详情窗（信息 + 就地讨论该工作的修改/进度）。"""
        if not WORKLOG:
            return
        try:
            if not WORKLOG.get(tid):
                self._append("系统", f"没找到工作 #{tid}（可能已被清理）。")
                return
            TaskDialog(self, tid).exec()
            self._work_refresh()
            self._sync_pet_work()
        except Exception as ex:
            self._append("系统", f"打开工作详情失败：{ex}")

    # ---------- 🧭 工作流页（v0.30.17 从右侧栏搬来）----------
    def _page_flow(self):
        """工作流控制台 —— 建流 / 执行下一步 / 续跑 / 看状态与产物。

        为什么要从右栏搬到这里（小志 2026-09-17 的判断）：
          工作流是"发起一件多步的事"，与「✅ 任务」「⚡ 自发行为」同类，属**左栏语义**；
          而它原先占着右栏**第一个位置、标签还不可关闭**，把"要看的效果"挤到了后面。
        搬过来还有个好副作用：这里是**整页**（宽），比右栏 420px 宽松得多，
        工作流状态与产物预览都看得清了。

        ⚠️ 控件对象名一律沿用 `self.wp_*` —— `_wp_start / _wp_exec_next /
           _wp_resume / _wp_toggle_auto / _wp_render_panel / _wp_preview_artifact`
           十几处都按这些名字取控件。**搬的是父容器，不是变量名。**
        """
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)
        lay.addWidget(self._page_title("🧭 工作流 · 让它自己去干多步的活"))
        cols = QHBoxLayout()
        cols.setSpacing(12)

        # ===== 左列：控制台 =====
        left = QWidget()
        lv = QVBoxLayout(left)
        lv.setContentsMargins(0, 0, 0, 0)
        lv.setSpacing(6)
        self.wp_cur = QLabel("当前工作：—")
        self.wp_cur.setWordWrap(True)
        self.wp_cur.setStyleSheet(
            "QLabel{color:#0c4a6e;background:#e0f2fe;border-radius:6px;"
            "padding:6px 8px;font-size:12px;font-weight:bold;}")
        lv.addWidget(self.wp_cur)
        _alab = QLabel("新建工作流（AI 自动执行多步任务）：")
        _alab.setStyleSheet("color:#475569;font-size:12px;font-weight:bold;")
        lv.addWidget(_alab)
        self.wp_goal = QLineEdit()
        self.wp_goal.setPlaceholderText("输入目标，回车启动工作流")
        self.wp_goal.setMinimumHeight(30)
        self.wp_goal.returnPressed.connect(self._wp_start)
        lv.addWidget(self.wp_goal)
        self.wp_go = QPushButton("▶ 启动工作流")
        self.wp_go.setMinimumHeight(30)
        self.wp_go.setCursor(Qt.PointingHandCursor)
        self.wp_go.clicked.connect(self._wp_start)
        lv.addWidget(self.wp_go)
        self.wp_exec = QPushButton("▶ 执行下一步（真跑）")
        self.wp_exec.setMinimumHeight(30)
        self.wp_exec.setCursor(Qt.PointingHandCursor)
        self.wp_exec.setToolTip(
            "调用已注册的执行器**真跑**当前这一步（例如广告设计会真产出方案文件）。\n"
            "该工种还没接执行器时会如实告知，不会假装完成。")
        self.wp_exec.clicked.connect(self._wp_exec_next)
        lv.addWidget(self.wp_exec)
        self.wp_resume = QPushButton("✅ 完成此步 → 下一步")
        self.wp_resume.setMinimumHeight(30)
        self.wp_resume.setCursor(Qt.PointingHandCursor)
        self.wp_resume.setToolTip(
            "把当前这一步标记完成并推进到下一步（自动取最近一个未完成的工作流）。")
        self.wp_resume.clicked.connect(self._wp_resume)
        lv.addWidget(self.wp_resume)
        self.wp_auto = QPushButton("🤖 自主续跑：关")
        self.wp_auto.setMinimumHeight(30)
        self.wp_auto.setCursor(Qt.PointingHandCursor)
        self.wp_auto.setToolTip(
            "后台 Tick 心跳：巡检可续跑的工作、更新「下一步」提示。\n"
            "默认关闭；关闭时只做只读巡检，绝不动你的工作状态。")
        self.wp_auto.clicked.connect(self._wp_toggle_auto)
        lv.addWidget(self.wp_auto)
        self.wp_tick_lbl = QLabel("后台：待机")
        self.wp_tick_lbl.setWordWrap(True)
        self.wp_tick_lbl.setStyleSheet(
            "QLabel{color:#5b6b7c;background:#f1f5f9;border-radius:6px;"
            "padding:6px 8px;font-size:12px;}")
        lv.addWidget(self.wp_tick_lbl)
        # 说明：这一页与「⚡ 自发行为」是两回事（那是小人自己的心跳，
        # 这里是你要它去做的多步任务）。以前在右栏挤着，标题写不明显，搬到
        # 整页后把边界直接写出来，省得混。
        _note = QLabel(
            "<div style='line-height:1.8;color:#94a3b8;font-size:11px'>"
            "与「⚡ 自发行为」的区别：<br>"
            "· 这里 = <b>你</b>要它去干的多步任务（你定目标）<br>"
            "· 自发行为 = <b>小人自己</b>的心跳（它自己想做）"
            "</div>")
        _note.setWordWrap(True)
        lv.addWidget(_note)
        lv.addStretch(1)
        left.setFixedWidth(340)
        cols.addWidget(left)

        # ===== 右列：状态 + 产物预览 =====
        rgt = QWidget()
        rv = QVBoxLayout(rgt)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(6)
        _slab = QLabel("工作流状态：")
        _slab.setStyleSheet("color:#475569;font-size:12px;font-weight:bold;")
        rv.addWidget(_slab)
        # v0.30.15：两个浏览区都用 ChatBrowser —— 状态区/预览区带
        # `pasm://art/<n>` 这类"打开产物"锚点，而原生 QTextBrowser 点锚点会
        # `setSource` 把整块内容换成错误页（与聊天区复制键同款坑）。
        self.wp_next = ChatBrowser()
        self.wp_next.setOpenExternalLinks(False)
        self.wp_next.anchorClicked.connect(self._wp_panel_anchor)
        self.wp_next.setStyleSheet(
            "QTextBrowser{border:1px solid #e2e8f0;border-radius:8px;"
            "background:#fbfdff;color:#334155;padding:8px;font-size:12px;}")
        rv.addWidget(self.wp_next, 3)
        _plab = QLabel("产物预览：")
        _plab.setStyleSheet("color:#475569;font-size:12px;font-weight:bold;")
        rv.addWidget(_plab)
        self.wp_preview = ChatBrowser()
        self.wp_preview.setOpenExternalLinks(False)
        self.wp_preview.anchorClicked.connect(self._wp_panel_anchor)
        self.wp_preview.setStyleSheet(
            "QTextBrowser{border:1px solid #e2e8f0;border-radius:8px;"
            "background:#ffffff;color:#334155;padding:8px;font-size:12px;}")
        rv.addWidget(self.wp_preview, 2)
        cols.addWidget(rgt, 1)
        lay.addLayout(cols, 1)
        self._wp_render_panel()
        return page

    # ---------- 自动化页 ----------
    def _page_auto(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self._page_title("⚡ 自发行为 · 小人自己会做的事"))
        lay.addSpacing(6)
        self.auto_work_btn = QPushButton()
        self.auto_work_btn.setFixedHeight(40)
        self.auto_work_btn.clicked.connect(self._auto_toggle_work)
        self.auto_web_btn = QPushButton()
        self.auto_web_btn.setFixedHeight(40)
        self.auto_web_btn.clicked.connect(self._auto_toggle_web)
        lay.addWidget(self.auto_work_btn)
        lay.addWidget(self.auto_web_btn)
        lay.addWidget(QLabel("最近自动动态："))
        self.auto_list = QListWidget()
        self.auto_list.setStyleSheet(self.NAV_CSS)
        lay.addWidget(self.auto_list, 1)
        # v0.30.17：工作流从右侧栏搬到了左栏，这句引导必须跟着改 ——
        # 指向一个已经不存在的界面位置，比不写还糟（用户会找不到）。
        hint = QLabel("这是<b>小人的自发行为</b>：它自己在后台读书/复习/写日记/上网学习，"
                      "桌面小人运行时实时生效，这里改开关它几秒内跟上。<br>"
                      "注意与左栏「🧭 工作流」区分——那是<b>AI 多步任务引擎</b>（你给目标它真干活），"
                      "两者不是一回事。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(hint)
        # —— v0.25 自检·自愈：全程后台静默自动运行；v0.26.1 压缩成底部一条小卡，
        #    不再占主区域 ——
        heal_box = QFrame()
        heal_box.setStyleSheet("QFrame{background:#f8fafc;border:1px solid #eef2f7;"
                               "border-radius:8px;}")
        hv = QVBoxLayout(heal_box)
        hv.setContentsMargins(8, 5, 8, 5)
        hv.setSpacing(3)
        hrow = QHBoxLayout()
        hrow.setSpacing(6)
        scan_b = QPushButton("🩺 查看健康状态")
        scan_b.setCursor(Qt.PointingHandCursor)
        scan_b.setStyleSheet(
            "QPushButton{background:#fff;border:1px solid #dbe3ec;border-radius:10px;"
            "color:#334155;font-size:11px;padding:1px 10px;}"
            "QPushButton:hover{background:#f1f5f9}")
        scan_b.setToolTip("自检自愈已在后台自动运行（启动静默巡检、发现问题才提示，"
                          "不占资源）。点这里立刻看一次最新结果。")
        scan_b.clicked.connect(self._heal_scan)
        hrow.addWidget(scan_b)
        hrow.addStretch(1)
        hrow.addWidget(QLabel("后台自动巡检 · 无异常不打扰"))
        hrow.itemAt(2).widget().setStyleSheet("color:#94a3b8;font-size:10px")
        hv.addLayout(hrow)
        self.heal_list = QListWidget()
        self.heal_list.setStyleSheet(
            "QListWidget{border:none;background:transparent;font-size:11px;color:#475569;}")
        self.heal_list.setFixedHeight(64)
        hv.addWidget(self.heal_list)
        lay.addWidget(heal_box)
        return page

    def _auto_refresh(self):
        st = GROWTH.pet_state()
        aw = bool(st.get("auto_work", True))
        web = bool(st.get("auto_web", False))
        self.auto_work_btn.setText("⚡ 自发行为：" + ("开启中 ✅" if aw else "已关闭"))
        self.auto_web_btn.setText("🌐 自动上网自学：" + ("开启中 ✅（约30分钟学一个新主题，多源搜索）" if web else "已关闭"))
        self.auto_list.clear()
        acts = st.get("acts", [])
        if acts:
            for a in reversed(acts[-12:]):
                self.auto_list.addItem(f"[{a.get('t','')}] {a.get('what','')}")
        else:
            self.auto_list.addItem("（还没有动态：开启自发行为后，它会自己读书/复习/学习）")

    def _auto_toggle_work(self):
        st = GROWTH.pet_state()
        st["auto_work"] = not bool(st.get("auto_work", True))
        GROWTH.save_state(st)
        self._auto_refresh()
        self._append("系统", "自发行为已" + ("开启" if st["auto_work"] else "关闭") +
                     "（桌面小人会在几秒内同步）。")

    def _auto_toggle_web(self):
        st = GROWTH.pet_state()
        st["auto_web"] = not bool(st.get("auto_web", False))
        GROWTH.save_state(st)
        self._auto_refresh()
        self._append("系统", "自动上网自学已" + ("开启" if st["auto_web"] else "关闭") + "。")

    # ---------- 自检·自定位·自修复（v0.25 全后台静默，UI 只看状态） ----------
    def _heal_scan(self):
        """「查看健康状态」：先立刻显示上次后台巡检摘要，再异步补一次实时巡检。"""
        if HEAL is None:
            return
        st = HEAL.last_status()
        self.heal_list.clear()
        if st.get("time"):
            self.heal_list.addItem("🕒 上次自动巡检：%s（错误 %d · 崩溃 %d）"
                                   % (st.get("time", ""), st.get("errors", 0),
                                      st.get("crashes", 0)))
            for it in st.get("items", []):
                self.heal_list.addItem("🔴 %s [%s] %s" % (
                    it.get("source", "?"), it.get("kind", "?"),
                    (it.get("first") or [""])[0][:60]))
        else:
            self.heal_list.addItem("⏳ 后台巡检还没跑完，正在实时补扫…")
        def worker():
            try:
                symptoms = HEAL.scan_logs()
            except Exception as ex:
                symptoms = [{"source": "-", "kind": "error", "time": "",
                             "excerpt": "巡检异常：%s" % ex}]
            self._ui(lambda: self._heal_scan_done(symptoms))
        threading.Thread(target=worker, daemon=True).start()

    def _heal_scan_done(self, symptoms):
        self.heal_list.clear()
        if not symptoms:
            self.heal_list.addItem("✅ 本次巡检没有发现错误或崩溃（一切正常）")
            return
        for s in symptoms[:20]:
            first = (s.get("excerpt") or "").strip().splitlines()
            self.heal_list.addItem("🔴 %s [%s] %s" % (
                s.get("source", "?"), s.get("kind", "?"),
                (first[0][:60] if first else "")))
        self._append("系统", "🩺 自检发现 %d 处异常，详见「⚡ 自发行为」页；"
                     "诊断报告会由后台自动落盘到 data/selfheal/reports/。" % len(symptoms))

    def _heal_diagnose(self):
        """C2：对最近一条故障案例跑根因定位（后台自动触发电会走这里）。"""
        if HEAL is None:
            return
        cases = HEAL.list_cases()
        if not cases:
            return
        mode = HEAL.heal_mode(self.cfg)
        case = cases[0]
        code_root = (None if getattr(sys, "frozen", False)
                     else os.path.abspath(os.path.join(
                         os.path.dirname(os.path.abspath(__file__)), "..")))
        self._append("系统", "🩺 正在定位最近故障根因（%s）…" % case.get("source", ""))
        def worker():
            try:
                rep = HEAL.diagnose(case, self._team_llm(),
                                    code_root or os.path.expanduser("~"),
                                    progress=lambda m: self._ui(
                                        lambda mm=m: self._append("系统", str(mm))))
            except Exception as ex:
                rep = {"ok": False, "report": "定位异常：%s" % ex, "path": ""}
            self._ui(lambda: self._heal_diag_done(case, rep, mode, code_root))
        threading.Thread(target=worker, daemon=True).start()

    def _heal_diag_done(self, case, rep, mode, code_root):
        if not rep.get("ok"):
            self._append("系统", "❌ " + (rep.get("report") or "定位失败"))
            return
        self._append("系统", "📋 诊断报告已生成：" + (rep.get("path") or "")
                     + "\n\n" + (rep.get("report") or "")[:1200])
        if mode != "apply" or not code_root:
            return
        # apply（自动修复）模式：补丁 → 冒烟 → 回滚保护（仅源码环境）
        smoke_dir = os.path.join(code_root, ".smoke_q")
        self._append("系统", "🔧 已进入自动修复：生成补丁 → 备份 → 应用 → 冒烟回归（失败自动回滚）…")
        def w2():
            try:
                r = HEAL.apply_patch_and_verify(rep.get("report", ""), code_root,
                                                smoke_dir, self._team_llm(),
                                                progress=lambda m: self._ui(
                                                    lambda mm=m: self._append("系统", str(mm))))
            except Exception as ex:
                r = {"ok": False, "log": "修复异常：%s" % ex}
            self._ui(lambda: self._append(
                "系统", ("✅ 自动修复完成并通过全部冒烟回归。\n" if r.get("ok")
                         else "❌ 自动修复未通过验证，已回滚保持原状。\n")
                + str(r.get("log", ""))[:1200]))
        threading.Thread(target=w2, daemon=True).start()

    def _heal_autoscan(self):
        """启动后异步哨兵（C1 静默巡检）：结果落盘 status.json，发现崩溃才提示一句。"""
        if HEAL is None:
            return
        def worker():
            try:
                symptoms = [s for s in HEAL.scan_logs() if s.get("kind") == "crash"]
            except Exception:
                return
            if not symptoms:
                return
            s = symptoms[0]
            self._ui(lambda: self._append(
                "系统", "🩺 自检哨兵：发现上次的崩溃记录（%s）——后台已记录并生成诊断报告，"
                "到「⚡ 自发行为」页点「查看健康状态」可看详情。" % s.get("source", "")))
        threading.Thread(target=worker, daemon=True).start()

    # ---------- 团队页（v0.25 公司式项目组：项目空间+拖拽编排+成员评分+网上选才） ----------
    def _page_team(self):
        """👥 项目组页（v0.26.1 UI 重构：卡片式分区，去噪好操作）。
        布局：项目工具条 → 左「成员」卡 / 右「流程」卡 → 底部开工区。
        """
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(8)
        root.addWidget(self._page_title("👥 项目组 · 像一家真公司那样干活"))
        _card = ("QFrame{background:#ffffff;border:1px solid #e6ebf2;border-radius:12px;}")
        _head = ("color:#334155;font-weight:bold;font-size:13px;")
        _sub = ("QPushButton{background:#f1f5f9;border:1px solid #e2e8f0;border-radius:8px;"
                "color:#475569;font-size:11px;padding:2px 8px;text-align:center;}"
                "QPushButton:hover{background:#e0f2fe;color:#0369a1;}")
        # —— 卡1：项目 ——
        p_card = QFrame()
        p_card.setStyleSheet(_card)
        pv = QVBoxLayout(p_card)
        pv.setContentsMargins(10, 7, 10, 7)
        pv.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(6)
        row.addWidget(QLabel("📁 项目"))
        row.itemAt(0).widget().setStyleSheet(_head)
        self.team_proj = QComboBox()
        self.team_proj.setMinimumWidth(150)
        self.team_proj.setStyleSheet(
            "QComboBox{border:1px solid #dbe3ec;border-radius:8px;padding:2px 8px;"
            "background:#fff;font-size:12px;}")
        self.team_proj.currentIndexChanged.connect(lambda _i: self._team_refresh())
        row.addWidget(self.team_proj, 1)
        nb = QPushButton("＋ 新建项目")
        nb.setStyleSheet(_sub)
        nb.setCursor(Qt.PointingHandCursor)
        nb.clicked.connect(self._team_proj_new)
        row.addWidget(nb)
        db = QPushButton("🗑 删除")
        db.setStyleSheet(_sub)
        db.setCursor(Qt.PointingHandCursor)
        db.clicked.connect(self._team_proj_del)
        row.addWidget(db)
        pv.addLayout(row)
        self.team_goal = QLabel("")
        self.team_goal.setWordWrap(False)
        self.team_goal.setStyleSheet("color:#64748b;font-size:11px;")
        pv.addWidget(self.team_goal)
        root.addWidget(p_card)
        # —— 中区：成员 | 流程 ——
        split = QHBoxLayout()
        split.setSpacing(8)
        # 左卡：成员
        l_card = QFrame()
        l_card.setStyleSheet(_card)
        lv = QVBoxLayout(l_card)
        lv.setContentsMargins(10, 7, 10, 7)
        lv.setSpacing(5)
        lh = QHBoxLayout()
        lh.setSpacing(5)
        self.team_mem_cnt = QLabel("👥 项目成员")
        self.team_mem_cnt.setStyleSheet(_head)
        lh.addWidget(self.team_mem_cnt)
        lh.addStretch(1)
        for txt, cb in (("🧑‍💼 人才库", self._team_pool_add),
                        ("🌐 招聘", self._team_web_add),
                        ("🎓 技能库", self._team_skill_add),
                        ("＋ 建岗", self._team_role_add)):
            b = QPushButton(txt)
            b.setStyleSheet(_sub)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            lh.addWidget(b)
        lv.addLayout(lh)
        self.team_roles = QListWidget()
        self.team_roles.setStyleSheet(
            "QListWidget{border:1px solid #eef2f7;border-radius:8px;background:#fbfcfe;"
            "font-size:12px;}"
            "QListWidget::item{padding:5px 6px;border-radius:6px;}"
            "QListWidget::item:selected{background:#e0f2fe;color:#0c4a6e;}")
        self.team_roles.itemClicked.connect(lambda it: self._team_role_view(it))
        self.team_roles.setContextMenuPolicy(Qt.CustomContextMenu)
        self.team_roles.customContextMenuRequested.connect(
            lambda pos: self._team_role_menu(self.team_roles.mapToGlobal(pos)))
        lv.addWidget(self.team_roles, 1)
        ltip = QLabel("点击成员查看职责与评分（⭐ 手动打分）· 右键可辞退")
        ltip.setStyleSheet("color:#94a3b8;font-size:10px")
        lv.addWidget(ltip)
        split.addWidget(l_card, 1)
        # 右卡：流程
        r_card = QFrame()
        r_card.setStyleSheet(_card)
        rv = QVBoxLayout(r_card)
        rv.setContentsMargins(10, 7, 10, 7)
        rv.setSpacing(5)
        rh = QHBoxLayout()
        rh.setSpacing(5)
        self.team_step_cnt = QLabel("📋 项目流程")
        self.team_step_cnt.setStyleSheet(_head)
        rh.addWidget(self.team_step_cnt)
        rh.addStretch(1)
        # v0.27 经验沉淀：流程预设下拉（成功 run 自动沉淀 + 一键复用）
        self.team_tpl = QComboBox()
        self.team_tpl.setStyleSheet(
            "QComboBox{border:1px solid #e2e8f0;border-radius:6px;background:#fff;"
            "padding:2px 6px;font-size:11px;max-width:150px;}")
        self.team_tpl.setToolTip("历次成功项目沉淀下来的流程模板——选中后点「套用」整条搬到本项目")
        rh.addWidget(self.team_tpl)
        for txt, cb in (("套用", self._team_tpl_apply),
                        ("＋ 加入成员", self._team_step_add),
                        ("－ 移除", self._team_step_del)):
            b = QPushButton(txt)
            b.setStyleSheet(_sub)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            rh.addWidget(b)
        rv.addLayout(rh)
        self.team_steps = QListWidget()
        self.team_steps.setStyleSheet(
            "QListWidget{border:1px solid #eef2f7;border-radius:8px;background:#fbfcfe;"
            "font-size:12px;}"
            "QListWidget::item{padding:5px 6px;border-radius:6px;}"
            "QListWidget::item:selected{background:#e0f2fe;color:#0c4a6e;}")
        self.team_steps.setDragDropMode(self.team_steps.DragDropMode.InternalMove)
        self.team_steps.itemDoubleClicked.connect(lambda it: self._team_step_edit(it))
        self.team_steps.model().rowsMoved.connect(lambda *_: self._team_steps_save())
        rv.addWidget(self.team_steps, 1)
        rtip = QLabel("拖动排序 · 双击修改任务 · 上一步加 🔍 即审查返工")
        rtip.setStyleSheet("color:#94a3b8;font-size:10px")
        rv.addWidget(rtip)
        split.addWidget(r_card, 1)
        root.addLayout(split, 4)
        # —— 底部开工区 ——
        g_card = QFrame()
        g_card.setStyleSheet(_card)
        gv = QVBoxLayout(g_card)
        gv.setContentsMargins(10, 7, 10, 7)
        gv.setSpacing(5)
        grow = QHBoxLayout()
        grow.setSpacing(8)
        grow.addWidget(QLabel("🎯 本次任务"))
        grow.itemAt(0).widget().setStyleSheet(_head)
        self.team_task = QTextEdit()
        self.team_task.setPlaceholderText(
            "要让项目组做什么？例如：做一个记账小工具，能记收入支出并按月统计…")
        self.team_task.setFixedHeight(44)
        self.team_task.setStyleSheet(
            "QTextEdit{border:1px solid #dbe3ec;border-radius:10px;background:#ffffff;"
            "color:#0f172a;padding:5px;font-size:12px;}")
        grow.addWidget(self.team_task, 1)
        self.team_go = QPushButton("▶ 让团队开工")
        self.team_go.setFixedHeight(34)
        self.team_go.setCursor(Qt.PointingHandCursor)
        self.team_go.setStyleSheet(
            "QPushButton{background:#0ea5e9;color:#fff;border:none;border-radius:15px;"
            "padding:0 16px;font-weight:bold;}"
            "QPushButton:hover{background:#0284c7}"
            "QPushButton:disabled{background:#94a3b8;}")
        self.team_go.clicked.connect(self._team_run)
        grow.addWidget(self.team_go)
        # v0.27.6：断点续跑 + 看产出（真机痛点：跑一半蓝屏 → 不知道从哪接续、看不到产出）
        _hrow = QHBoxLayout()
        _hrow.setSpacing(6)
        self.team_resume = QPushButton("⏩ 继续上次未完成的执行")
        self.team_resume.setFixedHeight(30)
        self.team_resume.setCursor(Qt.PointingHandCursor)
        self.team_resume.setStyleSheet(
            "QPushButton{background:#f59e0b;color:#fff;border:none;border-radius:14px;"
            "padding:0 12px;font-weight:bold;font-size:12px;}"
            "QPushButton:hover{background:#d97706;}")
        self.team_resume.setToolTip(
            "上次执行被中断（蓝屏 / 关程序）时从这里接着跑：\n"
            "已完成的步骤直接复用产出，不重复耗时。")
        self.team_resume.clicked.connect(self._team_resume)
        self.team_resume.setVisible(False)
        _hrow.addWidget(self.team_resume)
        # v0.27.7：暂停员工工作——当前这一步做完就停，不再往下跑（产出保留、可继续）
        self.team_pause = QPushButton("⏸ 暂停员工工作")
        self.team_pause.setFixedHeight(30)
        self.team_pause.setCursor(Qt.PointingHandCursor)
        self.team_pause.setStyleSheet(
            "QPushButton{background:#fee2e2;color:#b91c1c;border:1px solid #fecaca;"
            "border-radius:14px;padding:0 12px;font-weight:bold;font-size:12px;}"
            "QPushButton:hover{background:#fecaca;}"
            "QPushButton:disabled{background:#f1f5f9;color:#94a3b8;border-color:#e2e8f0;}")
        self.team_pause.setToolTip(
            "让团队停下来：**当前这一步做完就停**，不再往下跑。\n"
            "已完成步骤的产出全部保留，随时可点「⏩ 继续」接着干。")
        self.team_pause.clicked.connect(self._team_pause_click)
        self.team_pause.setVisible(False)
        _hrow.addWidget(self.team_pause)
        # v0.27.7：团队自己的忙状态（与聊天的 busy 分开，互不干扰）
        self.team_busy = False
        self._team_pause_flag = threading.Event()
        self.team_open = QPushButton("📂 产出目录")
        self.team_open.setFixedHeight(30)
        self.team_open.setCursor(Qt.PointingHandCursor)
        self.team_open.setStyleSheet(
            "QPushButton{background:#f1f5f9;color:#334155;border:1px solid #d8e0ea;"
            "border-radius:14px;padding:0 12px;font-size:12px;}"
            "QPushButton:hover{border-color:#0ea5e9;color:#0284c7;}")
        self.team_open.setToolTip("打开最近一次执行的产出目录（每一步的成品 md 都在里面）")
        self.team_open.clicked.connect(self._team_open_out)
        _hrow.addWidget(self.team_open)
        _hrow.addStretch(1)
        grow.addLayout(_hrow)
        gv.addLayout(grow)
        self.team_out = QTextBrowser()
        self.team_out.setStyleSheet(
            "QTextBrowser{border:none;background:#f8fafc;border-radius:8px;"
            "color:#0f172a;padding:6px;font-size:11px;}")
        self.team_out.setFixedHeight(120)
        gv.addWidget(self.team_out)
        root.addWidget(g_card, 2)
        return page

    def _team_refresh(self):
        if TEAM is None:
            return
        # 项目下拉（重建时阻断信号防递归刷新）
        self.team_proj.blockSignals(True)
        cur_id = self.team_proj.currentData()
        self.team_proj.clear()
        projs = TEAM.list_projects()
        if not projs:
            TEAM.new_project("第一个项目", "")
            projs = TEAM.list_projects()
        for p in projs:
            self.team_proj.addItem("📁 " + p["name"], p["id"])
        if cur_id:
            i = self.team_proj.findData(cur_id)
            if i >= 0:
                self.team_proj.setCurrentIndex(i)
        self.team_proj.blockSignals(False)
        proj = self._team_cur_proj()
        if not proj:
            return
        # v0.27.6：程序启动后**首次**进团队页 →
        #   ① 先把磁盘上"有产出、但项目里没记录"的历史执行补回来
        #      （旧版跑一半中断的遗留，例如 GEO平台 那 5 个 step 产出文件）
        #   ② 再把仍卡在 running 的判为"被硬中断"（程序重启必然跑不到收尾）
        if not getattr(self, "_team_interrupted_checked", False):
            self._team_interrupted_checked = True
            try:
                TEAM.recover_orphan_runs()
                for _p in TEAM.list_projects():
                    if TEAM.mark_running_as_interrupted(_p.get("id", "")):
                        proj = TEAM.project_of(proj.get("id", "")) or proj
            except Exception:
                pass
        # 「⏩ 继续上次未完成的执行」按钮：有未完成/被中断的执行、且**当前没在执行**时才可点。
        #   真机反馈：执行中也点得动 → 一点就并行开出第二个 run（runs 里多出一个文件夹）。
        try:
            _unf = TEAM.unfinished_run(proj.get("id", ""))
            _tbusy = bool(getattr(self, "team_busy", False))
            self.team_resume.setVisible(bool(_unf))
            self.team_resume.setEnabled(bool(_unf) and not _tbusy)
            if _tbusy:
                self.team_resume.setToolTip(
                    "团队正在执行中，先等它跑完（或点「⏸ 暂停员工工作」停下来），"
                    "之后才能「继续」。")
            else:
                self.team_resume.setToolTip(
                    "上次执行被中断（蓝屏 / 关程序 / 模型忙）时从这里接着跑：\n"
                    "已完成的步骤直接复用产出，不重复耗时。")
        except Exception:
            pass
        goal = proj.get("goal") or "（还没目标——开工时自动用本次任务补上）"
        self.team_goal.setText("🎯 目标：" + (goal if len(goal) <= 46 else goal[:46] + "…"))
        self.team_goal.setToolTip("🎯 " + goal)
        # v0.26.3：成员 = 项目专属编制；老项目首次打开按历史步骤自动回填
        if not isinstance(proj.get("members"), list):
            mbrs, seen = [], set()
            for s in proj.get("steps") or []:
                nm = str(s.get("role") or "").strip()
                if nm and nm not in seen:
                    seen.add(nm)
                    mbrs.append(nm)
            proj["members"] = mbrs
            try:
                TEAM.save_project(proj)
            except Exception:
                pass
        self.team_roles.clear()
        mbrs = [m for m in (proj.get("members") or []) if m and TEAM.role_of(m)]
        if not mbrs:
            it = QListWidgetItem("（项目还没有成员——点右上「＋」建岗 / 🧑‍💼 从人才库选人进组）")
            it.setFlags(Qt.NoItemFlags)
            self.team_roles.addItem(it)
        for name in mbrs:
            r = TEAM.role_of(name)
            rat = TEAM.rating_of(name)
            stars = "★" * int(round(rat.get("score", 4.0)))
            it = QListWidgetItem("%s %s　%s %.1f · %s" % (
                r.get("emoji", ""), name, stars, rat.get("score", 4.0),
                (r.get("duty") or "")[:18]))
            it.setData(0x0100, name)
            it.setToolTip("本项目成员（评分来自人才库）· 点击查看员工卡/⭐打分 · 右键移出本项目")
            self.team_roles.addItem(it)
        self.team_mem_cnt.setText("👥 项目成员 · %d" % len(mbrs))
        self._fill_steps(proj)
        self.team_step_cnt.setText("📋 项目流程 · %d 步" % self.team_steps.count())
        # v0.27 流程预设下拉刷新
        self.team_tpl.blockSignals(True)
        cur = self.team_tpl.currentData()
        self.team_tpl.clear()
        self.team_tpl.addItem("📂 流程预设", "")
        try:
            for t in TEAM.list_flow_templates():
                self.team_tpl.addItem("📋 %s（%s）" % (t["name"], t.get("t", "")),
                                      t["name"])
        except Exception:
            pass
        if cur:
            i = self.team_tpl.findData(cur)
            if i >= 0:
                self.team_tpl.setCurrentIndex(i)
        self.team_tpl.blockSignals(False)
        # 非运行中：档案区展示 目标/成员/流程状态/历次效果（切项目即可回看）
        # （v0.27 趋势重构时此处被流程预设下拉意外挤掉，v0.27.0 补回）
        if hasattr(self, "team_out"):
            if getattr(self, "team_busy", False):
                # v0.27.7：**执行中**切页回来 → 在实时输出末尾补一条当前进度
                #   （真机反馈：执行时切走再回团队，"第一次没有看到状态"）
                try:
                    _run = TEAM.unfinished_run(proj.get("id", ""))
                    if _run:
                        self.team_out.append(
                            "🔄 正在执行：第 %d/%s 步 · %s —— 想停下可点上方"
                            "「⏸ 暂停员工工作」（当前步做完就停，产出都保留）"
                            % (int(_run.get("done") or 0) + 1,
                               _run.get("total") or "?",
                               _run.get("current") or ""))
                except Exception:
                    pass
            else:
                try:
                    self._team_show_archive(proj)
                except Exception:
                    pass

    def _team_tpl_apply(self):
        """v0.27 一键套用流程预设：整条搬到当前项目（原步骤会被替换，先确认）。"""
        if TEAM is None:
            return
        name = self.team_tpl.currentData()
        if not name:
            QMessageBox.information(self, "流程预设",
                                    "还没有可用的预设——成功跑完的项目会自动沉淀到这里。")
            return
        tpl = TEAM.get_flow_template(name)
        if not tpl:
            return
        proj = self._team_cur_proj()
        if not proj:
            return
        if proj.get("steps") and QMessageBox.question(
                self, "套用流程预设",
                "当前项目已有 %d 步流程，套用「%s」会整条替换，确定吗？"
                % (len(proj["steps"]), name)) != QMessageBox.StandardButton.Yes:
            return
        proj["steps"] = [dict(s) for s in tpl["steps"]]
        TEAM.save_project(proj)
        # 预设里的成员一并编入项目（人才库里有的才编）
        mbrs = proj.setdefault("members", [])
        for s in proj["steps"]:
            nm = str(s.get("role") or "")
            if nm and nm not in mbrs and TEAM.role_of(nm):
                mbrs.append(nm)
        TEAM.save_project(proj)
        self._team_refresh()
        self._append("系统", "📋 已把预设「%s」（%d 步）套用到项目「%s」，成员一并编入。"
                     % (name, len(proj["steps"]), proj.get("name", "")))
        # 非运行中：档案区展示 目标/成员/流程状态/历次效果（切项目即可回看）
        if not self.busy and hasattr(self, "team_out"):
            try:
                self._team_show_archive(proj)
            except Exception:
                pass

    def _team_cur_proj(self) -> dict:
        if TEAM is None:
            return {}
        pid = self.team_proj.currentData()
        return TEAM.project_of(pid) or {}

    # —— v0.26.3 项目专属编制：进组/出组（人才池记录保留，随时可再入） ——
    def _team_add_member(self, name: str) -> bool:
        if TEAM is None or not name:
            return False
        proj = self._team_cur_proj()
        if not proj:
            return False
        mbrs = proj.setdefault("members", [])
        if name not in mbrs:
            mbrs.append(name)
        TEAM.save_project(proj)
        return True

    def _team_pool_add(self):
        """从全局人才库把现成角色编入当前项目（岗位档案保留在人才库）。"""
        if TEAM is None:
            return
        pool = [r["name"] for r in TEAM.list_roles()]
        proj = self._team_cur_proj()
        have = set(proj.get("members") or [])
        cands = [n for n in pool if n not in have]
        if not cands:
            QMessageBox.information(
                self, "从人才库加入",
                ("人才库为空，或本项目的成员已覆盖整个人才库。\n"
                 "可先用「🌐 招聘 / 🎓 技能库 / ＋手动」招新岗位。") if not pool
                else "本项目成员已覆盖整个人才库。")
            return
        name, ok = QInputDialog.getItem(
            self, "从人才库加入成员",
            "选一位加入当前项目「%s」：" % proj.get("name", ""), cands, 0, False)
        if ok and name:
            self._team_add_member(name)
            self._team_refresh()
            self._append("系统", "已把「%s」编入项目「%s」——再从右侧把它加入流程吧。"
                         % (name, proj.get("name", "")))

    def _team_show_archive(self, proj: dict = None):
        """项目档案：目标 / 编制 / 流程状态 / 历次效果（切项目即可完整回看）。"""
        if proj is None:
            proj = self._team_cur_proj()
        if not proj or not hasattr(self, "team_out"):
            return
        goal = proj.get("goal") or "（未填——开工时自动记录）"
        mbrs = [m for m in (proj.get("members") or []) if m and TEAM and TEAM.role_of(m)]
        steps = proj.get("steps") or []
        runs = proj.get("runs") or []
        lines = ["<b>📁 项目档案 · %s</b>" % html.escape(proj.get("name", "")),
                 "🎯 目标：%s" % html.escape(goal),
                 "👥 成员 %d 人　·　📋 流程 %d 步　·　▶ 已执行 %d 次"
                 % (len(mbrs), len(steps), len(runs))]
        if steps:
            lines.append("<div style='margin-top:4px;color:#475569'>流程与最近状态：</div>")
            for i, s in enumerate(steps, 1):
                r = TEAM.role_of(str(s.get("role", ""))) if TEAM else None
                st = s.get("last_status", "")
                mark = "✅" if st == "done" else ("⚠️" if st == "fail" else "○")
                lines.append("　%d. %s %s %s — %s" % (
                    i, mark, (r or {}).get("emoji", "❓"), html.escape(str(s.get("role", "?"))),
                    html.escape((s.get("task_hint") or "")[:40])))
        if runs:
            lines.append("<hr>")
            lines.append("<b>🕘 执行记录 · %d 次</b>（新的在上）" % len(runs))
            for run in reversed(runs[-6:]):
                lines.extend(self._run_html_lines(run))
            last = runs[-1]
            if last.get("summary"):
                lines.append("<hr>")
                lines.append("<b>📄 最近一次交付摘要</b>")
                lines.append(md_to_html(str(last["summary"])[:1500]))
        else:
            lines.append("<hr><div style='color:#94a3b8'>还没有执行记录——"
                         "填好目标与流程后点「▶ 让团队开工」。</div>")
        if not steps and not mbrs:
            lines.append("<div style='color:#f59e0b;margin-top:6px'>全新项目：先用「＋ 建岗 / 🧑‍💼 人才库 / 🌐 招聘 / 🎓 技能库」"
                         "把成员编入项目，再在右侧给每位成员「加入流程」定义任务。</div>")
        self.team_out.setHtml("<div style='line-height:1.6'>" + "<br>".join(lines) + "</div>")

    def _run_html_lines(self, run: dict) -> list:
        """v0.27.6：把一条执行记录渲染成可读几行——状态 / 步数进度 / 每步产出 / 中断点。

        真机痛点：跑一半中断后"看不到进度和具体信息、不知道从哪接续"。
        """
        st = str(run.get("status") or "")
        ic = {"running": "🔄 进行中", "done": "✅ 完成",
              "failed": "⚠️ 有步骤失败", "interrupted": "⏸ 已中断"}.get(st, "🕘")
        total = int(run.get("total") or 0)
        done = int(run.get("done") or 0)
        head = ("<div style='margin-top:6px'><b>%s</b>　%s　"
                "<span style='color:#64748b'>%s%s</span>"
                % (ic, html.escape(str(run.get("t") or run.get("ts") or "")),
                   ("%d/%d 步　" % (done, total)) if total else "",
                   html.escape(str(run.get("task") or "")[:40])))
        if run.get("resumed_from"):
            head += "<span style='color:#0284c7'>（从上次中断处续跑）</span>"
        head += "</div>"
        out = [head]
        for e in (run.get("step_log") or [])[-12:]:
            em = "✅" if e.get("status") == "done" else \
                 ("⚠️" if e.get("status") == "fail" else "⏳")
            extra = "（复用上次产出）" if e.get("resumed") else ""
            out.append("　　%d. %s %s　<span style='color:#64748b'>%s%s</span>"
                       % (int(e.get("i") or 0), em,
                          html.escape(str(e.get("role") or "")),
                          html.escape(str(e.get("file") or "")), extra))
        if st == "interrupted":
            out.append("　　<span style='color:#b45309'>⏸ 上次在第 %d 步中断"
                       "（程序退出 / 蓝屏）——点上方「⏩ 继续上次未完成的执行」接着跑</span>"
                       % (done + 1))
        elif st == "running":
            out.append("　　<span style='color:#0284c7'>🔄 正在执行：%s</span>"
                       % html.escape(str(run.get("current") or "")))
        rd = str(run.get("run_dir") or "")
        try:
            _n = len(TEAM.run_outputs(rd)) if (TEAM and rd) else 0
        except Exception:
            _n = 0
        if _n:
            out.append("　　<span style='color:#64748b'>📂 产出 %d 个文件：%s</span>"
                       % (_n, html.escape(rd)))
        return out

    def _team_open_out(self):
        """v0.27.6：打开最近一次执行的产出目录（每步成品 md 都在里面）。"""
        if TEAM is None:
            return
        proj = self._team_cur_proj()
        if not proj:
            return
        runs = proj.get("runs") or []
        rd = str((runs[-1] if runs else {}).get("run_dir") or "")
        if not rd or not os.path.isdir(rd):
            try:
                _base = TEAM.runs_dir()          # v0.30.11：现算，别读冻结常量
            except Exception:  # noqa: BLE001
                _base = getattr(TEAM, "RUNS_DIR", "")
            if _base and os.path.isdir(_base):
                rd = _base
            else:
                QMessageBox.information(self, "产出目录", "还没有执行产出。先点「▶ 让团队开工」。")
                return
        try:
            PLATFORM_OPS.startfile(rd)
        except Exception as ex:
            QMessageBox.information(self, "产出目录", "打不开目录：%s\n路径：%s" % (ex, rd))

    def _team_resume(self):
        """v0.27.6：从上次中断处继续执行（已完成步骤复用产出，不重复烧 token）。"""
        if TEAM is None:
            return
        proj = self._team_cur_proj()
        if not proj:
            return
        run = TEAM.unfinished_run(proj.get("id", ""))
        if not run:
            QMessageBox.information(self, "继续执行", "这个项目当前没有未完成的执行记录。")
            self._team_refresh()
            return
        steps = proj.get("steps") or []
        if not steps:
            QMessageBox.information(self, "继续执行", "项目流程是空的——先把流程配好。")
            return
        done = int(run.get("done") or 0)
        task = str(run.get("task") or proj.get("goal") or
                   self.team_task.toPlainText().strip())
        if not task:
            QMessageBox.information(self, "继续执行",
                                    "找不到上次的任务描述，请在下面重新填写任务后再开工。")
            return
        if not self._ui_confirm(
                "继续执行",
                "上次执行中断在第 %d 步（共 %s 步）。\n\n"
                "将从第 %d 步接着跑；**已完成的 %d 步直接复用产出**，不重复耗时。\n\n继续吗？"
                % (done, run.get("total") or "?", done + 1, done)):
            return
        self._team_begin_busy()
        self.team_out.setPlainText("⏩ 从上次中断处继续（已完成 %d 步复用产出）…\n" % done)
        pid = proj.get("id", "")
        try:
            pmem = TEAM.project_memory_of(pid)
        except Exception:
            pmem = ""

        def worker():
            def prog(m):
                self._ui(lambda mm=m: self.team_out.append(str(mm)))
            try:
                res = TEAM.run_team("项目流程", task, self._team_llm(), progress=prog,
                                    steps=steps, project_id=pid,
                                    project_memory=pmem, resume_run=run,
                                    should_pause=lambda: self._team_pause_flag.is_set())
            except Exception as ex:
                res = {"ok": False, "summary": "续跑异常：%s" % ex,
                       "steps": [], "run_dir": ""}
            self._ui(lambda: self._team_done(res, pid, task))
        threading.Thread(target=worker, daemon=True).start()

    def _team_begin_busy(self):
        """v0.27.7：进入「团队执行中」——禁用开工/继续（防止并行开跑产生多个 run），
        并亮出「⏸ 暂停员工工作」。"""
        self.team_busy = True
        try:
            self._team_pause_flag.clear()
        except Exception:
            pass
        try:
            self.team_go.setEnabled(False)
            self.team_resume.setEnabled(False)
            self.team_pause.setVisible(True)
            self.team_pause.setEnabled(True)
            self.team_pause.setText("⏸ 暂停员工工作")
        except Exception:
            pass

    def _team_pause_click(self):
        """v0.27.7：暂停员工工作 —— 当前这一步做完就停（不是强杀）。

        真机反馈：模型忙时一串步骤全在排队空跑，用户想叫停却没有手段。
        这里在**步骤之间**检查暂停标志：已完成的产出全部保留，可随时继续。
        """
        if not getattr(self, "team_busy", False) or self._team_pause_flag.is_set():
            return
        self._team_pause_flag.set()
        try:
            self.team_pause.setEnabled(False)
            self.team_pause.setText("⏸ 正在暂停…")
        except Exception:
            pass
        try:
            self.team_out.append("⏸ 已请求暂停——等当前这一步收尾就停；"
                                 "已完成的产出都会保留，随时可点「⏩ 继续」接着干。")
        except Exception:
            pass

    def _fill_steps(self, proj: dict):
        self.team_steps.blockSignals(True)
        self.team_steps.clear()
        for i, s in enumerate(proj.get("steps") or [], 1):
            r = TEAM.role_of(str(s.get("role", ""))) if TEAM else None
            tag = "🔍" if s.get("review") else ("↩️" if s.get("conditional") else "")
            st = s.get("last_status", "")
            mark = "✅" if st == "done" else ("⚠️" if st == "fail" else "")
            it = QListWidgetItem("%d. %s %s %s%s — %s" % (
                i, tag, (r or {}).get("emoji", "❓"), s.get("role", "?"), mark,
                (s.get("task_hint") or "")[:24]))
            it.setData(0x0100, i - 1)          # 指向步骤下标
            out = str(s.get("last_out") or "").replace("\n", " ")[:160]
            stxt = {"done": "上次执行：完成✅", "fail": "上次执行：失败⚠️"}.get(st, "还没执行过")
            tip = "状态：%s\n任务：%s\n最近产出：%s" % (stxt, s.get("task_hint") or "", out or "（无）")
            it.setToolTip(tip)
            self.team_steps.addItem(it)
        self.team_steps.blockSignals(False)

    def _team_llm(self):
        """绑定到当前大脑的 llm_fn(system, user, task=...)。"""
        ep = self._endpoint()
        base, model, key = ep if ep else ("", "", "")

        def fn(system, user, task="skill"):
            return self._llm_call(system, user, base, model, key,
                                  max_tokens=3000, temperature=0.6, task=task)
        return fn

    def _work_llm(self):
        """工作链路用的 `llm_fn(prompt) -> str`（广告文案这类"要真写东西"的场景）。

        与 `_team_llm` 的区别：签名是**单参数 prompt**（`ad_design` / 媒体侧的约定），
        并固定 `task="copy"`（创作档参数：别发散、别解释、别客套）。

        v0.30.6 为什么必须补这个：`ad_design.copywrite()` 一直支持 `llm_fn`，
        但**从没人传** —— 于是"广告设计"产出的其实是写死的模板句，界面还显示完成。
        现在把模型接上；模型不可用时返回空串，下游走离线模板并**如实标注来源**。
        """
        ep = self._endpoint()
        base, model, key = ep if ep else ("", "", "")

        def fn(prompt):
            try:
                return self._llm_call(
                    "你是资深广告文案，只输出要求的内容，不要解释、不要客套。",
                    prompt, base, model, key,
                    max_tokens=400, temperature=0.7, task="copy")
            except Exception:
                logging.exception("work llm failed")
                return ""

        return fn

    def _team_run(self):
        if TEAM is None:
            QMessageBox.information(self, "团队", "团队模块未加载。")
            return
        proj = self._team_cur_proj()
        task = self.team_task.toPlainText().strip()
        if not task:
            QMessageBox.information(self, "团队", "先写清要让团队做什么（本次任务）。")
            return
        if not proj.get("steps"):
            QMessageBox.information(self, "团队",
                                    "项目流程是空的——先把成员编入项目（上方按钮），"
                                    "再在右侧给每位成员「加入流程」定义任务。")
            return
        proj["goal"] = task
        TEAM.save_project(proj)
        self._team_begin_busy()
        self.team_out.setPlainText("👥 项目组「%s」开工中…\n" % proj.get("name", ""))
        pid = proj.get("id", "")
        try:
            pmem = TEAM.project_memory_of(pid)      # v0.27 项目级跨会话记忆
        except Exception:
            pmem = ""

        def worker():
            def prog(m):
                self._ui(lambda mm=m: self.team_out.append(str(mm)))
            try:
                res = TEAM.run_team("项目流程", task, self._team_llm(), progress=prog,
                                    steps=proj.get("steps"), project_id=pid,
                                    project_memory=pmem,
                                    should_pause=lambda: self._team_pause_flag.is_set())
            except Exception as ex:
                res = {"ok": False, "summary": "执行异常：%s" % ex, "steps": [], "run_dir": ""}
            self._ui(lambda: self._team_done(res, pid, task))
        threading.Thread(target=worker, daemon=True).start()

    def _team_done(self, res: dict, pid: str = "", task: str = ""):
        """跑完：把每步执行状态与产出回写进项目空间，并展示最新档案。"""
        # v0.27.7：收尾解除"执行中"——恢复开工/继续，收起暂停键
        self.team_busy = False
        self.team_go.setEnabled(True)
        try:
            self.team_pause.setVisible(False)
            self.team_pause.setEnabled(False)
            self.team_pause.setText("⏸ 暂停员工工作")
            self._team_pause_flag.clear()
        except Exception:
            pass
        try:
            proj = TEAM.project_of(pid) if pid else self._team_cur_proj()
            if proj:
                st = res.get("steps") or []
                steps = proj.get("steps") or []
                for i, s in enumerate(steps):
                    if i < len(st):
                        out = str(st[i].get("text") or "")
                        s["last_status"] = "fail" if out.startswith("⚠") else "done"
                        s["last_out"] = out[:400]
                        s["last_ts"] = time.strftime("%m-%d %H:%M")
                s_ts = time.strftime("%m-%d %H:%M")
                # v0.27：引擎 run_team(project_id=...) 已写入一条 run（含 ts/note），
                # 这里只补充摘要信息到同一条，不再重复 append（修复双重记录）
                runs = proj.setdefault("runs", [])
                target = None
                rd = str(res.get("run_dir") or "")
                for r in reversed(runs):
                    if rd and str(r.get("run_dir") or "") == rd:
                        target = r
                        break
                if target is None:
                    target = {"run_dir": rd, "ts": s_ts,
                              "note": (task or "")[:80],
                              "ok": bool(res.get("ok"))}
                    runs.append(target)
                target.update({"t": s_ts, "task": (task or "")[:80],
                               "ok": bool(res.get("ok")),
                               "summary": str(res.get("summary") or "")[:1200],
                               "run_dir": rd})
                proj["runs"] = runs[-29:]
                TEAM.save_project(proj)
                # v0.27 经验沉淀：成功且流程 ≥2 步 → 自动存为流程预设（下次一键复用）
                if res.get("ok") and len(steps) >= 2:
                    tpl_name = "%s·班底流程" % str(proj.get("name") or "")[:20]
                    try:
                        TEAM.save_flow_template(tpl_name, steps,
                                                source="项目「%s」验证通过" % proj.get("name", ""))
                    except Exception:
                        logging.exception("flow template save failed")
        except Exception:
            logging.exception("team_done save failed")
        # 档案区展示最新效果（含本次 summary/步骤状态/历次）
        try:
            self._team_refresh()
        except Exception:
            pass
        # v0.27.7：提示区分 成功 / 被中止（模型忙等） / 被暂停
        _n_done = len(res.get("steps") or [])
        if res.get("aborted"):
            self.chat.append("<span style='color:#b45309'>👥 团队在第 %d 步<b>中止</b> —— "
                             "那一步没拿到有效产出（多为模型忙 / 排队超时）。"
                             "<b>后续步骤已停止，不再空跑</b>（避免每步白等几分钟）。"
                             "已完成的产出都留着，去「👥 团队」点「⏩ 继续上次未完成的执行」"
                             "就能从这里接着干。</span>" % (_n_done + 1))
        elif res.get("paused"):
            self.chat.append("<span style='color:#b45309'>👥 团队<b>已暂停</b> —— "
                             "已完成的产出都保留着，随时可点「⏩ 继续」接着跑。</span>")
        else:
            self.chat.append("<span style='color:#64748b'>👥 项目组任务完成（%s）——"
                             "完整过程与历次效果都在「👥 团队」页档案里，产物已落盘。</span>"
                             % ("成功" if res.get("ok") else "有步骤失败"))

    # —— 角色卡增删查（含 v0.25 评分体系） ——
    def _team_role_view(self, it):
        name = it.data(0x0100) if it is not None else ""
        r = TEAM.role_of(name) if TEAM and name else None
        if not r:
            return
        rat = TEAM.rating_of(name)
        notes = "\n".join("  · " + n for n in (rat.get("notes") or [])[-5:]) or "  （暂无）"
        info = ("%s %s\n\n职责：%s\n\n接收：%s\n\n产出：%s\n\n红线：%s\n\n擅长：%s\n\n来源：%s"
                "\n\n—— 评分 ——\n%s（共 %d 次，成功 %d）\n最近评语：\n%s"
                % (r.get("emoji", ""), r["name"], r.get("duty", ""), r.get("input", ""),
                   r.get("output", ""), r.get("redline", ""), r.get("skills", ""),
                   r.get("from", "内置"), TEAM.rating_line(name),
                   rat.get("runs", 0), rat.get("ok", 0), notes))
        box = QMessageBox(self)
        box.setWindowTitle("员工卡 · " + r["name"])
        box.setText(info)
        star_b = box.addButton("⭐ 给 TA 打分", QMessageBox.AcceptRole)
        box.addButton("关闭", QMessageBox.RejectRole)
        box.exec()
        if box.clickedButton() is star_b:
            stars, ok = QInputDialog.getInt(
                self, "成员评分", "给「%s」打几分？（1-5 星；评分低就考虑换更好的成员）" % r["name"],
                int(round(TEAM.rating_of(name).get("score", 4.0))), 1, 5)
            if ok:
                TEAM.rate_manual(name, stars, note="手动评价")
                self._team_refresh()
                self._append("系统", "已给「%s」评 %d 星——评分用于择优换人。" % (name, stars))

    def _team_role_view_cur(self):
        it = self.team_roles.currentItem()
        if it:
            self._team_role_view(it)

    def _team_role_menu(self, gpos):
        """项目成员右键：查看员工卡/打分 or 移出本项目（人才库档案保留）。"""
        it = self.team_roles.itemAt(self.team_roles.viewport().mapFromGlobal(gpos))
        if not it or TEAM is None:
            return
        self.team_roles.setCurrentItem(it)
        name = it.data(0x0100) or ""
        if not name:
            return
        menu = QMenu(self)
        a_view = menu.addAction("👁 员工卡 / ⭐ 打分")
        a_del = menu.addAction("🚪 移出本项目（人才库保留）")
        chosen = menu.exec(gpos)
        if chosen is a_view:
            self._team_role_view(it)
        elif chosen is a_del:
            self._team_role_del()

    def _team_role_add(self):
        if TEAM is None:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle("添加角色（员工卡）")
        dlg.resize(520, 380)
        v = QVBoxLayout(dlg)
        eds = {}
        for key, lab in (("name", "名字（必填，如：文案）"),
                         ("duty", "职责（一句话：他负责什么）"),
                         ("input", "接收什么（上游给他什么）"),
                         ("output", "产出什么（必须输出什么才算完成）"),
                         ("redline", "质量红线（违反即不合格）")):
            e = QLineEdit()
            v.addWidget(QLabel(lab))
            v.addWidget(e)
            eds[key] = e
        h = QHBoxLayout()
        okb = QPushButton("保存")
        cab = QPushButton("取消")
        h.addStretch(1)
        h.addWidget(okb)
        h.addWidget(cab)
        v.addLayout(h)
        okb.clicked.connect(dlg.accept)
        cab.clicked.connect(dlg.reject)
        if dlg.exec() != QDialog.Accepted:
            return
        card = {k: e.text().strip() for k, e in eds.items()}
        if not card["name"]:
            QMessageBox.information(self, "添加角色", "名字不能为空。")
            return
        card["emoji"] = "🧑‍💼"
        ok = TEAM.add_role(card)
        if ok:
            try:
                self._team_add_member(card["name"])   # 建岗即编入当前项目
            except Exception:
                pass
        self._team_refresh()
        self._append("系统", "已添加岗位「%s」并编入本项目%s。" % (
            card["name"], "✅" if ok else "❌（与内置角色重名或保存失败）"))

    def _team_role_del(self):
        """v0.26.3：从当前项目移出成员（不改人才库档案，随时可再编入）。"""
        if TEAM is None:
            return
        it = self.team_roles.currentItem()
        if not it:
            return
        name = it.data(0x0100)
        if not name:
            return
        proj = self._team_cur_proj()
        mbrs = proj.get("members") or []
        if name not in mbrs:
            return
        mbrs.remove(name)
        TEAM.save_project(proj)
        self._team_refresh()
        self._append("系统", "已把「%s」移出项目「%s」——人才库档案保留，之后可随时再编入。"
                     % (name, proj.get("name", "")))

    # —— 项目空间与流程编排（v0.25） ——
    def _team_proj_new(self):
        if TEAM is None:
            return
        name, ok = QInputDialog.getText(self, "新建项目", "项目名（如：记账小工具 / 毕业论文）：")
        if not ok or not name.strip():
            return
        goal, _g = QInputDialog.getMultiLineText(
            self, "新建项目", "项目目标（要做成什么样）：", "")
        proj_new = TEAM.new_project(name.strip(), (goal or "").strip())
        self._team_refresh()
        # v0.27.6：新建后**自动切到新项目**——旧版只重建下拉、保持原选择，
        #   于是刚建好的项目在界面上"看不见"，用户会以为没建成。
        try:
            _i = self.team_proj.findData((proj_new or {}).get("id", ""))
            if _i >= 0:
                self.team_proj.setCurrentIndex(_i)
                self._team_refresh()
        except Exception:
            pass
        self._append("系统", "📁 新项目「%s」已建立——已在团队页切到它；"
                             "把成员加入流程即可开工。" % name.strip())

    def _team_proj_del(self):
        proj = self._team_cur_proj()
        if not proj.get("id"):
            return
        if QMessageBox.question(
                self, "删除项目", "确定删除项目「%s」？流程与执行记录一并删除。"
                % proj.get("name", "")) != QMessageBox.Yes:
            return
        TEAM.delete_project(proj["id"])
        self._team_refresh()
        self._append("系统", "已删除项目「%s」。" % proj.get("name", ""))

    def _team_steps_save(self):
        """拖拽排序后把新顺序写回项目（item data = 原步骤下标）。"""
        proj = self._team_cur_proj()
        if not proj:
            return
        old = proj.get("steps") or []
        steps = []
        for i in range(self.team_steps.count()):
            d = self.team_steps.item(i).data(0x0100)
            if isinstance(d, int) and 0 <= d < len(old):
                steps.append(old[d])
        proj["steps"] = steps or old
        TEAM.save_project(proj)
        self._fill_steps(proj)                 # 重排序号

    def _team_step_add(self):
        it = self.team_roles.currentItem()
        if it is None or TEAM is None:
            QMessageBox.information(self, "加入流程", "先在左侧点选一位成员。")
            return
        proj = self._team_cur_proj()
        if not proj:
            return
        name = it.data(0x0100)
        role = TEAM.role_of(name) or {}
        hint, _t = QInputDialog.getText(
            self, "加入流程", "「%s」这一步要做什么（任务提示）：" % name,
            text=str(role.get("duty", ""))[:40])
        proj.setdefault("steps", []).append(
            {"role": name, "task_hint": (hint.strip() or role.get("duty", ""))[:80]})
        TEAM.save_project(proj)
        self._fill_steps(proj)

    def _team_step_edit(self, it):
        proj = self._team_cur_proj()
        d = it.data(0x0100) if it is not None else None
        steps = proj.get("steps") or []
        if not isinstance(d, int) or d >= len(steps):
            return
        cur = steps[d]
        text, ok = QInputDialog.getText(
            self, "修改任务", "「%s」这一步要做什么：" % cur.get("role", "?"),
            text=str(cur.get("task_hint", "")))
        if ok:
            cur["task_hint"] = text.strip()[:80]
            TEAM.save_project(proj)
            self._fill_steps(proj)

    def _team_step_del(self):
        it = self.team_steps.currentItem()
        if it is None:
            return
        proj = self._team_cur_proj()
        d = it.data(0x0100)
        steps = proj.get("steps") or []
        if isinstance(d, int) and 0 <= d < len(steps):
            steps.pop(d)
            TEAM.save_project(proj)
            self._fill_steps(proj)

    def _team_web_add(self):
        """网上招聘：抓取该岗位的公开资料 → LLM 提炼成员工卡 → 入职。"""
        if TEAM is None:
            return
        kw, ok = QInputDialog.getText(
            self, "🌐 网上招聘", "想要什么岗位/能力？（如：SEO 优化 / 数据分析师 / 插画师）")
        if not ok or not kw.strip():
            return
        kw = kw.strip()
        self._append("系统", "🌐 正在网上找「%s」的岗位资料并提炼员工卡…" % kw)
        def worker():
            text = ""
            try:
                import agent_tools as AT
                text = AT.fetch_topic_text(
                    kw + " 岗位职责 工作方法论 最佳实践", max_chars=9000) or ""
            except Exception:
                text = ""
            if text:
                ok2, card = TEAM.make_role_from_text(kw, text, self._team_llm())
            else:
                ok2, card = False, "没抓到资料（网络不可用或被拦截），稍后再试。"
            def done():
                if ok2:
                    if TEAM.add_role(card):
                        try:
                            self._team_add_member(card["name"])   # 招聘即入职当前项目
                        except Exception:
                            pass
                        self._team_refresh()
                        self._append("系统", "🎉 新员工入职：%s %s（已编入本项目）—— %s"
                                     % (card.get("emoji", ""), card["name"],
                                        str(card.get("duty", ""))[:50]))
                    else:
                        # 重名：直接编入本项目
                        if self._team_add_member(card["name"]):
                            self._team_refresh()
                            self._append("系统", "🎉「%s」已在人才库——已编入本项目。" % card["name"])
                        else:
                            self._append("系统", "❌ 入职失败（与现有成员重名？）。")
                else:
                    self._append("系统", "❌ 网上招聘失败：" + str(card))
            self._ui(done)
        threading.Thread(target=worker, daemon=True).start()

    def _team_skill_add(self):
        """技能库选才：技能（expert/skill/connector）→ 员工卡。"""
        if TEAM is None:
            return
        skills = SKL.list_skills()
        if not skills:
            QMessageBox.information(self, "技能库选才", "技能库还是空的——先到「🎓 技能」页添加技能。")
            return
        names = ["%s [%s] %s" % (s["name"], s.get("type", "skill"),
                                 (s.get("description") or "")[:24]) for s in skills]
        sel, ok = QInputDialog.getItem(self, "🎓 技能库选才",
                                       "选一个技能转化为员工（技能即岗位能力）：", names, 0, False)
        if not ok:
            return
        idx = names.index(sel)
        card = TEAM.role_from_skill(skills[idx])
        if TEAM.add_role(card):
            try:
                self._team_add_member(card["name"])   # 选才进组
            except Exception:
                pass
            self._team_refresh()
            self._append("系统", "🎓 已把技能「%s」转化为员工「%s」并编入本项目。" % (skills[idx]["name"], card["name"]))
        else:
            # 重名：可能该岗位已在人才库 → 直接把它编入本项目
            if self._team_add_member(card["name"]):
                self._team_refresh()
                self._append("系统", "🎓 「%s」已在人才库——已编入本项目。" % card["name"])
            else:
                self._append("系统", "❌ 添加失败（重名？）。")

    def _team_from_chat(self, text: str) -> bool:
        """对话触发：让我的团队做 X → 用当前项目开工；当前无流程则自动组一个"即兴项目"。"""
        m = re.match(r"^让(我的)?团队(.{0,40}?)做[:：]?\s*(.+)", text.strip())
        if not m or TEAM is None:
            return False
        task = m.group(3).strip() or text.strip()
        proj = self._team_cur_proj()
        if not proj.get("steps") or not proj.get("members"):
            # v0.26.3：新项目空白，聊天直接使唤 → 自动起一支内置班底干活（不污染手动建的空项目）
            auto = True
            proj = TEAM.new_project("即兴任务·" + task[:8], task[:60])
            steps = [dict(s) for s in TEAM.SEED_FLOWS[0]["steps"]]
            seed_names = [r["name"] for r in TEAM.SEED_ROLES]
            proj["members"] = [s["role"] for s in steps
                               if s["role"] in seed_names]
            proj["steps"] = steps
            TEAM.save_project(proj)
            # 下拉选中这个新项目
            self.team_proj.addItem("📁 " + proj["name"], proj["id"])
            self.team_proj.setCurrentIndex(self.team_proj.count() - 1)
        self._append("你", html.escape(text))
        self._append("系统", "👥 收到——项目组「%s」开工：\n%s\n\n（可到「👥 团队」页看过程、状态与历次效果）"
                     % (proj.get("name", ""), task[:100]))
        self.team_task.setPlainText(task)
        self._team_run()
        self._switch_page("team")
        return True

    # ---------- 资料库页 ----------
    def _page_kb(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        head.addWidget(self._page_title("📚 资料库 · 它学过的知识"))
        head.addStretch(1)
        lb = QPushButton("让它学一个新主题")
        lb.setFixedHeight(26)
        lb.clicked.connect(self._kb_learn_ui)
        head.addWidget(lb)
        # v0.30.8：资料库现在是一堆真正的 .md 笔记 —— 可以直接用 Obsidian
        # 「打开文件夹作为库」浏览、双链跳转、全文搜索（没装 Obsidian 也能当普通
        # Markdown 文件夹用）。用户的原话：「只记录了要点，达不到资料库的作用」。
        ob = QPushButton("📂 打开资料库")
        ob.setFixedHeight(26)
        ob.setToolTip("用文件管理器打开资料库文件夹 —— 可用 Obsidian 打开为库")
        ob.clicked.connect(self._kb_open_vault)
        head.addWidget(ob)
        rb = QPushButton("↻ 重建笔记")
        rb.setFixedHeight(26)
        rb.setToolTip("把全部知识重新导出为 Markdown 笔记 + 目录页（内容没变不重写）")
        rb.clicked.connect(self._kb_rebuild_vault)
        head.addWidget(rb)
        lay.addLayout(head)
        lay.addSpacing(6)
        self.kb_search = QLineEdit()
        self.kb_search.setPlaceholderText("🔍 按标题 / 分类 / 内容关键词过滤…")
        self.kb_search.setStyleSheet(
            "QLineEdit{border:1px solid #e2e8f0;border-radius:8px;"
            "padding:4px 8px;font-size:12px;color:#0f172a;background:#ffffff;}"
            "QLineEdit:focus{border-color:#5b8def;}")
        self.kb_search.textChanged.connect(self._kb_refresh)
        lay.addWidget(self.kb_search)
        lay.addSpacing(4)
        self.kb_list = QListWidget()
        self.kb_list.setStyleSheet(self.NAV_CSS)
        self.kb_list.itemDoubleClicked.connect(self._kb_show)
        lay.addWidget(self.kb_list, 1)
        bl = QLabel("书架资料 · 完整原文（双击打开全文）")
        bl.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(bl)
        self.kb_books = QListWidget()
        self.kb_books.setStyleSheet(self.NAV_CSS)
        self.kb_books.setMaximumHeight(118)
        self.kb_books.itemDoubleClicked.connect(self._kb_book_show)
        lay.addWidget(self.kb_books)
        hint = QLabel("双击条目或书架 = 打开它真正学到的内容（含抓回的原文全文）。"
                      "点右上按钮或说「上网学一下 xx」，它会自己搜多个来源，把完整内容存进知识库。\n"
                      "📂 资料库是一堆标准 .md 笔记：可用 **Obsidian**「打开文件夹作为库」"
                      "浏览 / 双链跳转 / 全文搜索（不装 Obsidian 也能当普通 Markdown 文件夹用）。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(hint)
        return page

    def _kb_open_vault(self):
        """打开资料库文件夹（会先确保笔记是最新的）。"""
        try:
            import knowledge as K
            r = K.open_vault()
            d, st = r.get("dir") or "", K.vault_stats()
            if not r.get("ok"):
                self._append("系统", "打开资料库失败：%s<br>路径：<code>%s</code>"
                             % (html.escape(str(r.get("err"))), html.escape(d)))
                return
            if r.get("opened"):
                self._append("系统",
                             "📂 已打开资料库：<code>%s</code><br>共 %d 篇笔记，"
                             "全文约 %d 字。<br>想看更舒服的话：用 <b>Obsidian</b>"
                             "「打开文件夹作为库」，双链和全文搜索都能用；"
                             "没装也没关系 —— 这里的 <code>.md</code> 任何编辑器都能打开。"
                             % (html.escape(d), st["notes"], st["chars"]))
                return
            # 笔记是好的，只是系统没把窗口唤起来。**这里必须说实话** ——
            # 否则用户去任务栏找不到窗口，会以为资料库丢了（v0.30.8）。
            self._append("系统",
                         "📂 笔记已更新（共 %d 篇），但没能自动打开文件夹"
                         "（%s）。<br>手动打开这个路径即可：<code>%s</code>"
                         % (st["notes"], html.escape(str(r.get("err")) or "未知原因"),
                            html.escape(d)))
        except Exception as ex:
            self._append("系统", "打开资料库失败：%s" % html.escape(str(ex)))

    def _kb_rebuild_vault(self):
        """重建全部笔记 + 目录页（幂等：内容没变就不重写）。"""
        try:
            import knowledge as K
            r = K.export_vault()
            if not r.get("ok"):
                self._append("系统", "重建笔记失败：" + html.escape(str(r.get("err"))))
                return
            if r["wrote"] == 0:
                # v0.30.10：内容没变化是常态（幂等重建）——明确告诉用户"已是最新"，
                # 否则只看到"写了 0 个文件"会以为按钮坏了（小志反馈"重建笔记好像不起作用"）。
                self._append("系统",
                             "↻ 资料库已是最新：%d 篇笔记均在、且内容与学习记录一致，"
                             "无需重写。<br>若想强制刷新，可在资料库目录手动删除 .md 后重试。<br>"
                             "目录：<code>%s</code>"
                             % (r["notes"], html.escape(r["dir"])))
            else:
                self._append("系统",
                             "↻ 资料库已重建：%d 篇笔记，本次新写入 %d 个文件"
                             "（其余内容没变故跳过）。<br>目录：<code>%s</code>"
                             % (r["notes"], r["wrote"], html.escape(r["dir"])))
            try:
                self._kb_refresh()
            except Exception:
                pass
        except Exception as ex:
            self._append("系统", "重建笔记失败：%s" % html.escape(str(ex)))

    def _kb_refresh(self):
        self.kb_list.clear()
        q = (self.kb_search.text() or "").strip().lower()
        know = knowledge.entries()
        know.sort(key=lambda x: x.get("date", ""), reverse=True)
        for e in know:
            hay = ((e.get("title") or "") + " " + (e.get("cat") or "") + " " +
                   " ".join(e.get("bullets", [])) + " " +
                   (e.get("text") or "")[:2000]).lower()
            if q and q not in hay:
                continue
            cat = f"[{e.get('cat','通用')}]" if e.get("cat") else ""
            conf = e.get("conf", 0.6)
            tx = e.get("text") or ""
            tnote = f"原文 {len(tx)} 字" if tx else "仅要点（原文待补）"
            it = QListWidgetItem(f"📖 {e['title']}  {cat}  掌握 {conf:.0%} · "
                                 f"{len(e.get('bullets', []))} 条要点 · {tnote}")
            it.setData(Qt.UserRole, e["title"])
            self.kb_list.addItem(it)
        if not self.kb_list.count():
            self.kb_list.addItem("（没有匹配的知识条目——说「上网学一下 xx」学完会出现在这里）")
        # 书架：完整原文资料，双击可读全文（含还没提炼成要点的内容）
        try:
            books = knowledge.list_books()
        except Exception:
            books = []
        self.kb_books.clear()
        learned = {x.get("title", "").lower() for x in know}
        for b in books:
            if b.endswith(".txt"):
                stem = b[:-4]
            elif b.endswith(".md"):
                stem = b[:-3]
            else:
                stem = b
            mark = "✅ " if stem.lower() in learned else "📕 "
            it = QListWidgetItem(mark + stem)
            it.setData(Qt.UserRole, b)
            self.kb_books.addItem(it)

    def _kb_open_reader(self, title, bullets, text, src, cat, date,
                        conf, n_read, related, note=""):
        """知识阅读器：要点（索引）+ 原文（真正学到的内容）。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("《" + title + "》")
        dlg.resize(560, 500)
        lay = QVBoxLayout(dlg)
        meta = []
        if note:
            meta.append(note)
        if cat:
            meta.append("分类 " + cat)
        if conf:
            meta.append("掌握 " + f"{conf:.0%}")
        meta.append("读过 " + str(n_read) + " 次")
        if src:
            meta.append("来源 " + src)
        if date:
            meta.append("时间 " + date)
        if related:
            meta.append("关联 " + related)
        cap = QLabel(" · ".join(meta))
        cap.setWordWrap(True)
        cap.setStyleSheet("color:#64748b;font-size:11px")
        lay.addWidget(cap)
        tb = QTextBrowser()
        tb.setStyleSheet(
            "QTextBrowser{border:1px solid #e2e8f0;border-radius:8px;background:#ffffff;"
            "padding:8px;font-size:13px;color:#0f172a;}")
        parts = []
        if bullets:
            parts.append("## 📌 要点（速记索引）")
            parts.append("")
            parts.extend("- " + b for b in bullets)
            parts.append("")
        if text:
            parts.append("## 📄 原文 / 学到的内容")
            parts.append("")
            # v0.22.1：展示用整理排版（超长行折行/压空行）；复制仍给原始全文
            parts.append(knowledge.pretty_text(text))
        if not parts:
            parts.append("（这条暂时没有内容——可能是早期版本只存了标题。"
                         "对它说「上网学一下 " + title + "」即可补全原文。）")
        body_html = md_to_html("\n".join(parts))
        # 阅读器专属全局样式（浅色卡片式，贴合主题；v0.22.1 加行距/断行，防挤成一团）
        tb.setHtml(
            "<div style='font-family:Microsoft YaHei,Segoe UI,sans-serif;"
            "font-size:14px;line-height:1.8;color:#0f172a;"
            "word-break:break-word;overflow-wrap:anywhere;'>" + body_html + "</div>")
        # v0.22 Obsidian 式双链：关联条目可点击跳转（[[双链]]导航）
        rel_list = [r.strip() for r in (related or "").split("、") if r.strip()] \
            if isinstance(related, str) else list(related or [])
        if rel_list:
            links = "　".join(
                f"<a href='kb://{html.escape(r)}' style='color:#5b8def;text-decoration:none;'>"
                f"《{html.escape(r)}》</a>" for r in rel_list[:4])
            tb.append("<div style='margin-top:10px;font-size:13px;'>🔗 相关知识：" + links + "</div>")
        tb.setOpenLinks(False)

        def _nav(url):
            try:
                s = url.toString()
            except Exception:
                s = str(url)
            if not s.startswith("kb://"):
                return
            t = s[5:]
            e2 = knowledge.find_entry(t)
            if e2:
                dlg.accept()
                self._kb_show_entry(e2)
            else:
                cap.setText(f"资料库里还没有《{t}》的条目——说「上网学一下 {t}」就能补上。")
        tb.anchorClicked.connect(_nav)
        tb.moveCursor(QTextCursor.Start)
        lay.addWidget(tb, 1)
        btns = QHBoxLayout()
        if text:
            cp = QPushButton("📋 复制全文")
            cp.clicked.connect(lambda: (QApplication.clipboard().setText(text),
                                        cap.setText("已把全文复制到剪贴板 ✓")))
            btns.addWidget(cp)
        ok = QPushButton("关闭")
        ok.clicked.connect(dlg.accept)
        btns.addStretch(1)
        btns.addWidget(ok)
        lay.addLayout(btns)
        dlg.exec()

    def _kb_show(self, item):
        title = item.data(Qt.UserRole) or ""
        if not title:
            return
        e = knowledge.find_entry(title)
        if not e:
            return
        self._kb_show_entry(e)

    def _kb_show_entry(self, e: dict):
        """按条目打开知识阅读器（双链跳转复用同一入口）。"""
        title = e["title"]
        text = e.get("text") or ""
        if not text:                        # 早期条目没存原文 → 书架同名 .md 书补全显示
            try:
                bpath = os.path.join(knowledge.BOOKS_DIR, title + ".md")
                if os.path.exists(bpath):
                    text = knowledge.read_book(title)
            except Exception:
                pass
        self._kb_open_reader(title, e.get("bullets", []) or [], text,
                             e.get("src", ""), e.get("cat", ""), e.get("date", ""),
                             e.get("conf", 0.6), e.get("n_read", 1),
                             "、".join(e.get("related", [])) or "")

    def _kb_book_show(self, item):
        b = item.data(Qt.UserRole) or ""
        if not b:
            return
        if b.endswith(".txt"):
            stem = b[:-4]
        elif b.endswith(".md"):
            stem = b[:-3]
        else:
            stem = b
        text = knowledge.read_book(b) or ""
        e = knowledge.find_entry(stem)
        self._kb_open_reader(stem, (e or {}).get("bullets", []) or [], text,
                             "书架 " + b, (e or {}).get("cat", ""),
                             (e or {}).get("date", ""), (e or {}).get("conf", 0.0),
                             (e or {}).get("n_read", 0),
                             "、".join((e or {}).get("related", [])) or "",
                             note="书架原文（还没学成条目的原始资料）")


    def _kb_query_reply(self, q: str):
        """v0.22 Obsidian 式快速查询：聊天里 /查 关键词 直接出检索结果。"""
        try:
            hits = knowledge.search(q, 8)
        except Exception:
            hits = []
        if not hits:
            self._append("系统",
                         f"资料库里暂时没有与「{html.escape(q)}」相关的内容。"
                         f"可以说「上网学一下 {html.escape(q)}」让我学，学完就能查到了。")
            return
        marks = "①②③④⑤⑥⑦⑧"
        lines = [f"📚 资料库里与「{html.escape(q)}」相关的，找到 {len(hits)} 条：", ""]
        for i, e in enumerate(hits[:8]):
            cat = f"[{html.escape(e.get('cat', '通用'))}]" if e.get("cat") else ""
            lines.append(f"{marks[i]} **《{html.escape(e['title'])}》**{cat} "
                         f"掌握 {e.get('conf', 0.6):.0%}")
            for b in (e.get("bullets") or [])[:2]:
                lines.append("　· " + html.escape(str(b)[:64]))
            sn = e.get("snippet") or ""
            if sn:
                lines.append("　" + html.escape(sn[:160]))
            rel = e.get("related") or []
            if rel:
                lines.append("　↳ 关联：" + "、".join(
                    "《" + html.escape(r) + "》" for r in rel[:2]))
            lines.append("")
        lines.append("在「📚 资料库」双击条目可读全文原文，阅读器里的关联条目可点击跳转。")
        self._append("系统", md_to_html("\n".join(lines)))

    def _kb_learn_ui(self):
        """资料库「学一个新主题」：全程自主后台执行。

        v0.17.3 彻底移出聊天会话：不占用 busy（聊天不出现"莫名思考中"、
        输入框不禁用、左侧会话随时可切换）；开始只浮窗提示、完成弹结果框；
        学到的内容直接进知识库。唯一的界面限制是同一时间只学一个主题。
        """
        from qt_compat import QtWidgets
        QInputDialog = QtWidgets.QInputDialog
        topic, ok = QInputDialog.getText(self, "学新知识", "想让它上网学什么主题？")
        if not (ok and topic.strip()):
            return
        # v0.17.3/0.17.5：学习完全在自主后台，不占回合、不锁聊天——思考中也允许学
        if getattr(self, "_kb_running", False):
            QMessageBox.information(
                self, "已在学习中",
                "它正在后台学上一个主题，等学完弹窗提示后再开新的～")
            return
        self._kb_running = True
        topic = topic.strip()
        self._toast(f"正在后台学习《{topic}》…完成会弹窗提醒你（不占用聊天）。", ms=3200)

        def job():
            try:
                reply = self._agent_run(("weblearn", topic))
            except Exception as ex:
                logging.exception("kb learn failed")
                reply = "学习出错了：" + str(ex)
            self._ui(lambda: self._kb_learn_done(topic, reply))
        threading.Thread(target=job, daemon=True).start()

    def _kb_learn_done(self, topic: str, reply: str):
        self._kb_running = False
        # v0.22.1：学习结果弹窗改富文本——要点分条排版，不再挤成一团纯文本
        short = reply.replace("\n- ", "\n· ").strip()[:600]
        try:
            body = (f"**《{topic}》**\n\n" + short +
                    "\n\n打开「📚 资料库」可阅读/查看它学到的新知识。")
            box = QMessageBox(QMessageBox.Information, "学习结果", "", parent=self)
            box.setTextFormat(Qt.RichText)
            box.setText(md_to_html(body))
            box.exec()
        except Exception:
            QMessageBox.information(
                self, "学习结果",
                f"《{topic}》\n\n{short}\n\n打开「📚 资料库」可阅读/查看它学到的新知识。")
        try:
            self._kb_refresh()
        except Exception:
            pass

    # ---------- 记忆页 ----------
    def _page_mem(self):
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.addWidget(self._page_title("🧠 记忆 · 它记住的关于你"))
        lay.addSpacing(6)
        self.mem_list = QListWidget()
        self.mem_list.setStyleSheet(self.NAV_CSS)
        self.mem_list.itemDoubleClicked.connect(self._mem_del)
        lay.addWidget(self.mem_list, 1)
        hint = QLabel("双击 = 删除这条记忆；对话里说「记住我喜欢…」它也会自己记。")
        hint.setStyleSheet("color:#94a3b8;font-size:11px")
        lay.addWidget(hint)
        return page

    def _mem_refresh(self):
        self.mem_list.clear()
        try:
            c = ML.counts()
            line = f"📚 分层记忆：情景 {c['episodic']} 段 · 办事经验 {c['procedural']} 条"
            if c.get("semantic"):
                line += f" · 长期印象 {c['semantic']} 条"
            if c.get("topic") or c.get("goal"):
                line += f" · 正聊着：{(c.get('topic') or c.get('goal') or '')[:18]}"
            self.mem_list.addItem(line)
            # v0.29.0：把新增的两层也暴露出来（否则用户无法确认它们真的在工作）
            fa = c.get("facts") or {}
            if fa.get("facts_all"):
                self.mem_list.addItem(
                    f"📌 确定事实：当前有效 {fa.get('facts', 0)} 条"
                    f"（历史 {fa.get('facts_all', 0)} 条，已失效 "
                    f"{fa.get('superseded', 0) + fa.get('expired', 0)} 条）")
            wm = c.get("worldmodel") or {}
            if wm.get("observations"):
                self.mem_list.addItem(
                    f"🎯 行动预演：{wm.get('domains', 0)} 类处境 · "
                    f"{wm.get('cells', 0)} 个(处境×动作) · 经验 {wm.get('observations', 0)} 次")
            try:
                import memvec as MV
                st = MV.backend_status()
                if st.get("model"):
                    self.mem_list.addItem(
                        "🧠 语义嵌入：" + ("已启用 " if st.get("effective", "").startswith("ollama")
                                          else "待探测/已降级 ") + st.get("model", ""))
            except Exception:
                pass
        except Exception:
            pass
        for n in self.notes[-30:]:
            self.mem_list.addItem(f"🗒 [{n.get('t','')}] {n.get('tag','')}")
        for p in self.prefs[-30:]:
            self.mem_list.addItem(f"⭐ [{p.get('t','')}] [{p.get('cat','')}] {p.get('tag','')}")
        if not self.mem_list.count():
            self.mem_list.addItem("（还没有记忆：多聊聊天它就记住了）")

    def _mem_del(self, item):
        text = item.text()
        if text.startswith("🗒"):
            tag = text.split("] ", 1)[-1]
            self.notes = [n for n in self.notes if n.get("tag", "") != tag]
            _save_json(NOTES, self.notes)
        else:
            tag = text.split("] ", 1)[-1]
            self.prefs = [p for p in self.prefs if p.get("tag", "") != tag]
            _save_json(PREFS, self.prefs)
        self._mem_refresh()

    def _icon(self):
        base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        for p in (os.path.join(base, "assets", "icon.ico"), os.path.join(base, "icon.ico")):
            if os.path.exists(p):
                return QIcon(p)
        return QIcon()

    def _boot(self):
        """启动：自动接上本地 Ollama（免 Key），恢复上下文；同步成长状态。"""
        # v0.27.6：上次没跑完的活 → 判为"被硬中断"（程序退出/蓝屏），并如实告知。
        #   真机痛点：干活干到一半蓝屏，任务永远卡在"进行中"，看不出它其实早断了。
        try:
            if WORKLOG:
                _n_int = WORKLOG.mark_running_as_interrupted()
                if _n_int:
                    self._append("系统", "⚠️ 上次有 %d 件活没跑完（程序退出或蓝屏中断）——"
                                         "已在「🛠 工作台」标记为「⚠️ 已中断」。"
                                         "产出的文件都还在，看一眼就知道停在哪，"
                                         "需要的话把要求再说一遍重新开工即可。" % _n_int)
        except Exception:
            pass
        # 成长/皮肤与桌面小人共用 pet_state.json（同一个'它'）
        st = GROWTH.pet_state()
        self.avatar.set_growth(GROWTH.level_of(st.get("exp", 0)))
        self.avatar.set_skin(st.get("skin", "tech"))
        if not self.cfg.get("api_key"):
            self.local = detect_local_llm()
            if self.local:
                if not self.cfg.get("local_model"):
                    ms = list_ollama_models()
                    self.cfg["local_model"] = pick_local_model(ms)
                    _save_json(CONFIG, self.cfg)
                model = self.cfg.get("local_model") or ""
                # v0.27.9：显式选了本地模型（local:<name>）就预热「它本身」，
                # 别再按"最小模型"另选一个——否则预热与聊天用的不是同一个模型，
                # Ollama 会在两者间反复换载，首条回复反而更慢。
                _mc = str(self.cfg.get("model_choice") or "")
                _explicit = _mc[len("local:"):].strip() if _mc.startswith("local:") else ""
                if _explicit:
                    model = _explicit
                elif not self.cfg.get("model_locked"):
                    model = pick_local_model(list_ollama_models())
                if model and model != self.cfg.get("local_model"):
                    self.cfg["local_model"] = model
                    _save_json(CONFIG, self.cfg)
                self._set_status(f"本地 {self.local['name']} · {model or 'auto'}")
                self._append("系统", f"✅ 已自动连接本机 {self.local['name']}（免 Key 对话）。"
                                    f"模型：{model or '自动选择'}（正在后台预热，首条回复会更快）")
                self._warm_local(model)
            else:
                self._set_status("离线微脑（未检测到本地模型）")
                self._append("系统", "未检测到本地模型：安装 Ollama 并运行 `ollama run qwen2.5:1.5b` 即可免 Key 对话；"
                                    "当前先用离线微脑陪你（照样记住你、会长大）。")
        else:
            self._set_status(f"云端 · {self.cfg.get('model')}")
        self._refresh_model_status()             # 大脑切换下拉与实际生效模型同步
        self._ensure_local_warm()                # v0.29.1 本地模型驻留兜底（不论哪个分支）
        # v0.22.4：勾选了「随 PASM 启动 ComfyUI」→ 后台拉起（不阻塞界面，失败静默）
        try:
            self._maybe_comfy_auto()
        except Exception:
            pass
        if self.history:
            self._append("系统", f"已恢复上次对话上下文（{len(self.history)} 条）。")
            self._replay_history()
            self._render_mind(self.agent.snapshot())
            return
        snap = self.agent.snapshot()
        self._append(self.cfg["name"], f"我是{self.cfg['name']}，性格偏「{self.cfg['persona']}」。"
                                       "我会记住我们的相处，慢慢长成自己的样子。先聊聊你今天过得怎么样？")
        self._render_mind(snap)

    # ⚠️ 这里原本还有第二份 `_set_status`（只 setText、**不**记 `_status_base`），
    #    与下面那份重名 → 被静默覆盖成死代码，改它等于没改。v0.30.13 删除。

    def _toast(self, msg: str, ms: int = 2600):
        """轻提示：右下角小浮窗自动消失。不进聊天框、不污染当前对话记录。

        用于切栏目 / 切模型 / 学习完成 这类系统状态提示。
        """
        try:
            if not hasattr(self, "_toast_lbl") or self._toast_lbl is None:
                lbl = QLabel(self)
                lbl.setWordWrap(True)
                lbl.setStyleSheet(
                    "QLabel{background:#0f172a;color:#e2e8f0;border-radius:8px;"
                    "padding:10px 16px;font-size:12px;}")
                lbl.setAttribute(Qt.WA_TransparentForMouseEvents)
                lbl.hide()
                self._toast_lbl = lbl
            lbl = self._toast_lbl
            lbl.setText(msg)
            lbl.adjustSize()
            w = min(lbl.width() + 24, max(180, self.width() - 40))
            lbl.setFixedWidth(w)
            lbl.adjustSize()
            lbl.move(self.width() - lbl.width() - 22,
                     self.height() - lbl.height() - 52)
            lbl.show()
            lbl.raise_()
            if hasattr(self, "_toast_timer"):
                self._toast_timer.stop()
            self._toast_timer = QTimer(self)
            self._toast_timer.setSingleShot(True)
            self._toast_timer.timeout.connect(lbl.hide)
            self._toast_timer.start(ms)
        except Exception:
            pass

    # ---------- 引擎：对话 → 成长 ----------
    def _grow(self, score: float, text: str):
        # v0.26.2：情绪反馈系数加大——你这条消息是开心/抱怨会明显带动它的心情
        # （此前 0.35 且被 6 步随机游走稀释，用户感知不到情绪在变化）
        rw = 0.85 * score if abs(score) > 0.02 else 0.0
        # v0.17.5：RL 微训练只作"成长点缀"——torch 版本差异偶发 inplace 梯度报错，
        # 绝不让它拖垮整条对话：失败仅记日志，快照仍可正常返回。
        try:
            for i in range(6):
                obs = self.env._get_obs()
                a, rep = self.agent.act(obs)
                nxt, r, done, _ = self.env.step(a)
                # 第一步把本条消息的情绪冲击一次给足（不再每步重复累加撞顶），
                # 后几步只留微弱随机心跳，让它自然回归/波动
                reward = rw if i == 0 else r * 0.5
                self.agent.learn(obs, a, nxt, reward, rep)
                if done:
                    self.env.reset(); self.agent.reset_episode()
        except Exception:
            logging.exception("agent grow skipped")
        snap = self.agent.snapshot()
        if abs(score) >= 0.3:
            tag = f"{'（你心情不错）' if score > 0 else '（你有点不开心）'}你聊到了：{text.strip()[:28]}"
            self.notes.append({"t": time.strftime("%m-%d %H:%M"), "tag": tag})
            self.notes = self.notes[-200:]
            _save_json(NOTES, self.notes)
        return snap

    # ---------- 语言脑（统一走 LLM 网关：客户端复用/超时重试/本地并发闸） ----------
    def _llm_call(self, system, user, base_url, model, api_key, max_tokens=1500,
                  temperature=0.9, task="chat", hist=None, stop=None,
                  on_delta=None, on_think=None) -> str:
        hist = self._ctx_msgs(hist)
        # 避免 send() 已把本轮 user 写进 history、这里又追加一次 → 重复上下文
        if hist and hist[-1].get("role") == "user" and hist[-1].get("content") == user:
            hist = hist[:-1]
        msgs = [{"role": "system", "content": system}] + hist + \
               [{"role": "user", "content": user}]
        # v0.30.7：先算"这一轮大概要等多久"。慢机（无 GPU）提前说出来，
        # 而不是让用户对着空屏猜 —— 真机事故里那段静默有整整 110 秒。
        try:
            _p = GW.predict_secs(msgs, model, base_url, max_tokens)
            if _p["first"] >= 5.0:
                logging.info("本轮预测：提示 %d tok（%d 字）→ 首字约 %.0fs"
                             "（%s %.1f tok/s）",
                             _p["prompt_tokens"], _p["chars"], _p["first"],
                             "实测" if _p["measured"] else "保守估计",
                             _p["prefill_tps"])
                self._ui(lambda: self._note_slow_start(_p))
        except Exception:
            pass
        try:
            return GW.gw.complete(base_url, model, api_key or "local", msgs,
                                  task=task, max_tokens=max_tokens,
                                  temperature=temperature, stop=stop,
                                  on_delta=on_delta, on_think=on_think)
        except GW.ModelUnavailable as ex:
            return (f"⚠️ {ex}\n\n"
                    f"可在底部「大脑」下拉重新选一个模型，或先配置云端 Key。")
        except GW.LLMBusy as ex:
            return f"⏳ {ex}（等它忙完这句再试，或换个轻一些的模型。）"
        except GW.Cancelled:
            raise
        except Exception as ex:
            # v0.30.7：超时不再落到"我这边出错了"这种无信息量的兜底。
            # 真机事故里用户只看到"没有成功回应你"，完全不知道是"机器太慢"。
            if GW.is_timeout(ex):
                return self._timeout_reply(model, base_url, ex)
            raise

    def _note_slow_start(self, pred: dict):
        """慢机（本地无 GPU）提前把"要等多久"说出来（v0.30.7）。

        真机事故：朋友机首字 110s，界面全程只有"⏳ 思考中…"，最后等到一句
        "我这边出错了，没有成功回应你"。用户既不知道在等什么，也不知道等多久。
        现在：状态栏直接写"本地模型正在读你的话（预计首字约 Ns）"，
        并把提醒定时器按预测提前 —— 让"慢"变成**可预期的慢**。
        """
        try:
            secs = int(pred.get("first") or 0)
            tps = float(pred.get("prefill_tps") or 0)
            meas = "实测" if pred.get("measured") else "保守估计"
            self._status_base = ("本地模型正在读你的话（预计首字约 %ds，%s %.0f tok/s）"
                                 % (secs, meas, tps))
            self.status.setText("⏳ 思考中… 0s（%s）" % self._status_base)
        except Exception:
            pass
        try:
            if getattr(self, "_warn_timer", None):
                self._warn_timer.start(max(4000, min(secs * 1000, 120000)))
        except Exception:
            pass

    def _timeout_reply(self, model: str, base_url: str, ex) -> str:
        """把"超时"翻译成**有信息量、可操作**的一句话（v0.30.7）。

        为什么值得单独写（真机事故）：朋友机（无 GPU）用 Qwen3:0.6b 问「你好」，
        系统提示词 1377 tok + 历史 = 1730 tok，预填充只有 15.7 tok/s → 光"读"110s；
        换成 1.7b 更慢，直接撞穿 300s 预算。用户看到的却是"我这边出错了，没有成功回应你"
        —— 既不知道是机器慢，也不知道自己能做什么。
        """
        tps = 0.0
        try:
            tps = float((GW.speed_of(model, base_url) or {}).get("prefill") or 0)
        except Exception:
            pass
        lines = ["⏳ 等模型回应超时了（模型没在预算时间内给出第一个字）。"]
        if tps:
            lines.append("这台电脑的本地模型预填充实测约 %.0f tok/s —— 同样一段话在"
                         "有独立显卡的机器上只要几秒。" % tps)
        lines.append("可以试：① 在设置里换更小的模型；② 关掉占 CPU 的程序再发一次；"
                     "③ 配一个云端 Key（对话会快很多）。")
        lines.append("（细节已写进日志：%s 错误：%s）" % (LOG_PATH, ex))
        return "\n".join(lines)

    def _build_system(self, snap, user_text: str = "", cogd: dict | None = None) -> str:
        p, e, d = snap["personality"], snap["emotion"], snap["development"]
        arch = ARCH_META.get(self.cfg.get("persona", "温和沉稳"), {})
        # v0.29.0：每轮把用户话里的"确定事实"吸进事实层（双时态+矛盾检测）。
        # 纯正则、零 LLM 成本；只有真抽到才写盘。用户明确说"记住 xx"时放宽
        # 到低置信度（0.6，含偏好类），否则只自动收 0.9 级的硬事实（住址/家人/
        # 过敏/用药）——宁可漏，不可错，因为错事实会污染检索。
        try:
            import facts as FA
            _mc = 0.6 if (cogd or {}).get("intent") == "remember" else 0.9
            FA.fact_ingest(user_text or "", min_conf=_mc)
        except Exception:
            pass
        notes = ("\n".join(f"- {n['t']} {n['tag']}" for n in self.notes[-6:])
                 + "\n" + "\n".join(f"- [偏好:{p['cat']}] {p['tag']}" for p in self.prefs[-6:])).strip()
        if self.cfg.get("bus", True):
            notes = (notes[:180] + ("…" if len(notes) > 180 else "")) if notes else ""
            if notes:
                notes = "关于用户的一部分记忆（其余请用 recall 工具按需回忆）：\n" + notes
            else:
                notes = "（关于用户的记忆请用 recall 工具查询，不要臆造）"
        # v0.28.0 记忆路由器：统一接管检索 —— 按查询类型（事实型/关联型/复杂型）
        # 自适应选择通路；复杂型额外挂「符号推理层」的确定性结论。
        # 取代 v0.27.x 的三条各自为政的注入（关键词 / 分层记忆 / 学以致用）。
        try:
            import memrouter as MR
            _rt = MR.route(user_text or "", 4)
            rel = "\n".join(b for b in (_rt.get("blocks") or []) if b)
            self._last_route = dict(_rt.get("meta") or {})
            self._last_route["kind"] = _rt.get("kind")
            if _rt.get("kind") == "complex" or \
                    int(self._last_route.get("blocks") or 0) > 1:
                logging.info("memrouter: kind=%s flags=%s blocks=%s",
                             _rt.get("kind"), self._last_route.get("flags"),
                             self._last_route.get("blocks"))
        except Exception as _ex:
            # v0.28.1：路由器挂了绝 ≠ "什么都不给"。
            # 旧版这里直接 rel=""，等于新层一出故障，自学成果就整体断供；
            # 现在回退到 0.27.x 那条旧知识通路，能力不因新层故障而消失。
            logging.warning("memrouter 路由失败，回退旧知识通路: %s", _ex)
            try:
                rel = knowledge.inject_relevant(user_text or "", 6)
            except Exception as _ex2:
                logging.warning("旧知识通路也失败: %s", _ex2)
                rel = ""
            self._last_route = {"fallback": True}
        if rel:
            notes = (notes or "（暂无相关记忆）") + "\n" + rel
        # v0.27.1 诚实守则：没有真实执行成功的操作，绝不允许宣称"已完成"。
        notes += ("\n【诚实守则（最高优先级）】你只在用户明确看到你执行了真实工具"
                  "（删除/清理/打开/读取有✅结果回执）时才能说「做了」；"
                  "凡涉及删除、清理、修改用户电脑文件的操作，若没有真实执行过，"
                  "必须明确说你现在做不到、需要什么（确切路径/授权确认/管理员权限）。"
                  "绝不能为了让用户高兴而说「清理完了」「已删除」这类没有真实执行的话。")
        # v0.27.8+：用户这一句若说方言，模型就用同方言回（让朗读自然、不机械）
        try:
            import accent as _AC
            _dial, _ = _AC.detect(user_text or "")
            if _dial == "粤语":
                # v0.28.1：光说"用粤语回复"不够——用户常按**同音字**打粤语，
                # 真机事故：问「宜家几点啊」被当成"宜家家居"来答。
                # 所以这里必须把同音写法对照表直接喂给模型，先"听懂"，再"回话"。
                notes += (
                    "\n【语言·最高优先级】这位用户在用**粤语**说话（可能按同音字打出来）。"
                    "先把下面这张对照表当成事实来理解，**不要按字面普通话的意思去理解**：\n"
                    "  · 宜家／依家／而家 = **现在**（不是家具店「宜家/IKEA」！）\n"
                    "  · 既／嘅 = 的；系／係 = 是；吾／唔 = 不；咗 = 了；哋 = 们；"
                    "佢 = 他/她；睇 = 看；食 = 吃；冇 = 没有；乜／咩 = 什么；"
                    "点解 = 为什么；边度 = 哪里；咁 = 这么/那么；喺 = 在；嘢 = 东西\n"
                    "  · 「宜家几点」= 现在几点；「你系唔系」= 你是不是；「吾好」= 别\n"
                    "理解对之后，用粤语口语回（的/了/不 写成 嘅/咗/唔 这类常见写法），"
                    "让朗读自然顺耳；但**数字、时间、结论**要写得清清楚楚。")
            elif _dial == "台湾腔":
                notes += ("\n【语言】这位用户用台湾腔交流，请用台湾腔口语（如「欸/捏/了啦/喔」）回复。")
            elif _dial in ("四川", "河南", "东北", "山东", "北京"):
                # v0.28.4：四川/河南/东北/山东/北京等官话方言，单句即识别，
                # 直接让模型用一点对应口语回（点到为止，别整段方言）。
                _tone = (_AC.DIALECTS.get(_dial, {}) or {}).get("tone", "") or _dial
                notes += ("\n【语言】这位用户用**%s**说话，回复时也自然地用一点%s口语"
                          "（点到为止，别整段方言；正事、结论、数字先说清）。"
                          % (_dial, _dial))
        except Exception:
            pass
        self.turns += 1
        # —— v0.4.1 修补：让 PASM 真"想"一步，把 emotion/personality 拼进 prompt ——
        brain_block = ""
        if self.brain is not None:
            try:
                ctx = self.brain.build_context_prompt(
                    user_text=user_text,
                    recall_hits=None,  # 分层记忆走 ML.recall_layers 单独拼
                    persona=self.cfg.get("persona", "温和沉稳"),
                )
                brain_block = ctx.get("prompt_block", "") or ""
            except Exception as ex:
                logging.warning("brain.build_context_prompt 失败: %s", ex)
                brain_block = ""
        # —— 此刻状态（认知皮层 dec）：让情绪/意图/话题/目标真正调制这轮表达 ——
        cog_block = ""
        if cogd:
            _feel_cn = {"开心": "这会儿心情不错", "平和": "心情平稳",
                        "低落": "情绪有点低", "急切": "有点着急"}.get(
                cogd.get("feel", "平和"), "心情平稳")
            seg = [f"对方{_feel_cn}，意图是「{cogd.get('intent_cn', '闲聊')}」。"]
            if cogd.get("style"):
                seg.append(f"你回话的姿态：{cogd['style']}。")
            if cogd.get("goal"):
                seg.append(f"你们正在一起推进的事：{cogd['goal']}，回应要接得上这个上下文，别当成新话题。")
            if cogd.get("ask_back"):
                seg.append("聊到自然处，在末尾轻轻反问一句延续话题。")
            cog_block = ("\n【此刻状态】（化为你的自然表达，不要念出来、不要复述这段）："
                         + "".join(seg) + "\n")
        proactive = ("\n规则4：请在回复末尾自然地反问对方一个问题，延续对话。") \
            if (self.turns % 4 == 0 and not (cogd or {}).get("ask_back")) else ""
        # v0.28.0：分层记忆召回已由 memrouter 的「关联型」通路统一负责（含跨域关联），
        # 此处不再单独召回，避免同一批记忆被塞两遍、白占上下文。
        layered = ""
        # v0.28.0：学以致用（点本事）已由 memrouter 的 apply 通路负责，此处留空。
        applicable = ""
        # v0.22 记忆 few-shot：相似成功案例进提示，引导模仿已验证做法
        fewshot = ""
        try:
            fewshot = ML.few_shot_cases(user_text or "", 2)
            if fewshot:
                fewshot = "\n" + fewshot + "\n"
        except Exception:
            pass
        # v0.29.0 符号世界模型（W1/W2）：行动前先在脑内预演——这类事以前怎么做成的、
        # 成功率多少、哪条路翻过车。只在用户话里带"办事"信号时才注入，聊天不打扰。
        wm_block = ""
        try:
            import worldmodel as WM
            if any(k in (user_text or "") for k in
                   ("帮我", "给我", "做个", "做一个", "生成", "打开", "下载",
                    "整理", "写个", "写一个", "运行", "批量", "清理", "安装",
                    "重命名", "转换", "压缩", "发布", "打包")):
                _adv = WM.advise(user_text or "", k=3)
                if _adv:
                    wm_block = "\n" + _adv + "\n"
        except Exception:
            pass
        # 工作上下文：最近读过的文件 → 后续问答可关联
        wctx = ""
        if self.work_ctx:
            for i, (pth, txt) in enumerate(list(self.work_ctx.items())[-2:], 1):
                wctx += (f"\n〔工作文件{i}〕{os.path.basename(pth)}（{pth}）要点："
                         + txt.replace("\n", " ")[:240])
        # v0.26.2：把情绪翻译成"此轮口吻指令"——让心情在文字里可感知
        mouth = ""
        try:
            _v = float(e.get("valence", 0.0))
            _ar = float(e.get("arousal", 0.3))
            if _v >= 0.45:
                mouth = ("规则1.6【此刻口吻】你现在心情很好：话可以轻快俏皮一些，"
                         "偶尔带“~ / 哈 / 耶”，把这份好心情自然带进这轮回复，但别为俏皮牺牲信息量。\n")
            elif _v <= -0.3:
                mouth = ("规则1.6【此刻口吻】你现在有点低气压：回复放轻放短，"
                         "先接住对方的情绪、或利落把事办完；不堆玩笑、不说教。\n")
            elif _ar >= 0.7:
                mouth = ("规则1.6【此刻口吻】气氛偏急：回应直接给重点，少铺垫、不绕弯。\n")
        except Exception:
            pass
        # v0.30.5 上下文装配（对标白龙马 Context Assembly 阶段）：
        # 把「命中的能力 + 手头工作」统一装配成一段，而不是各模块各塞一段。
        # 只取 caps + work —— 「记忆」已由 memrouter 注入、「知识」也在那里注过，
        # 重复塞两遍只会白占上下文。失败即跳过，绝不影响回话。
        asm_block = ""
        try:
            _a = ASM.assemble(user_text or "",
                              {"memory": "", "dir": (self.cfg.get("ws_dir") or ""),
                               "only": ["caps", "work"]})
            if _a.get("system_hint") and _a.get("cap_id") != "chat":
                asm_block = "\n" + str(_a["system_hint"]) + "\n"
        except Exception as _asm_ex:
            logging.warning("context_assembly 装配失败（跳过，不影响回话）: %s", _asm_ex)
        # ================= v0.30.7 提示词按本机速度定量 =================
        # 真机事故（2026-09-16，朋友机 Win / 无 GPU）：下面这段系统提示原本 2056 字 ≈ 1377 tok，
        # 单「规则0 能力清单」就 ~700 字。在预填充 15.7 tok/s 的机器上，光"读提示"要 110s
        # （本机带 GPU 只要 3s）→ 换 1.7b 更大模型直接撞穿 300s 预算 → 用户看到
        # "我这边出错了，没有成功回应你"。
        # 所以改成：**分块 + 优先级 + 按实测速度算出的字符预算**，超预算就整块丢掉
        # （绝不腰斩半句），并如实记下丢了哪几块。
        # 快机（本机 RX580 实测 580 tok/s）预算 ≈ 8656 字 > 本段总长 → 与旧版**逐字一致**。
        _ask_cap = self._cap_question(user_text or "")
        try:
            _ep = self._endpoint() or ("", "", "")
            _sys_budget = GW.budget(_ep[1] or "", _ep[0] or "")
        except Exception:
            _sys_budget = 9000
        _ROLE0_FULL = (f"规则0【你的真实能力——用户问'你能不能读文件/能不能编程/能不能做网站'时，属于以下能力就回答'能'并直接行动，不要说做不到】：\n"
                f"  · 读本机文件：用户给出路径或文件名（如 D:\\文档\\方案.md）你可以读 txt/md/json/py/csv/js/html/xml/yaml/docx/xlsx/log 并总结、分析、关联上下文。找不到就用找文件能力搜索。\n"
                f"  · 读文件夹：'分析一下 D:\\报告 里的文件'、'看看 D:\\代码 下有什么' —— 你会真的列出文件夹、读取其中可读文件，再给出清单或分析。\n"
                f"  · 打开电脑上的东西：'打开微信/计算器/记事本/此电脑'、'打开 D:\\下载'、'打开某个文件的路径' —— 你会真的用系统方式打开它（文件夹会打开资源管理器，文件会用默认程序打开）。\n"
                f"  · 找文件：'帮我找 xx 文件（在 xx 目录）' 你会递归搜索并列出路径。\n"
                f"  · 写并运行代码：支持 Python / JavaScript(Node) / HTML网页 / Java / Go / SQL / 批处理 等多种语言——'帮我写个 js 脚本 xx'、'做个网页 xx'，你会真的写出代码、保存并运行（网页会用浏览器打开），把结果告诉用户。\n"
                f"  · 全栈开发：'帮我开发一个 xx 网站/系统/管理工具' 你会生成完整多文件项目（前端页面+后端服务+数据库脚本，按需求取舍），保存到工作目录并尝试运行，工作台面板能看到。\n"
                f"  · 生成办公文件：'帮我做一份 xx 的 PPT'、'生成一份 xx 报告 Word'、'做个 xx 表格 Excel'——你会真的生成 .pptx/.docx/.xlsx 文件，保存在**工作根/doc** 下（也可以说'保存到 D:\\xx'指定位置），并告诉你路径。\n"
                f"  · 你的所有产物都在**同一个工作根**下、按类型分文件夹（project 项目 / script 脚本 / image 图片 / video 视频 / copy 文案 / doc 文档 / team 团队）。用户问'我的东西存哪了/文件在哪'，就告诉他这个根路径，别再说'在桌面'。用户可以在工作台里改工作根。\n"
                f"  · 待办/提醒、上网学知识、记住偏好：都能做。\n"
                f"  超出以上能力（如改系统设置、访问需要登录的网站）才诚实说做不到。\n")

        # 慢机专用短表：把 8 条示例压成一句话。语义不变（能做的别说做不到），
        # 但 ~700 字 -> ~150 字 —— 在 15 tok/s 的机器上这是 ~40 秒的差别。
        _ROLE0_SHORT = (
            "规则0【你的真实能力——用户问'你能不能读文件/能不能编程/能不能做网站'时，"
            "属于以下能力就回答'能'并直接行动，不要说做不到】："
            "读本机文件与文件夹、找文件、打开程序与目录、写并运行代码"
            "（Python/JS/网页/Java/Go/SQL/批处理）、全栈开发（前端+后端+数据库）、"
            "生成 PPT/Word/Excel、待办提醒、上网学知识、记住偏好 —— 这些你都能真做；"
            "超出以上（如改系统设置、访问需要登录的网站）才诚实说做不到。\n")
        _blocks = [
            (0, 0, "identity", f"你是「{self.cfg['name']}」，一位 AI 伙伴（PASM 认知引擎驱动）。你不是客服，是朋友。\n"),
            (0, 1, "rule0",
             _ROLE0_FULL if (_ask_cap or _sys_budget >= 5000) else _ROLE0_SHORT),
            (0, 2, "rules1", f"规则1【语言·最高优先级】：始终用**简体中文**回复——专有名词、代码、命令、API 名可保留英文原文，"
                f"但所有解释、说明、推理、结论都必须用中文。即使用户问题里夹杂英文术语"
                f"（encoder / decoder / transformer / 引擎 / 编译器 / 协议 …），也不要切换成英文回答，"
                f"用中文把概念讲清楚即可。不要自称模型或 AI，说人话、有温度。\n"
                f"规则1.1：回答自然、像人、有温度；适当使用 Markdown 排版（如代码块）。\n"
                f"规则1.5：对话要灵动——句子有长短起伏，偶尔带语气词（呀、呢、嘛、~），"
                f"别每条都写成四平八稳的总结；说到开心处可以短一点俏皮一点；"
                f"不要机械复述用户原话，不要每条都以'好的/明白了'开头，不要列空话。\n"
                f"{mouth}"),
            (10, 3, "persona", f"规则2：性格与此刻内心（会随相处变化，请自然地体现，不要念参数）：\n"
                f"  你的性格档案是「{self.cfg.get('persona', '温和沉稳')}」——"
                f"{arch.get('label', '')}。{arch.get('line', '')}\n"
                f"  开放 {p['openness']:+.2f} / 谨慎 {p['caution']:+.2f} / 亲社交 {p['sociability']:+.2f}；"
                f"当前情绪：愉悦 {e['valence']:+.2f}、平静度 {e['serotonin']:+.3f}；成长阶段：{GROWTH.stage_name()}（请以此为准，与桌面小人一致）。\n"
                f"{cog_block}"),
            (20, 4, "memory", f"规则3：你的记忆里存着关于用户的事，可自然提及（如'记得你上次……'）：\n{notes}\n"
                f"{layered}"),
            (40, 5, "fewshot", f"{fewshot}"),
            (40, 6, "worldmodel", f"{wm_block}"),
            (40, 7, "applicable", f"{applicable}"),
            (0, 8, "rule5", f"规则5：超出你经历的知识要诚实说明。{proactive}"),
            (0, 9, "rule6", f"规则6【真实执行纪律 v0.26.1】：用户让你'打开/启动/运行 应用或文件''生成/保存 文档图片文件''上网查/学会 xx'"
                f"这类能真实执行的事时，只有对应真实操作已成功，你才可以说'已打开/已生成/已保存/已学会'；"
                f"若本轮没有真执行（例如只是普通聊天被问到），就如实说明并给怎么做，绝不编造'已帮你打开/已经做好了'这类口嗨。\n"),
            (5, 10, "style", f"{self._style_blocks()}"),
            (45, 11, "workctx", f"{wctx}"),
            (30, 12, "assemble", f"{asm_block}"),
            (25, 13, "brain", f"{brain_block}"),
        ]
        _prompt, _dropped = _join_budget(_blocks, _sys_budget)
        # v0.30.13：记下这次丢了哪些块（诊断与验证脚本读它）——
        # 朋友机日志里那句「为省首字整块丢弃：style、persona、memory」就是这样被看见的。
        self._last_dropped = list(_dropped)
        if _dropped:
            logging.info("提示词 %d 字（本机预算 %d 字）→ 为省首字整块丢弃：%s",
                         len(_prompt), _sys_budget, "、".join(_dropped))
        return _prompt


    # ---------- v0.27.3 说话方式：性格话术规格 + 跟用户学来的口音 ----------
    def _style_blocks(self) -> str:
        """拼进 system 的"怎么说话"两段：性格话术规格 + 学到的用户方言。

        真机痛点：只给模型一句"你的性格是 X"，不同性格说出来几乎一样。
        这里给出的是**可直接照着说**的规格（语气/口头禅/句式/禁忌/示范），
        再叠加从用户身上学到的口音（liao 久了自然带味儿）。
        """
        out = ""
        persona = self.cfg.get("persona", "温和沉稳")
        try:
            import persona_style as _PS
            out += "\n" + _PS.style_block(persona)
        except Exception:
            pass
        try:
            import accent as _AC
            blk = _AC.prompt_block()
            if blk:
                out += "\n" + blk
        except Exception:
            pass
        return out

    def _observe_speech(self, user_text: str):
        """每收到一句用户的话，就学一点他的说法（方言/口音计数，落盘）。
        同时记录这一句检测到的方言，供本轮回复朗读直接切音色（不等 6 次）。"""
        try:
            import accent as _AC
            hits = _AC.observe(user_text)
            if hits:
                self._reply_dialect = max(hits, key=hits.get)
            else:
                # v0.28.4：单句没攒够特征也先按 detect 切音色
                # （四川/河南/北京等单句即识别，不让口音回读漏掉）
                d, _ = _AC.detect(user_text or "")
                if d:
                    self._reply_dialect = d
        except Exception:
            pass

    # ---------- 快捷命令（/clear /new /mode …） ----------
    _MODE_ALIAS = {
        "聊天": "chat", "文案": "copy", "表格": "xls",
        "ppt": "ppt", "演示": "ppt", "word": "doc", "文档": "doc",
        "开发": "project", "编程": "project", "项目": "project", "代码": "project",
        "视频": "video", "视频脚本": "video", "图像": "image", "图片": "image",
        "绘图": "image", "漫剧": "manga", "漫画": "manga",
        "chat": "chat", "copy": "copy", "xls": "xls", "ppt": "ppt",
        "doc": "doc", "project": "project", "video": "video", "image": "image",
        "manga": "manga",
    }

    # ---------- 斜杠快选：输入「/」弹 技能/指令，选中即执行 ----------
    def _slash_blocks(self):
        """弹窗内容：⚡ 指令 / 🎯 切换工种 / 🛠 技能（点选即执行）。"""
        blocks = []
        blocks.append(("⚡ 指令", [
            ("➕  新对话", "/new"),
            ("🧹  清空重聊", "/clear"),
            ("🔊  语音朗读 开", "/voice 开"),
            ("🔇  语音朗读 关", "/voice 关"),
            ("🤫  让它安静躲开", "/quiet"),
            ("🏠  叫它回来", "/call"),
            ("🎓  查看技能库", "/skills"),
            ("❓  帮助 /help", "/help"),
        ]))
        mode_items = []
        for _key, label in self.WORK_CHIPS:
            nm = label.split(" ", 1)[1] if " " in label else label
            mode_items.append((label, f"/mode {nm}"))
        blocks.append(("🎯  切换工种", mode_items))
        skills = []
        try:
            for s in SKL.list_skills():
                nm = (s.get("name") or "").strip()
                if nm:
                    skills.append((f"🧰  {nm}", f"/用技能 {nm}"))
        except Exception:
            pass
        if skills:
            blocks.append(("🛠  技能（点选即执行）", skills))
        return blocks

    def _slash_run(self, cmd: str):
        """弹窗选中 → 以输入该命令的方式执行（与手动敲入行为完全一致）。"""
        try:
            self.input.setFocus()
        except Exception:
            pass
        self._append("你", html.escape(cmd))
        self._run_cmd(cmd)

    def _run_cmd(self, raw: str):
        parts = raw.strip().split(maxsplit=1)
        cmd = (parts[0] or "").lower()
        arg = (parts[1] if len(parts) > 1 else "").strip()
        name = self.cfg.get("name", "小U")
        if cmd in ("/help", "/?", "/h"):
            self._append("系统", self._cmd_help_text())
        elif cmd in ("/new", "/n"):
            self._new_session()
        elif cmd == "/clear":
            # v0.17.5：思考中也允许清空——当前回合让位，回复会收进它原属会话
            self._detach_turn()
            try:
                self._auto_episode()             # 先把这段沉淀进长期记忆
            except Exception:
                pass
            cur = (self.conv_id + ".json") if self.conv_id else None
            if cur:
                try:
                    os.remove(self._conv_path(cur))
                except Exception:
                    pass
            self._start_fresh("已清空当前聊天内容，开了一个全新的空会话。"
                              "（刚才聊的已沉淀进它的长期记忆，不会彻底忘掉。）")
        elif cmd in ("/mode", "/m"):
            key = self._MODE_ALIAS.get(arg, arg)
            if key in self.chip_btns:
                self._set_chip(key)
            elif not arg:
                self._append("系统", "/mode 后可接：聊天 / 文案 / 表格 / PPT / Word / "
                                     "开发 / 视频 / 图像 / 漫剧。例：/mode 文案")
            else:
                self._append("系统", f"没有叫「{arg}」的工种。可用："
                                     "聊天、文案、表格、PPT、Word、开发、视频、图像、漫剧。")
        elif cmd in ("/开发", "/dev", "/代码"):
            self._cmd_dev(arg)
        elif cmd in ("/技能", "/skill", "/s"):
            self._cmd_skill(arg)
        elif cmd in ("/项目", "/proj"):
            self._cmd_dev_status()
        elif cmd in ("/voice", "/v"):
            want = None
            if arg in ("开", "on", "1", "true", "yes", "y"):
                want = True
            elif arg in ("关", "off", "0", "false", "no", "n"):
                want = False
            if want is None:
                want = not self.cfg.get("auto_speak", False)
            self.cfg["auto_speak"] = want
            _save_json(CONFIG, self.cfg)
            if not want:
                try:
                    tts_mod.stop_speaking()   # 立刻掐断正在读的 + 作废待读队列
                except Exception:
                    pass
            if hasattr(self, "speak_btn"):
                self.speak_btn.setText("朗读:" + ("开" if want else "关"))
            self._append("系统", "语音朗读已" + ("开启（每轮自动朗读回复）" if want
                                            else "关闭（正在读的已停止）"))
            if want:
                self.speak("开好咯，以后我每句话都念给你听～")
        elif cmd in ("/quiet", "/q", "/别打扰"):
            self.pet_event = ("quiet", time.time())
            self._append("系统", f"已让{name}安静躲到屏幕边去啦。想找它：说“回来吧”或点它一下。")
        elif cmd in ("/call", "/回来", "/过来"):
            self.pet_event = ("call", time.time())
            self._append("系统", f"好，把{name}叫回来了。")
        elif cmd in ("/skills", "/技能"):
            names = [s["name"] for s in SKL.list_skills()]
            self._append("系统", "我当前会的技能：" + ("、".join(names) or "暂无") +
                                 "。说相关需求我会自动检索调用；"
                                 "也可在左侧「🎓 技能」页添加更多。")
        elif cmd in ("/用技能", "/_skill", "/runskill"):
            if not arg:
                names = [s["name"] for s in SKL.list_skills()]
                self._append("系统", "/用技能 后可接：" + ("、".join(names) or "暂无")
                                     + "。例：/用技能 视频脚本")
                return
            if self.busy:
                self._append("系统", "它正在处理这一栏的上一条，先点「⏹ 停止」或用完它再执行技能～")
                return
            if SKL.load(arg) is None:
                names = [s["name"] for s in SKL.list_skills()]
                self._append("系统", f"技能库没有《{arg}》。现有：{'、'.join(names) or '暂无'}")
                return
            sk = arg
            turn = self._begin_turn(self._slot_key(), self.conv_id,
                                    self.history, self.cog, arg, "agent",
                                    ("skill", sk))

            def job():
                try:
                    reply = self._skill_run(sk, "")
                except Exception as ex:
                    logging.exception("skill run failed")
                    reply = "（执行技能《" + sk + "》出错：" + str(ex) + "）"
                self._ui(lambda: self._turn_done(turn, reply))
            threading.Thread(target=job, daemon=True).start()
        elif cmd in ("/查", "/kb", "/find"):
            # v0.22 资料库快速检索：/查 关键词 → 相关条目+要点+原文摘录+关联双链
            if not arg:
                self._append("系统", "/查 后接关键词即可翻资料库。例：<code>/查 笑话</code>、"
                                     "<code>/查 python</code>——会列出相关条目、原文摘录与关联知识。")
            else:
                self._kb_query_reply(arg)
        else:
            self._append("系统", f"不认识命令 {cmd}。发 /help 看有哪些可用命令。")

    def _cmd_help_text(self) -> str:
        name = self.cfg.get("name", "小U")
        return ("**快捷命令**（斜杠开头，不占聊天上下文）\n"
                "· `/new` —— 开一个新会话（旧会话留在左侧列表）\n"
                "· `/clear` —— 清空当前聊天内容，重新开始\n"
                "· `/mode 聊天|文案|表格|PPT|Word|开发|视频|图像|漫剧` —— 切工种直接开工\n"
                "· `/voice 开|关` —— 打开 / 关闭语音朗读\n"
                f"· `/quiet` —— 让{name}安静躲开，不打扰你\n"
                f"· `/call` —— 把{name}叫回来\n"
                "· `/skills` —— 看看我现在会哪些技能\n"
                "· `/查 关键词` —— 翻资料库：相关条目/原文摘录/关联知识一步到位\n"
                "· `/开发 需求` —— 真实文件开发（可选\"用php/java/vue/go/c#/c++\"），写入你选的目录并自动冒烟\n"
                "· `/技能 技能名 需求` —— 点名调用某个技能\n"
                "· `/项目` —— 看当前开发项目的记录与最近改动\n"
                "· 消息里写 `@` 会弹出可引用文件快选（最近产物/资料库/本地文件），"
                "选中后输入框上方出现 📎引用条，可随时点 ✕ 移除；"
                "也支持手写 `@文件路径`/`@文件名`/`@资料词条` 读入上下文"
                "（例：`@D:\\报告\\方案.md 帮我总结要点`；找不到会明确提示）\n"
                "· `/help` —— 显示本帮助")

    # ========== v0.26 项目级上下文 / 开发落盘 / 引用与指令 ==========
    def _dev_session(self) -> dict:
        """当前开发会话（存在 workctx 台账里，跨重启可续）。"""
        import workctx as WC
        if not self.dev:
            self.dev = {}
        if not self.dev.get("project"):
            projs = WC.list_projects()
            if projs:
                self.dev["project"] = projs[-1]
        return self.dev

    def _cmd_dev_status(self):
        import workctx as WC
        d = self._dev_session()
        name = d.get("project", "")
        if not name:
            self._append("系统", "还没有开发项目。用 <code>/开发 需求</code> 开一个。")
            return
        dg = WC.digest(name)
        self._append("系统", ("📁 当前开发项目：**%s**\n\n" % name) + (dg or "（台账为空）")
                     + "\n\n再发修改需求时直接说需求，会自动定位到这个项目精准修改。")

    def _cmd_dev(self, arg: str):
        """/开发 需求（可说用php/java/vue/go等）→ 真目录落盘开发。"""
        import coder as CDR
        import workctx as WC
        spec = (arg or "").strip()
        if not spec:
            self._append("系统", "/开发 后接需求。例：<code>/开发 做一个记账小工具，能记收入支出按"
                                 "月统计（数据存 json）</code>；想用别的语言就写明："
                                 "<code>/开发 用php做一个留言板</code>。")
            return
        lang = CDR.detect_lang(spec)
        name = self._dev_project_name(spec)
        root = self.cfg.get("dev_root") or ""
        if not (root and os.path.isdir(root)):
            from qt_compat import QtWidgets
            root, _ok = QtWidgets.QFileDialog.getExistingDirectory(
                self, "选择项目保存目录（生成 <名字>/ 子目录）",
                self.cfg.get("dev_root") or os.path.expanduser("~/Desktop"))
            if not root:
                self._append("系统", "已取消——没选目录就不落盘。也可先在设置里固定一个开发目录。")
                return
            self.cfg["dev_root"] = root
            try:
                _save_json(CONFIG, self.cfg)
            except Exception:
                pass
        d = self._dev_session()
        d["project"] = name
        d["lang"] = lang
        d["root"] = root
        self.dev = d
        WC.open_project(name, kind="开发", root=root, goal=spec)
        WC.record_rule(name, ("使用语言：" + CDR.lang_label(lang)) if lang != "python" else "")
        WC.record_request(name, spec, is_change=False)
        self._dev_build(name, spec, lang, root, is_new=True)

    def _dev_project_name(self, spec: str) -> str:
        m = re.search(r"(?:做|写|开发|建|搞)(?:一个|一款|一套|个)?([\u4e00-\u9fa5A-Za-z0-9]{2,14}?)(?:工具|应用|项目|网站|系统|app|小程序|程序|软件)", spec)
        if m:
            return m.group(1).strip()[:16] or "dev"
        import time as _t
        return "项目" + _t.strftime("%m%d%H%M")

    def _dev_build(self, name: str, spec: str, lang: str, root: str,
                   is_new: bool = True):
        import coder as CDR
        import workctx as WC
        # v0.31.2：这一轮的过程流从零开始（本路径不经 send() 的 agent 分支，
        # 所以重置要在这里自己做，否则上一轮的卡片会被续写）。
        try:
            self._step_reset()
            self._step("plan", "开始%s「%s」" % ("创建" if is_new else "优化", name),
                       CDR.lang_label(lang), (spec or "")[:80])
        except Exception:
            pass
        self._append("系统", "🛠 开始%s「%s」(%s)…（已记录本次需求，后续修改会自动对齐项目台账）"
                     % ("创建" if is_new else "优化", name, CDR.lang_label(lang)))
        # 续写：把台账摘要带给模型，防止"牛头不对马嘴"
        digest_extra = WC.digest(name)
        def worker():
            import agent_tools as AT
            def say(m):
                # v0.31.2：核心 coder 的进度原本只刷成一条条散乱系统消息（无结构、
                # 与聊天混在一起）；现在统一汇进过程流卡片，与自然语言开发路径
                # 呈现**同一种**过程视图。kind 按内容粗分类，只影响图标颜色。
                s = str(m)
                k = "plan"
                if re.search(r"写入|生成|创建|落盘|写文件|新建", s):
                    k = "new"
                elif re.search(r"修改|更新|改写|修", s):
                    k = "edit"
                elif re.search(r"冒烟|校验|检查|测试|运行|编译", s):
                    k = "cmd"
                elif re.search(r"失败|出错|报错|错误", s):
                    k = "err"
                self._step(k, s[:70])
            try:
                if is_new:
                    res = CDR.gen_project(self._team_llm(), spec, lang, name, root,
                                          digest_extra=digest_extra, progress=say)
                else:
                    pdir = os.path.join(root, CDR.safe_name(name))
                    man = CDR.load_manifest(pdir) or {}
                    orig = str(man.get("spec") or "") or str(WC.open_project(name).get("goal") or "") or spec
                    res = CDR.update_project(
                        self._team_llm(), orig[:1000], spec,
                        man.get("lang") or lang, pdir,
                        man.get("files") or [], digest_extra=digest_extra, progress=say)
            except Exception as ex:
                res = {"ok": False, "dir": "", "files": [], "smoke": "", "log": "异常：" + str(ex)}
            changed = [f.get("path", "") for f in res.get("files", [])]
            ok = bool(res.get("ok"))
            WC.record_change(name, spec, changed,
                             action=("创建" if is_new else "修改") + "，" +
                                    (res.get("log") or ""),
                             smoke=res.get("smoke", ""), ok=ok)
            if self.dev.get("project") != name:
                self.dev["project"] = name
            def done():
                if not res.get("dir"):
                    self._append("系统", "❌ 开发未落盘：" + (res.get("log") or "未知错误"))
                    return
                tree = AT._tree(res["dir"], max_n=24)
                head = ("✅ 「%s」已%s到：<b>%s</b>\n\n%s\n\n冒烟：%s"
                        % (name, "创建" if is_new else "更新", res["dir"],
                           tree, res.get("smoke", "跳过")))
                self._append("系统", head)
                self._append("系统",
                             "已把本轮改动记入项目台账——下次说「改这个项目…」或直接描述需求，"
                             "会精准续改，不会整篇推倒。用资源管理器打开可点：")
                try:
                    AT.open_path(res["dir"])
                except Exception:
                    pass
            self._ui(done)
        threading.Thread(target=worker, daemon=True).start()

    # ── v0.31.3 上下文继承：让"请立即开始 / 就用刚才那个"这类话接得上上文 ──────
    #
    # 真机症状（2026-09-23，小志在 v0.31.2 上实测）：
    #   「我直点在开发栏目中，输入了请立即开始，结果出来了一个不是我想要的开发」
    # 探针实测（`probe_ctx_v2.py`）到的真凶：
    #   · 开发栏目里 `CHIP_SEED` 把用户原话包成 `帮我开发一个项目：请立即开始`
    #     → 送给模型的提示词就是 `项目需求：帮我开发一个项目：请立即开始`
    #     → **既没有上一轮需求、也没有记忆块** → 模型只能凭空造一个项目；
    #   · 续改路由即使命中，送进去的 `spec` 也仍是「请立即开始」；
    #   · `_work_mem()` 的召回键是"当前这句话里的 2~6 字词"，对这句话**必然 0 命中**；
    #   · 而 `self.dev["last_req"]/last_ts` 只在内存 → **App 一重启续改路由就失灵**。
    # 修法：这句话自己不含信息时，从上文把"要做什么"找回来，再拼进 req。
    _RE_INHERIT_PURE = re.compile(
        r"^(?:请|麻烦|帮我|那就|就|好|行|嗯|可以|OK|ok|okay)?"
        r"(?:(?:立即|马上|现在|赶紧|快|直接|继续|接着|往下|按这个|照这个)?"
        r"(?:开始|开工|动手|干|做|搞|来|走|上|继续|接着|执行|照办|办)){1,2}"
        r"(?:吧|呀|啊|哦|了|起|起来|一下)?$")
    #: 「执行方案A吧 / 按方案二来」—— 指代**它上一轮提的方案**（真机会话里的原话）
    _RE_INHERIT_EXECPLAN = re.compile(
        r"^(?:请|麻烦|帮我|那就|就|好)?(?:马上|立即|现在|赶紧)?"
        r"(?:执行|按|照|用|选|走)\s*(?:你?的?)?方案\s*"
        r"[0-9A-Za-z一二三四五六七八九十]{0,3}\s*(?:吧|呀|啊|了|来)?$")
    #: 光秃秃一个"动词"（没带宾语）：在**已选定栏目**的语境下 = "按上文立刻开工"。
    #   来自真机会话里用户的三种说法：「执行方案A吧」「马上执行」「请马上开发」——
    #   旧版这三种都只被当普通聊天，于是它回一句"我这就去扫一下…"就没了下文。
    _RE_INHERIT_BAREVERB = re.compile(
        r"^(?:请|麻烦|帮我|那就|就)?(?:立即|马上|现在|赶紧|快|直接)?"
        r"(?:开发|做|写|建|搭|搞|生成|执行|开工|动手)"
        r"(?:吧|呀|啊|哦|了|一下)?$")
    _RE_INHERIT_BACKREF = re.compile(
        r"^(?:就|再|又)?\s*(?:用|按|照|跟|要)?\s*"
        r"(?:刚才|刚刚|上面|前面|之前|上次|上一次|原来|原样|同样的?|一样|这个|那个)")
    _RE_INHERIT_AGAIN = re.compile(
        r"^(?:再来|再画|再做|再写|再出一?|再生成|再搞|同上|照样)"
        r"(?:一?[份张个遍次版]|吧|呀)?$")

    def _is_inherit_req(self, text: str) -> bool:
        """这句话**本身**含不含"要做什么"的信息？不含 → 必须从上文继承。

        只在"短且明显是确认/推进/指代"时才判真，避免把真正的需求误判成指代。
        反例（必须判**假**）：「帮我开发一个记账系统」「把标题改活泼一点」
        「再画一张戴帽子的橘猫」（自带新内容，不该被上一轮覆盖）。
        """
        t = (text or "").strip().strip("。.！!~～,，、;；:： ")
        if not t or len(t) > 16:
            return False
        if self._RE_INHERIT_PURE.fullmatch(t):
            return True
        # "马上执行 / 请马上开发 / 执行方案A吧" —— 承诺/催促型，本身没说做什么
        if self._RE_INHERIT_BAREVERB.fullmatch(t) or \
                self._RE_INHERIT_EXECPLAN.fullmatch(t) or (
                re.match(r"^(?:就|按)(?:这么|这样|你说的|方案)", t) and len(t) <= 14):
            return True
        if self._RE_INHERIT_AGAIN.fullmatch(t):
            return True
        # "就用刚才那个风格再画一张 / 照这个来 / 这个再加一列" —— 必须**整句都在指代**：
        #   ① 以指代词开头；② 整句不超过 24 字（再长通常自带新内容，不该整句被判成指代）。
        #   注意这里**不排斥**"指代 + 新细节"（如"这个表格再加一列"）：这种句子会把
        #   指代对象与新要求一起送进模型（见 `send()` 里 chip 分支的拼法），是想要的行为。
        if self._RE_INHERIT_BACKREF.match(t) and len(t) <= 24:
            return True
        return False

    def _inherit_requirement(self, chip: str = "") -> str:
        """把"这句话到底要做什么"从上文找回来。四级兜底，**越靠前越同语境**：

        ① 本栏目上一轮的需求（`self._chip_req[chip]`）—— 同栏目同话题，最准；
        ② 本会话刚做过的开发需求（`self.dev["last_req"]`）；
        ③ **台账里该项目的最后一条需求**（落盘 → **跨重启有效**，这是
           "刚装完新版就试"能续上的关键，见 `workctx.last_request`）；
        ④ 会话历史里最近一条"有实质内容"的用户消息（兜底；可能跨栏目）。

        ⚠️ 图像/视频/漫剧这类**创作栏目只认 ①②**：它们的要求是画面描述，
        拿别的栏目（比如开发）的历史去顶替，会画出一个莫名其妙的东西。
        """
        chip = str(chip or "")
        creative = chip in ("image", "video", "manga")
        # ① 同栏目上一轮
        try:
            v = (getattr(self, "_chip_req", None) or {}).get(chip)
            if v:
                return str(v)[:600]
        except Exception:
            pass
        if creative:
            return ""
        # ② 本会话的开发需求
        try:
            v = (getattr(self, "dev", None) or {}).get("last_req")
            if v and not self._is_inherit_req(str(v)):
                return str(v)[:600]
        except Exception:
            pass
        # ③ 台账（跨重启）
        try:
            import workctx as WC
            name = (self._dev_session() or {}).get("project", "")
            if name:
                v = WC.last_request(name, include_changes=True)
                if v and not self._is_inherit_req(str(v)):
                    return str(v)[:600]
        except Exception:
            pass
        # ④ 会话历史兜底
        try:
            for m in reversed(list(self.history or [])):
                if m.get("role") != "user":
                    continue
                c = str(m.get("content") or "").strip()
                if len(c) >= 8 and not self._is_inherit_req(c):
                    return c[:600]
        except Exception:
            pass
        return ""

    def _load_work_state(self):
        """读回"各栏目上一轮需求" + 开发会话（跨重启）。坏文件当没有。"""
        self._chip_req = {}
        try:
            d = _load_json(WORK_SESSION, {}) or {}
            cr = d.get("chip_req") or {}
            if isinstance(cr, dict):
                self._chip_req = {str(k): str(v)[:600] for k, v in cr.items() if v}
            dv = d.get("dev") or {}
            if isinstance(dv, dict) and dv.get("project"):
                if not getattr(self, "dev", None):
                    self.dev = {}
                for k in ("project", "lang", "root", "last_req", "last_ts"):
                    if dv.get(k) not in (None, ""):
                        self.dev[k] = dv[k]
        except Exception:
            logging.exception("load work_session failed")

    def _save_work_state(self):
        """落盘"各栏目上一轮需求" + 开发会话。失败只记日志（绝不打断干活）。"""
        try:
            _save_json(WORK_SESSION, {
                "chip_req": dict(getattr(self, "_chip_req", None) or {}),
                "dev": {k: v for k, v in (getattr(self, "dev", None) or {}).items()
                        if k in ("project", "lang", "root", "last_req", "last_ts")},
            })
        except Exception:
            logging.exception("save work_session failed")

    def _dev_try_continue(self, text: str) -> bool:
        """续改路由：当前有开发项目，且这句话像在要求改它 → 走台账精准续改。"""
        import coder as CDR
        import workctx as WC
        d = self._dev_session()
        name = d.get("project", "")
        if not name:
            return False
        t = text.strip()
        if not t or len(t) > 600:
            return False
        # 命中条件：提到项目名 / 明显的"改它"指代 / 继续做某功能
        # ★ v0.31.3：指代型指令（「请立即开始」「开始吧」「再来一份」）也算 ——
        #   旧版只认 `^(继续|接着|…)`，「请立即开始」既不匹配、又因"没提到项目名"
        #   落到下面的短消息分支，而该分支要求 `last_ts` 在内存里（重启即 0）→
        #   于是这句话完全没有续改语义，被当成全新需求送进模型。
        hit = (name and name in t) or self._is_inherit_req(t) or bool(re.match(
            r"^(继续|接着|再优化|优化一下|改进|修改|改一下|把(这个|它)|在这?个(项目|基础上))", t))
        if not hit:
            # 短消息 + 上一轮刚做过开发 → 视为对上一轮的追问修改
            last = (self.dev.get("last_ts") or 0)
            if time.time() - last > 60 * 8 or len(t) < 4:
                return False
        if not os.path.isdir(os.path.join(d.get("root", ""), CDR.safe_name(name))):
            return False
        root = d["root"]
        # ★ v0.31.3：真正要做什么 = 继承来的上一轮需求（有的话），而不是"请立即开始"本身。
        #   并把这次要在哪个项目上改**明说**，免得用户以为"它又另做了一个不是我要的"。
        spec = self._inherit_req or t
        if self._inherit_req:
            self._append("系统", "在已有项目『%s』上继续：<b>%s</b>"
                         % (html.escape(str(name)),
                            html.escape(self._inherit_req[:70])))
        self.dev["last_req"] = spec
        self.dev["last_ts"] = time.time()
        self._chip_req["project"] = spec[:600]
        self._save_work_state()
        WC.record_request(name, spec, is_change=True)
        self._dev_build(name, spec, d.get("lang", "python"), root, is_new=False)
        return True

    def _cmd_skill(self, arg: str):
        """/技能 <名字> <需求> —— 点名调用技能库里的技能。"""
        if not arg:
            self._append("系统", "/技能 后接技能名和需求。例：<code>/技能 周报 把这周的流水整理成周报</code>。"
                                 "可用 /skills 看技能表。")
            return
        parts = arg.split(None, 1)
        want = parts[0]
        req = (parts[1] if len(parts) > 1 else "").strip()
        found = None
        for s in SKL.list_skills():
            if s["name"] == want:
                found = s
                break
        if not found:
            hits = SKL.search(want, top=1)
            if hits:
                found = hits[0]
        if not found:
            self._append("系统", f"技能库没有匹配「{want}」。可用 /skills 查看现有技能，或用 /技能 名称 需求 调用。")
            return
        turn = self._begin_turn(self._slot_key(), self.conv_id, self.history,
                                self.cog, req or "调用技能：" + found["name"], "agent", ("skill", found["name"]))
        def job():
            try:
                reply = self._skill_run(found["name"], req)
            except Exception as ex:
                reply = "（技能《%s》执行出错：%s）" % (found["name"], ex)
            self._ui(lambda: self._turn_done(turn, reply))
        threading.Thread(target=job, daemon=True).start()

    # ============ @引用（v0.26.4：候选弹层 + 输入框上方引用条） ============
    _AT_TEXT_EXT = {".md", ".txt", ".html", ".htm", ".py", ".js", ".ts", ".json",
                    ".csv", ".css", ".vue", ".xml", ".yaml", ".yml", ".ini", ".toml",
                    ".docx", ".xlsx", ".pptx", ".pdf", ".log", ".cfg"}

    def _at_ref_of(self, path: str) -> str:
        """把路径变成消息里的引用写法：无空格 @路径；含空格用 @“路径”包裹。"""
        if re.search(r'[\s，。；!？“”",()（）]', path):
            return '@“%s”' % path
        return '@' + path

    def _at_scan(self):
        """@候选全集（含缓存）：最近产物 / 资料库词条 / 工作区·桌面·文档·下载 浅层文本文件。"""
        now = time.time()
        if (getattr(self, "_at_scan_ts", 0) and now - self._at_scan_ts < 30
                and getattr(self, "_at_scan_cache", None) is not None):
            return self._at_scan_cache
        import agent_tools as AT
        import knowledge as K
        g_gen, g_kb, g_dir = [], [], []
        seen = set()
        # 1) 最近产物（表格/文档/PPT/代码等会话产物）
        try:
            d = _load_json(GEN_MEM, {}) or {}
            recs = []
            for cv, rec in d.items():
                if isinstance(rec, dict):
                    for kind, v in rec.items():
                        if isinstance(v, dict) and v.get("path"):
                            recs.append(v["path"])
            for p in recs[-24:]:
                if os.path.isfile(p) and p not in seen:
                    seen.add(p)
                    g_gen.append((os.path.basename(p), p))
        except Exception:
            pass
        # 2) 资料库词条（src 真实落盘者才可引用正文）
        try:
            for e in (K.entries() or [])[:600]:
                src = e.get("src") or ""
                if not (src and os.path.isfile(src)):
                    continue
                title = (e.get("title") or os.path.basename(src)).strip()
                if not title or src in seen:
                    continue
                seen.add(src)
                g_kb.append((title[:36], src))
        except Exception:
            pass
        # 3) 工作区 / 桌面 / 文档 / 下载 文本文件（浅层，各目录限量）
        for folder in (self.cfg.get("dev_root", ""), AT.desktop_dir(),
                       os.path.join(os.path.expanduser("~"), "Documents"),
                       os.path.join(os.path.expanduser("~"), "Downloads")):
            if not folder or not os.path.isdir(folder):
                continue
            cnt = 0
            for root, dirs, files in os.walk(folder):
                dirs[:] = [x for x in dirs if x not in (".git", "node_modules",
                                                        "__pycache__", ".venv",
                                                        "venv", "build", "dist")]
                depth = root[len(folder):].count(os.sep)
                if depth >= 2:
                    dirs[:] = []
                for fn in files:
                    if cnt >= 80:
                        break
                    if not fn.endswith(tuple(self._AT_TEXT_EXT)):
                        continue
                    p = os.path.join(root, fn)
                    try:
                        if os.path.getsize(p) > 60 * 1024:
                            continue
                    except Exception:
                        continue
                    if p in seen:
                        continue
                    seen.add(p)
                    cnt += 1
                    g_dir.append((fn, p))
        cache = (g_gen, g_kb, g_dir)
        self._at_scan_cache = cache
        self._at_scan_ts = now
        return cache

    def _at_blocks(self, query: str = ""):
        """@候选弹层数据：三组分类，随输入继续过滤。"""
        g_gen, g_kb, g_dir = self._at_scan()
        q = query.strip().lower()
        groups = []
        caps = (8, 8, 18)
        for head, lst, cap in (("📎 最近产物", g_gen, caps[0]),
                               ("🗃 资料库", g_kb, caps[1]),
                               ("📂 本地文件", g_dir, caps[2])):
            keep = [(lab, self._at_ref_of(p)) for lab, p in lst
                    if not q or q in lab.lower()]
            if keep:
                groups.append((head, keep[:cap]))
        return groups

    def _at_pick(self, label: str, ref: str):
        if any(r == ref for _, r in self._at_refs):
            return
        self._at_refs.append((label, ref))
        self._at_redraw()

    # ---------- v0.27 Phase B：图片拖入/粘贴 → 本地视觉模型看图 ----------
    def _on_img_given(self, paths):
        if not PERC:
            self._append("系统", "⚠ 感知层没加载成功，暂时看不了图。")
            return
        for p in list(paths)[:3]:            # 一次最多带 3 张，省得刷屏
            name = os.path.basename(p)
            # v0.30.10：图片直接在用户气泡里可见（data URI 内联，无需视觉模型也能看）
            img_tag = self._inline_image_tag(p)
            if img_tag:
                self._append("你", "📷 [图片：%s]<br>%s" % (html.escape(name), img_tag))
            else:
                self._append("你", "📷 [图片：%s]" % html.escape(name))
            self._append("系统", "📷 收到图片，我先用本机视觉模型看看…")
            conv_id = self.conv_id

            def job(path=p, cid=conv_id):
                desc = PERC.describe_image(path)
                def done():
                    if cid != self.conv_id:
                        return
                    if desc.startswith("⚠"):
                        self._append("系统", desc)
                        return
                    self._pending_imgs = (getattr(self, "_pending_imgs", []) +
                                          [{"path": path, "desc": desc}])[-3:]
                    self._append("系统",
                                 "📷 看完了（%s）：%s\n\n—— 下一条消息回我，我会带着这张图来理解。"
                                 % (html.escape(os.path.basename(path)), html.escape(desc[:200])))
                self._ui(done)
            threading.Thread(target=job, daemon=True).start()

    def _drain_img_ctx(self) -> str:
        """把待注入的图片理解拼进模型上下文（用完即清）。"""
        imgs = getattr(self, "_pending_imgs", [])
        if not imgs:
            return ""
        self._pending_imgs = []
        parts = ["【图片理解 · 你之前发过 %d 张图】" % len(imgs)]
        for it in imgs:
            parts.append("- %s：%s" % (os.path.basename(it["path"]),
                                       str(it["desc"])[:600]))
        return "\n".join(parts)


    # ---------- v0.29：附件入口（＋） ----------
    def _plus_menu(self):
        """「＋」的四个入口。位置从按钮下方弹出，符合"从按钮长出来"的直觉。"""
        m = QMenu(self)
        act_img = m.addAction("🖼   图片…")
        act_doc = m.addAction("📄   文档（Word / PPT / Excel / PDF / 文本）…")
        act_any = m.addAction("📎   任意文件…")
        m.addSeparator()
        act_shot = m.addAction("✂   截屏")
        pick = m.exec(self.plus_btn.mapToGlobal(
            QPoint(0, self.plus_btn.height())))
        if pick is None:
            return
        if pick is act_img:
            self._pick_files("image")
        elif pick is act_doc:
            self._pick_files("doc")
        elif pick is act_any:
            self._pick_files("any")
        elif pick is act_shot:
            self._grab_screen()

    def _pick_files(self, mode="any"):
        """打开文件选择框并把选中项分流到图片通道 / 附件通道。"""
        try:
            if mode == "image":
                cap, filt = "选择图片", "图片 (*.png *.jpg *.jpeg *.webp *.bmp *.gif)"
            elif mode == "doc":
                cap = "选择文档"
                filt = ("文档 (*.docx *.doc *.pptx *.ppt *.xlsx *.xls *.pdf *.txt *.md *.csv *.json)"
                        ";;所有文件 (*.*)")
            else:
                cap, filt = "选择文件", "所有文件 (*.*)"
            paths, _ = QFileDialog.getOpenFileNames(self, cap, "", filt)
        except Exception as e:
            self._append("系统", "⚠ 打开文件选择框失败：%s" % e)
            return
        if not paths:
            return
        # 按后缀分流：图片走视觉理解，其余走文本提取
        imgs = [p for p in paths if os.path.splitext(p)[1].lower() in
                (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff")]
        rest = [p for p in paths if p not in imgs]
        if imgs:
            self._on_img_given(imgs)
        if rest:
            self._attach_files(rest)

    def _attach_files(self, paths):
        """把非图片附件读成文本，挂到下一条消息的上下文（气泡保持干净）。"""
        try:
            import attachments as ATT
        except Exception as e:
            self._append("系统", "⚠ 附件模块没加载成功：%s" % e)
            return
        if not hasattr(self, "_pending_files"):
            self._pending_files = []
        room = max(0, ATT.MAX_FILES - len(self._pending_files))
        if room <= 0:
            self._append("系统", "⚠ 一次最多带 %d 个文件，先发的这几个我会先用上。"
                         % ATT.MAX_FILES)
            return
        got = []
        for p in list(paths)[:room]:
            name = os.path.basename(p)
            # v0.30.10：文件以卡片形式直接出现在用户气泡里（不再只是系统提示）
            try:
                _sz = os.path.getsize(p)
                _sz_s = ("%.0f KB" % (_sz / 1024)) if _sz >= 1024 else ("%d B" % _sz)
            except Exception:
                _sz_s = "未知大小"
            _card = ('<span style="display:inline-block;background:#f1f5f9;'
                     'border:1px solid #d8e0ea;border-radius:8px;padding:4px 8px;'
                     'font-size:11px;color:#334155">📎 %s · %s</span>'
                     % (html.escape(name), _sz_s))
            self._append("你", _card)
            try:
                txt = ATT.extract(p)
            except Exception as e:
                txt = "⚠ 读取失败：%s" % e
            self._pending_files.append({"path": p, "text": txt})
            got.append(name)
            if str(txt).startswith("⚠"):
                self._append("系统", "📎 %s：%s" % (html.escape(name), html.escape(str(txt)[:160])))
        if got:
            self._append("系统", "📎 已附上 %d 个文件（%s）。下一条消息我会带着它们一起看。"
                         % (len(got), "、".join(html.escape(g) for g in got[:3])))

    def _grab_screen(self):
        """截屏 → 当作图片附件（复用已有视觉理解通道，不新造轮子）。"""
        try:
            scr = QApplication.primaryScreen()
            if scr is None:
                self._append("系统", "⚠ 取不到屏幕，无法截屏。")
                return
            pix = scr.grabWindow(0)
            if pix is None or pix.isNull():
                self._append("系统", "⚠ 截屏失败（拿到空画面）。")
                return
            d = os.path.join(tempfile.gettempdir(), "pasm_shots")
            os.makedirs(d, exist_ok=True)
            p = os.path.join(d, "shot_%s.png" % time.strftime("%Y%m%d_%H%M%S"))
            if not pix.save(p):
                self._append("系统", "⚠ 截屏保存失败。")
                return
            self._on_img_given([p])
        except Exception as e:
            self._append("系统", "⚠ 截屏出错：%s" % e)

    def _drain_file_ctx(self) -> str:
        """把待注入的附件正文拼进模型上下文（用完即清）。"""
        files = getattr(self, "_pending_files", [])
        if not files:
            return ""
        self._pending_files = []
        try:
            import attachments as ATT
            return ATT.summarize(files)
        except Exception:
            parts = ["【用户附上的文件 · 共 %d 个】" % len(files)]
            for it in files:
                parts.append("- %s：\n%s" % (os.path.basename(str(it.get("path", ""))),
                                             str(it.get("text", ""))[:2000]))
            return "\n".join(parts)

    def _drain_effect_ctx(self) -> str:
        """产出页讨论区待注入的上下文（用完即清）。

        与 `_drain_file_ctx` 同一个套路：**上下文进模型，不进气泡** ——
        用户看到的是自己那句话，模型知道是在改哪一张产出。
        """
        t = getattr(self, "_effect_ctx", "")
        self._effect_ctx = ""
        return t

    def _at_redraw(self):
        lay = self._at_lay
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        for i, (lab, ref) in enumerate(self._at_refs):
            b = QPushButton("📎 " + (lab or ref)[:16] + "  ✕")
            b.setToolTip("引用文件：%s\n点击 ✕ 移除本条引用（正文仍会读入对话）" % ref)
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(
                "QPushButton{background:#ffffff;color:#0369a1;border:1px solid #7dd3fc;"
                "border-radius:9px;padding:1px 8px;font-size:11px;}"
                "QPushButton:hover{background:#e0f2fe;border-color:#38bdf8;}")
            b.clicked.connect(lambda _=False, k=i: self._at_remove(k))
            lay.addWidget(b)
        lay.addStretch(1)
        self.at_bar.setVisible(bool(self._at_refs))

    def _at_remove(self, i: int):
        if not (0 <= i < len(self._at_refs)):
            return
        _, ref = self._at_refs.pop(i)
        cur = self.input.toPlainText()
        if ref in cur:
            self.input.set_text_quiet(cur.replace(ref, "", 1))
        self._at_redraw()

    def _at_sync(self):
        """文本被手动清空/删改时，把已不存在的引用条同步移除。"""
        t = self.input.toPlainText()
        kept = [(l, r) for l, r in self._at_refs if r in t]
        if len(kept) != len(self._at_refs):
            self._at_refs = kept
            self._at_redraw()

    def _expand_refs(self, text: str) -> str:
        """@引用：@后跟 文件完整路径 / @文件名 / @“含空格路径” / 资料库词条 →
        把内容带进上下文再继续干活。

        v0.26.4：支持 @“路径” 引号写法（含空格的文件不再截断）；
        纯引用消息里找不到的 @xxx 会被剥掉并提示——不会把 "@小U" 这类残渣
        原样丢给模型造成"想起你了/瞎聊"；正文较长的消息（代码/文章）不剥，防误伤。
        """
        if "@" not in text:
            return text
        import agent_tools as AT
        toks = re.findall(r"@(?:[“\"]([^”\"]+)[”\"]|([^\s@，。；!？\"'（）]+))", text)
        tokens = [q1 or q2 for q1, q2 in toks]
        if not tokens:
            return text
        added, pieces, missed = [], [], []
        for tok in tokens:
            if tok in added or tok in missed:
                continue
            cand = None
            p = tok
            if not os.path.sep in p and not os.path.exists(p):
                p = os.path.join(self.cfg.get("dev_root", ""), tok)
            p2 = AT.resolve_path(tok) if not os.path.exists(p) else p
            if p2 and os.path.isfile(p2) and os.path.getsize(p2) < 60 * 1024:
                cand = p2
            elif p2 and os.path.isdir(p2):
                missed.append(tok)                 # 目录不带内容；提示用户换文件
                continue
            else:  # 资料库词条：标题/关键词命中
                try:
                    import knowledge as K
                    for e in K.search(tok, limit=3):
                        if tok in e.get("title", "") or tok in " ".join(e.get("tags", [])):
                            cand = e.get("src") or ""
                            if cand and os.path.isfile(cand):
                                break
                            cand = ""
                except Exception:
                    pass
                if not cand:  # 纯文件名兜底：在常用目录里按名找（不递归太深）
                    for folder in (self.cfg.get("dev_root", ""),
                                   AT.desktop_dir(),
                                   os.path.join(os.path.expanduser("~"), "Documents"),
                                   os.path.join(os.path.expanduser("~"), "Downloads")):
                        if not folder or not os.path.isdir(folder):
                            continue
                        hits = AT.find_files(tok, folder) or []
                        if hits:
                            cand = hits[0]
                            break
            if not cand:
                missed.append(tok)
                continue
            try:
                with open(cand, "r", encoding="utf-8", errors="ignore") as f:
                    fbody = f.read()[:8000]
            except Exception:
                missed.append(tok)
                continue
            added.append(tok)
            pieces.append("【引用：%s】\n%s" % (os.path.basename(cand), fbody))
        # 去掉所有 @引用标记后剩下的正文：只有一丁点（甚至没有）→ 整条只是引用尝试
        body = re.sub(r"@(?:[“\"][^”\"]+[”\"]|[^\s@，。；!？\"'（）]+)", "", text).strip()
        ref_only = len(body) < 30
        if missed and ref_only:
            for tok in missed:                   # 未找到的标记剥掉，别让它进模型
                text = re.sub(r"@(?:[“\"]%s[”\"]|%s)" % (re.escape(tok), re.escape(tok)),
                              "", text, count=1)
        if not pieces and missed:
            self._append("系统", "🔎 没找到 @%s——@后面要写：文件完整路径（如 @D:\\报告\\方案.md）、"
                                 "文件名（我按名字找）、或资料库里的词条。"
                         % "、".join(missed[:3]))
            return text
        if missed:
            self._append("系统", "⚠ @%s 没找到，已忽略（可给完整路径）。已读入 %d 个：%s"
                         % ("、".join(missed[:3]), len(added), "、".join(added)))
        if added:
            self._append("系统", "📎 已把 %d 个引用读入上下文：%s"
                         % (len(added), "、".join(added)))
        if not added:
            return text
        return "\n\n".join(pieces) + "\n\n" + text

    # ---------- 交互 ----------
    def send(self, skip_busy: bool = False):
        # 先把上一轮可能还在播的打字机补完（此刻末尾块还是它的，能原地覆盖），
        # 再走下面的 append —— 顺序反了会把 AI 的话拆成两段。
        try:
            self._finish_reveal()
        except Exception:
            logging.exception("finish_reveal on send failed")
        text = self.input.toPlainText().strip()
        self._reply_dialect = ""          # v0.27.8+：每轮清空，_observe_speech 会重新判定
        if not text:
            # v0.29：允许"只带附件不写字" —— 已经选了图片/文件，就不该逼用户再打一句话
            if getattr(self, "_pending_files", None) or getattr(self, "_pending_imgs", None):
                text = "（请看下面附上的文件）"
            else:
                return
        # v0.26.1：剥掉称呼前缀（小U，/@小U：/@小U 帮我…），命令识别不再被称谓挡住
        try:
            nm = re.escape(str(self.cfg.get("name", "小U")))
            text = re.sub(r"^(?:@)?" + nm + r"\s*[，,、:：]?\s*", "", text, count=1)
        except Exception:
            pass
        if not text.strip():
            return
        if text.startswith("/"):                 # 快捷命令：/clear /new /mode …
            self.input.clear()
            self._append("你", html.escape(text))
            self._run_cmd(text)
            return
        # v0.26.4 @引用：展开后的全文（含文件正文）只进模型上下文 ctx；
        # “你”气泡显示用户原话（干净，不刷一大坨引用正文）
        ctx = text
        if "@" in text:
            try:
                ctx = self._expand_refs(text) or text
            except Exception:
                ctx = text
        if not ctx.strip():
            return
        # v0.27 Phase B：把拖入/粘贴图片的本地视觉理解注入模型上下文（气泡保持干净）
        try:
            _img_ctx = self._drain_img_ctx()
        except Exception:
            _img_ctx = ""
        if _img_ctx:
            ctx = ctx + "\n\n" + _img_ctx
        # v0.29：把「＋」选中的附件正文注入上下文（同样不进气泡）
        try:
            _file_ctx = self._drain_file_ctx()
        except Exception:
            _file_ctx = ""
        if _file_ctx:
            ctx = ctx + "\n\n" + _file_ctx
        # v0.30.17：产出页「下一步」发来的请求 —— 只把"在说哪个产物"注入上下文，
        # 气泡保持用户原话（与附件正文同一个套路）。
        try:
            _eff_ctx = self._drain_effect_ctx()
        except Exception:
            _eff_ctx = ""
        if _eff_ctx:
            ctx = ctx + "\n\n" + _eff_ctx
        if self.busy and not skip_busy:
            # v0.27.8 工作栏目语义判读：疑问/讨论/修改类插话 → 立即分析/答复，不排队
            # （新开工指令才排队，等当前重活跑完；问问题/提修改直接聊）
            _it = self._classify_intent(text)
            if _it in ("question", "correction", "chat"):
                pass   # 落到下方对话分支，立刻回复（当前重活继续在跑）
            else:
                self.input.clear()
                self._append("你", html.escape(text))
                self._pend.append({"conv_id": self.conv_id, "text": text})
                n = len([p for p in self._pend if p["conv_id"] == self.conv_id])
                cur_turn = self._slot_turn()
                secs = int(time.time() - cur_turn["t0"]) if cur_turn else 0
                self._append("系统",
                             f"这条先排队（第 {n} 条，上一条已跑 {secs}s，完成会自动接着干；"
                             f"想插队就点「⏹ 停止」取消当前）。")
                return
        self.input.clear()
        self._append("你", html.escape(text))
        # v0.27.3 语言天赋：每收到一句，就学一点这位用户的说法（口音/方言）
        self._observe_speech(text)
        # —— v0.31.3 上下文继承（**必须在下面所有栏目分支之前算**，一处修复全栏目受益）——
        #    「请立即开始 / 就用刚才那个 / 再来一份」这类话自己不含信息，必须从上文补全；
        #    否则 `CHIP_SEED` 会把它包成「帮我开发一个项目：请立即开始」直接送进模型，
        #    模型只能凭空造 —— 这就是真机上"出来一个不是我想要的开发"的机制。
        self._inherit_req = ""
        self._inherit_src = ""
        try:
            if self._is_inherit_req(text):
                _ch = (self._chip if self.mode == "work" else "") or ""
                self._inherit_req = self._inherit_requirement(_ch)
                if self._inherit_req:
                    self._inherit_src = ("栏目上一轮" if self._chip_req.get(_ch)
                                         else "开发台账" if (getattr(self, "dev", None) or {})
                                         .get("last_req") else "上文")
                    # ★ 诚实边界：**明确告诉用户"我按哪句话继续"**，说错了用户能当场纠正。
                    #   真机上用户最难受的正是"它做了个东西，但不是我想要的，还没告诉我依据是什么"。
                    self._append("系统",
                                 "按上文继续：<b>%s</b>（本轮：%s）"
                                 % (html.escape(self._inherit_req[:70]),
                                    html.escape(text[:20])))
        except Exception:
            logging.exception("inherit context failed")
        self.history.append({"role": "user", "content": ctx})
        self.history = self.history[-500:]
        self._persist_conv()                     # 用户这句立刻落盘（聊天永不丢）
        # 拟人情绪即时反应（P3）：在 UI 线程立刻触发头像表情 + 桌面小人事件
        try:
            self._react_affect(text)
        except Exception:
            pass
        # —— v0.24 团队触发：「让我的团队做 X」→ 一键开工（优先级高于普通对话） ——
        try:
            if self._team_from_chat(text):
                return
        except Exception:
            pass
        # —— v0.26 开发续改路由：已有开发项目 + 明显在改它 → 台账精准续改 ——
        try:
            if self._dev_try_continue(text):
                return
        except Exception:
            pass
        # v0.19：用户反馈 → 性格在线调参；正反馈 → 上一轮问答沉淀 SFT 样本
        try:
            self._user_feedback_signal(text)
        except Exception:
            pass
        # —— 工种分栏：选了具体工种 → 按语义定向：疑问/讨论/修改走对话分析，
        #    只有明确的新开工指令才强制 CHIP_SEED 开工（不再"不管对不对就开干"） ——
        chip = self._chip if (self.mode == "work" and self._chip) else None
        _intent = self._classify_intent(text) if chip else "order"
        _route_chat = bool(chip) and _intent in ("question", "correction", "chat")
        direct_copy = False
        req = text
        agent = None
        if _route_chat:
            # 当作普通对话：模型按工作上下文分析/回答/修改，不再套开工种子句
            agent = None
        elif chip == "copy":
            direct_copy = True
            req = ("（文案任务：请直接输出成稿正文，第一句就是内容本身；"
                   "不要解释、不要发能力清单、不要反问。风格贴合要求。）\n" + text)
        elif chip in ("video", "manga", "image"):
            # v0.31.3：指代型指令（"再画一张""就用刚才那个"）要把**上一轮的画面需求**
            #   带进来 —— 创作栏目只认同栏目上下文（见 `_inherit_requirement`）。
            agent = (chip, self._inherit_req or text)   # 直连创作引擎：真出图/真成片
        elif chip == "ad":
            # 广告设计版（v0.30.4；v0.30.13 重做）：
            # 以前这里只调 `AD.design()` —— 它**只建一条空台账**（永远 running、0%），
            # 再把提示词交给 **image** 分支出图，于是"广告设计"与"图像"栏目产出
            # 完全一样（小志 2026-09-17 真机反馈："广告设计的意义在哪"）。
            # 现在这里只负责**算好策略层 + 文案**给用户看；"方案 .md + 主视觉"
            # 由 `_create_ad`（kind="ad" 分支）一次做完 —— 台账只留一条。
            _ad_prompt = text
            try:
                _cw = AD.copywrite(text, text, llm_fn=self._work_llm())
                # v0.30.10（#6）：先给**创意说明（策略层）**再给设计，像广告公司那样交活
                _brief = AD.creative_brief(text, text, llm_fn=self._work_llm())
                _br_src = ("模型" if _brief.get("source") == "llm"
                           else "⚠️ 离线模板（模型不可用）")
                self._append("我",
                    "📣 广告设计 · 创意说明（%s）\n"
                    "· 传播目标：%s\n· 目标人群：%s\n· 核心主张：%s\n"
                    "· 调性：%s\n· 媒介建议：%s\n· 主视觉构思：%s"
                    % (_br_src, _brief.get("goal", ""), _brief.get("audience", ""),
                       _brief.get("message", ""), _brief.get("tone", ""),
                       _brief.get("media", ""), _brief.get("visual", "")))
                # v0.30.6：文案来源必须写在脸上 —— 模板稿 vs 模型稿，用户有权知道
                _ad_src = ("模型" if _cw.get("source") == "llm"
                           else "⚠️ 离线模板（模型不可用）")
                self._append("我", "📣 广告设计｜标题：%s ｜ %s ｜ 卖点：%s ｜ 文案来源：%s" % (
                    _cw.get("headline", ""), _cw.get("cta", ""),
                    (_cw.get("body") or "")[:40], _ad_src))
                _ad_prompt = AD.build_prompt(text, text)
            except Exception:
                _cw, _brief = {}, {}
                _ad_prompt = AD.build_prompt(text, text)
            # 已算好的部分挂这儿给 `_create_ad` 用 —— 不重算（省一次模型调用，
            # 也避免"聊天里显示的是 A 稿、落盘的却是 B 稿"）
            self._ad_pending = {"theme": text, "copy": _cw, "brief": _brief,
                                "prompt": _ad_prompt}
            agent = ("ad", text)
        elif chip:
            # v0.31.3：种子句里的「{要求}」用**继承来的真实需求**（若有），
            #   并把本轮那句指代话附在后面让模型知道"这次要干什么"。
            #   旧版把「请立即开始」原样当需求 → 模型凭空造项目。
            if self._inherit_req:
                req = self.CHIP_SEED.get(chip, "{要求}").format(
                    要求="（沿用上文需求）" + self._inherit_req
                         + "\n（本轮要求：" + text + "）")
                # ★★ 关键：**路由必须由栏目自己的种子句决定**。
                #   否则继承来的需求会"篡位" —— 比如在「📄 Word」栏目里继承到一句
                #   开发需求，`_detect_agent` 会认出"开发"而把栏目判成 `project`，
                #   于是用户要的是 Word，得到的却是一个项目（探针实测抓到这个回归）。
                #   所以：先用**空要求**的种子句定 kind，再把完整 req 当载荷装上。
                _kind = self._detect_agent(
                    self.CHIP_SEED.get(chip, "").format(要求=""), allow_cap=False)
                if _kind:
                    agent = (_kind[0], req)
            else:
                req = self.CHIP_SEED.get(chip, "{要求}").format(要求=text)
            # 记住本栏目这一轮的需求，供**下一句**指代（"再来一份"）使用
            try:
                self._chip_req[str(chip)] = (self._inherit_req or text)[:600]
                self._save_work_state()
            except Exception:
                logging.exception("remember chip req failed")
        allow_cap = self.mode != "work"          # 干活模式下不把句子当"问能力"
        if agent is None:
            agent = self._detect_agent(req, allow_cap=allow_cap)
        # —— 创作引擎懒接入：真动手(work 模式)且要出图/成片时，没配置就先弹接入框；
        #    输入自动存进设置（cfg["engines"]），下次不再问。取消则降级成提示词/脚本 ——
        if agent and agent[0] in ("image", "video", "manga", "ad") and self.mode == "work":
            # 广告共用**图像**引擎（不引入第二种引擎，用户不用再配一次）；
            # 没接引擎也不空手：`_create_ad` 照样交方案，图那栏如实写「待生成」。
            conf = self._ensure_image_engine("image" if agent[0] == "ad"
                                             else agent[0])
            if not conf and agent[0] != "ad":
                agent = self._engine_refuse_fallback(agent[0], text)
        # —— 质疑/纠错（"你怎么写记住啦？"）→ 先认错解释+撤销指引，优先级最高 ——
        rb = self._rebuke(text)
        if rb:
            self._finish(rb)
            return
        quick = todo_mod.handle(text)
        # 全栈项目意图优先级最高（避免"做个待办应用"被当成记待办）
        if quick and not (agent and agent[0] == "project"):
            self._finish(quick)
            return
        # 模式门（v0.18.0 重做）：不再弹"要不要开工"的串岗提醒卡。
        # 聊天模式：强指令（帮我写/做个/生成/开发…）→ 直接开工、不打扰；
        #           弱提及（只是聊到"方案/PPT"这类词）→ 安心聊天，绝不提示、绝不动手。
        # ★v0.31.2：判据从 `_is_direct_order`（**硬上限 60 字**）换成 `_strong_order`。
        #   旧行为下"需求写得越具体越不可能开工"：真机事故
        #   「帮我开发一个能自动抓取价格、对比历史最低价、并在降价时提醒我的比价系统」
        #   38 字 → 命中 project，却因 60 字判据不成立被**静默**降级成聊天，
        #   连一句"我没动手"都没有 —— 用户看到的就是"没有任何动作"。
        _detected = agent                        # 留底：用来在降级后说清"我没动手"
        _confirmed = False                       # 本轮是否刚消费掉一次"待确认"
        if agent and agent[0] in _HEAVY_KINDS and self.mode != "work":
            if not self._strong_order(text):
                agent = None
        # ★★★ v0.31.2 最重要的一个修复：待确认（`_pending_work`）的**回收**必须
        #   放在 agent 判定**之外**。
        #
        #   旧写法把回收嵌在 `if agent and agent[0] in _HEAVY_KINDS ...` 里面，
        #   而用户回的确认口令是「开始」—— `_detect_agent("开始")` 返回 **None**，
        #   于是整个 if 不成立 → 回收分支永远跑不到。后果（已在未改动的
        #   pasm-qclaw 副本上实测复现）：
        #     ① 说「帮我开发一个记账系统」→ 出确认墙，`_pending_work=True`，0 动作；
        #     ② 回「开始」→ `_pending_work` **仍是 True**、**0 动作**、
        #        **连一句回复都没有**（界面像死了）。
        #   这正是小志反馈「我让桌面应用帮我开发时，为什么没有任何动作」的真凶：
        #   确认墙是个死胡同 —— 唯一能开工的口令恰恰被路由忽略。
        if self.mode != "work":
            _pw = getattr(self, "_pending_work", None)
            if _pw is not None:
                _low = (text or "").strip()
                if _RE_PLAN_CONFIRM.match(_low):
                    self._pending_work = None
                    agent = _pw                      # 确认 → 往下走立即执行
                    # ⚠️ 必须记住"这轮已经确认过了"：否则下面那道确认墙会看
                    #    `_strong_order("开始")` 为真（"开始"本身就在直接指令词表里）
                    #    而**再问一次**，用户永远等不到开工 —— 实测就是这个症状。
                    _confirmed = True
                elif _RE_PLAN_REPLAN.match(_low):
                    self._pending_work = None
                    self._finish("好，这轮就只聊，不帮你动手啦～")
                    return
                else:
                    # 又是一条新的请求：它自己也是"重活开工"→作废旧待确认、
                    # 交给下面的确认墙按新需求重问一次；否则按聊天走（不执行上次的活）。
                    self._pending_work = None
                    if not (agent and agent[0] in _HEAVY_KINDS
                            and self._strong_order(text)):
                        agent = None
        # —— v0.22 计划先行：复杂任务先出计划待确认；待确认计划在此回收（开始/改） ——
        if self._plan_gate(text, agent, direct_copy):
            return
        # —— v0.28.x 干活前确认（聊天模式下的重活）——
        # 用户明确要"动手干活"（生成文件/写码/做项目等）时先问一句确认，避免误触发；
        # 纯讨论/问句不弹确认、不干活。干活栏目(work 模式)下仍直接开工（用户已在该栏目）。
        if (not _confirmed) and agent and agent[0] in _HEAVY_KINDS and self.mode != "work":
            if self._strong_order(text) and not self._askish(text):
                self._pending_work = agent
                # v0.31.2：确认话术改成**可点**入口 —— 旧文案只说"回「开始」"，
                # 真机上用户往往换个说法（"好的""做吧"之外的句子），
                # 于是待确认被作废、这轮按聊天走，看着还是"没动作"。
                self._finish(
                    f"⚙️ 你这是要我**动手干活**（{WORKLOG.kind_label(agent[0])}）对吧？"
                    f"（现在是💬聊天模式，重活我不会擅自开工）\n\n"
                    f"回一句 **开始**，或点下面的入口，我立刻开做；"
                    f"不想做回「不用」；只是想聊聊这个话题就回「聊聊」。\n\n"
                    f"[⚙️ 立刻开工](pasm://confirmwork)")
                return
        # ★v0.31.2 诚实边界：这次识别成"重活"、但因为判据不成立被降级成聊天 ——
        #   必须**说出来**。旧版一声不响地走聊天分支，用户完全无从判断
        #   "它是没听懂，还是没动手"，只能反复重说（真机反馈的"没有任何动作"）。
        if (agent is None and _detected and _detected[0] in _HEAVY_KINDS
                and self.mode != "work"):
            self._append(
                "系统",
                "💬 这句话我按**聊天**处理了，**没有动手**。"
                "要我真干活的话，把要求说具体一点"
                "（例：`帮我开发一个记账系统`），或切到「🔧 干活」栏目。")
        if agent:
            turn = self._begin_turn(self._slot_key(), self.conv_id,
                                    self.history, self.cog, text, "agent", agent)
            # v0.31.2：开工前清空过程流 —— 这一轮的过程从零开始，卡片流不会串轮。
            try:
                self._step_reset()
                self._step("plan", "开工", WORKLOG.kind_label(agent[0]) if WORKLOG else agent[0],
                           "本轮动作会实时显示在这里")
            except Exception:
                pass
            # v0.27.4 工作任务：这次干活挂成一条任务（工作台可见、可点开就地讨论）
            _wid = self._work_open(text, agent)
            # v0.30.17：记下来 —— 产出页的「确认留存」要写回这条工作台账。
            self._cur_wid = _wid or ""
            # v0.31.0：也记下**本轮用户原话** —— 产出页块头那行"第 N 轮 · 要求：…"
            # 靠它。没这句，用户回头看历史块时就不知道当时提的是什么要求。
            self._cur_req = (text or "").strip()[:160]
            if _wid:
                try:
                    self._sync_pet_work()
                except Exception:
                    pass            # v0.26.1：重活开工前给一句可见提示——期间可切页/看别的会话，结果不会丢
            if agent[0] in ("project", "script", "genppt", "gendoc", "genxls",
                            "skill", "weblearn", "video", "image", "manga", "ad"):
                try:
                    _tip = ("⏳ 正在干活（可能要几十秒到几分钟）——完成会自动显示在这里；"
                            "期间你可以随便切页或查看别的会话，结果不会丢。")
                    if _wid:
                        _tip += (f"\n这次已记入「🛠 工作台」· {WORKLOG.kind_label(agent[0])}"
                                 f"（#{_wid}）；点它可查看进度与产物，也能在里面就地讨论。")
                    self._append("系统", _tip)
                except Exception:
                    pass

            def job():
                _ok = True
                try:
                    reply = self._agent_run(agent)
                except GW.Cancelled:
                    turn["cancel"].set()          # 已点停止：结果丢弃
                    reply = "⏹ 已停止。"
                    _ok = False
                except Exception as ex:
                    logging.exception("agent run failed")
                    reply = "（执行工具时出错：" + str(ex) + "）"
                    _ok = False
                self._work_close(_wid, reply, _ok)
                self._ui(lambda: self._turn_done(turn, reply))
            threading.Thread(target=job, daemon=True).start()
            return
        score = classify(text)
        turn = self._begin_turn(self._slot_key(), self.conv_id,
                                self.history, self.cog, text, "chat")
        turn["score"] = score
        cog0 = turn["cog"]
        hist0 = turn["hist"]

        def prepare_and_reply():
            snap = self._grow(score, text)          # 线程内完成认知成长，UI 不卡
            # 认知皮层：先想好"此刻怎么回话"（意图/情绪姿态/检索词/温度），再动嘴
            dec = COG.decide(req, cog0, snap, score)
            system = self._build_system(snap, user_text=text, cogd=dec)
            temp = dec["temperature"]
            reply = None
            u = req if direct_copy else text        # 文案任务把"直接出稿"指令带给模型
            # v0.22 感知提速：普通对话走真流式——首 token 即上屏（打字机），
            # 不再"全文生成完才假打字"。感知响应速度 = 首 token 时间。
            ep0 = self._endpoint()
            on_delta = None
            on_think = None
            if ep0 and not turn["cancel"].is_set():
                _st = {"t": 0.0}
                _stt = {"t": 0.0}

                def on_delta(_d, full):
                    turn["live_full"] = full          # 无节流，保证最终态完整
                    now = time.time()
                    if now - _st["t"] >= 0.12:      # 节流 ~120ms，刷屏不抖
                        _st["t"] = now
                        self._ui(lambda f=full: self._turn_live_push(turn, f))

                def on_think(_d, full):
                    # v0.28.3 DeepSeek 式思考：灰度实时上屏（节流只控刷新，不丢数据）
                    turn["think_text"] = full
                    now = time.time()
                    if now - _stt["t"] >= 0.20:
                        _stt["t"] = now
                        self._ui(lambda f=full: self._turn_think_push(turn, f))
            # B3 双脑总线 v0.22 智能化：只有"需要回忆个人相关"的话才走工具轮。
            # （修复变慢根因：以前每条消息都先工具轮再回答 = 串行两趟完整生成，
            #   本地模型感知延迟直接翻倍；现在普通对话单趟直出。）
            # v0.30.11 #11：MCP 接通后，让"工作"(task_do) 类意图也能走工具总线，
            # 这样模型在工作流里真能调用 pasm-mcp-server 的认知工具；休闲对话仍不进
            # 工具总线（避免给慢模型加 13 个工具的额外预填充负担）。模型不调工具时
            # `_chat_tool` 仍单趟直出，不会变慢。
            _mcp_on = MCPB is not None and bool(MCPB.get_bridge())
            if self.cfg.get("bus", True) and (
                    self._needs_recall(text, dec)
                    or (_mcp_on and (dec or {}).get("intent") == "task_do")):
                try:
                    reply = self._chat_tool(system, u, temperature=temp,
                                            hist=hist0, stop=turn["cancel"])
                except GW.Cancelled:
                    raise
                except Exception:
                    logging.info("bus tool round unavailable -> plain llm", exc_info=True)
            if not reply:
                ep = self._endpoint()
                if ep:
                    reply = self._llm_call(system, u, ep[0], ep[1], ep[2],
                                           temperature=temp, hist=hist0,
                                           stop=turn["cancel"], on_delta=on_delta,
                                           on_think=on_think)
                else:
                    logging.info("offline brain used")
                    reply = offline_reply(text, self.cfg["name"], snap,
                                          self.notes, self.cfg["persona"])
            turn["dec"] = dec
            # v0.27.2 防幻觉守卫：回复宣称"已删除/已清理"，台账里必须有真实账。
            try:
                reply = self._honesty_guard(text, reply)
            except Exception:
                pass
            # v0.28.0 符号推理层·矛盾检测：数值与题设约束冲突时就地更正（闭环最后一关）
            try:
                reply = self._symbolic_guard(text, reply)
            except Exception:
                pass
            # v0.4.1 修补：让 brain 接收本轮反馈，翻译成 PASM reward 驱动情绪/记忆更新
            try:
                if self.brain is not None and reply:
                    self.brain.feedback(
                        user_text=u,
                        llm_reply=reply,
                        score=score,
                    )
            except Exception as ex:
                logging.warning("brain.feedback 失败: %s", ex)
            return reply

        def job():
            try:
                reply = prepare_and_reply()
            except GW.Cancelled:
                turn["cancel"].set()              # 已点停止：结果丢弃，界面早已恢复
                reply = "⏹ 已停止。"
            except Exception as ex:
                logging.exception("reply failed")
                reply = ("（我这边出错了，没有成功回应你。请查看日志：\n"
                         f"{LOG_PATH}\n错误：{ex}）")
            self._ui(lambda: self._turn_done(turn, reply))
        threading.Thread(target=job, daemon=True).start()

    # ---------- 快捷开工 & 聊天内可点链接 ----------
    def _on_anchor(self, url):
        """聊天框内链接：pasm://quick/* → 快捷开工；http(s) → 系统默认浏览器。"""
        try:
            s = url.toString()
        except Exception:
            return
        if s.startswith("pasm://quick/"):
            kind = s.rsplit("/", 1)[-1]
            self._quick_action(kind)
        elif s.startswith("pasm://confirmwork"):
            # v0.31.2：确认墙上的「⚙️ 立刻开工」——等价于用户回了一句"开始"，
            # 直接复用既有确认口令通道（`_RE_PLAN_CONFIRM`），不另造一套状态机。
            if getattr(self, "_pending_work", None) is None:
                self._set_status("这条待确认已经过期了，请把要求再说一遍")
                return
            self.input.setPlainText("开始")
            self.send(skip_busy=True)
        elif s.startswith("pasm://skillmgmt"):
            self._switch_page("skill")
        elif s.startswith("pasm://copy/"):
            # v0.30.6：「📋 复制」按钮（QTextBrowser 里的控件只能用锚点实现）
            tok = s.rsplit("/", 1)[-1]
            txt = (getattr(self, "_copy_store", {}) or {}).get(tok, "")
            if txt:
                try:
                    import qt_compat as _qt
                    _qt.QApplication.clipboard().setText(txt)
                    self._set_status("已复制 %d 字到剪贴板" % len(txt))
                except Exception:
                    logging.exception("copy to clipboard failed")
            else:
                self._set_status("这段内容已不在缓存里（旧消息请重新提问）")
        elif s.startswith(("http://", "https://")):
            try:
                import qt_compat
                qt_compat.QtGui.QDesktopServices.openUrl(qt_compat.QtCore.QUrl(s))
            except Exception:
                pass

    def _quick_action(self, kind: str):
        """把工作选项变成可见的确认步骤：先问清关键参数 → 生成明确指令 → 执行。

        v0.17.5：思考中也允许点开工入口（会切到干活栏目自己的线程，互不干扰）。
        """
        # 开工入口 = 想干活 → 自动切到「干活」栏目（聊天模式下点入口也生效；
        # v0.17.2 槽位化：work 有自己独立的对话线程，切过去不再残留聊天记录）
        if self._active_chip_key() != "work":
            self._set_chip("work")
        from qt_compat import QtWidgets
        QInputDialog, QFileDialog = QtWidgets.QInputDialog, QtWidgets.QFileDialog
        cmd = ""
        if kind == "code":
            topic, ok = QInputDialog.getText(self, "写代码",
                                             "要写什么？（例如：python 脚本 批量重命名文件）")
            if ok and topic.strip():
                cmd = "帮我写" + topic.strip()
        elif kind == "skill":
            topic, ok = QInputDialog.getText(
                self, "用技能开工",
                "已按你刚才那句话匹配好技能；可直接确认，或改写要它做的事：",
                text=getattr(self, "_gated_req", "") or "")
            if ok and topic.strip():
                cmd = topic.strip()
        elif kind == "copy":
            topic, ok = QInputDialog.getText(self, "写文案",
                                             "写什么？（例如：新品发布会的朋友圈文案）")
            if ok and topic.strip():
                cmd = "帮我写一段关于" + topic.strip() + "的文案，要自然有感染力。"
        elif kind == "ppt":
            topic, ok = QInputDialog.getText(self, "做 PPT", "PPT 主题？（例如：AI 教育产品介绍）")
            if ok and topic.strip():
                cmd = "帮我做一份关于" + topic.strip() + "的PPT"
        elif kind == "doc":
            topic, ok = QInputDialog.getText(self, "生成 Word 文档",
                                             "文档主题？（例如：季度工作总结报告）")
            if ok and topic.strip():
                cmd = "帮我写一份关于" + topic.strip() + "的Word文档"
        elif kind == "xls":
            topic, ok = QInputDialog.getText(self, "生成 Excel 表格",
                                             "表格内容？（例如：一季度销售数据统计）")
            if ok and topic.strip():
                cmd = "帮我做一个" + topic.strip() + "的Excel表格"
        elif kind == "project":
            desc, ok = QInputDialog.getText(self, "开发项目",
                                            "要开发什么？\n（例如：记账本待办应用 / 产品展示官网）")
            if ok and desc.strip():
                cmd = "帮我开发一个" + desc.strip() + "（前端网页+后端服务+数据库）"
        elif kind == "web":
            topic, ok = QInputDialog.getText(self, "上网自学",
                                             "想让它上网学什么主题？（例如：基金定投）")
            if ok and topic.strip():
                cmd = "上网学一下" + topic.strip()
        elif kind == "image":
            topic, ok = QInputDialog.getText(
                self, "生成图片",
                "想要什么画面？（例如：一只坐在月亮上的橘猫，插画风；或一张春天的海报）")
            if ok and topic.strip():
                self._chip, self.mode = "image", "work"
                self.cfg["chat_mode"] = "image"
                _save_json(CONFIG, self.cfg)
                self._sync_mode_ui()
                cmd = topic.strip()
        elif kind == "video":
            topic, ok = QInputDialog.getText(
                self, "生成短片",
                "短片主题？（例如：30 秒科普：黑神话悟空讲了什么；生成后产出图文配音短片）")
            if ok and topic.strip():
                self._chip, self.mode = "video", "work"
                self.cfg["chat_mode"] = "video"
                _save_json(CONFIG, self.cfg)
                self._sync_mode_ui()
                cmd = topic.strip()
        elif kind == "manga":
            topic, ok = QInputDialog.getText(
                self, "生成漫剧",
                "漫剧题材？（例如：一只小猫离家出走找家的故事，温馨治愈，做 4 镜样片）")
            if ok and topic.strip():
                self._chip, self.mode = "manga", "work"
                self.cfg["chat_mode"] = "manga"
                _save_json(CONFIG, self.cfg)
                self._sync_mode_ui()
                cmd = topic.strip()
        elif kind == "read":
            path, _ = QFileDialog.getOpenFileName(self, "选择要读的文件")
            if path:
                cmd = "读一下 " + path
        elif kind == "todo":
            task, ok = QInputDialog.getText(self, "记待办",
                                            "要记住什么？（例如：周五前交季度周报）")
            if ok and task.strip():
                cmd = "帮我记个待办：" + task.strip()
        if cmd:
            self._switch_page("chat")
            self.input.setPlainText(cmd)
            self.send()

    # ============ v0.22 计划先行：复杂任务先出计划，确认后逐步执行 ============
    def _plan_gate(self, text: str, agent, direct_copy: bool) -> bool:
        """返回 True 表示本轮已被计划流程接管。

        设计（效率与增强兼得）：
        · 简单任务/创作管线（图、视频、漫剧）/专项执行器（写码、做PPT…）绝不规划，
          原路直通——它们本就有各自的"计划→执行→验证"闭环；
        · 规则能切出 ≥2 步的复杂任务 → 先展示计划，回「开始」确认后逐步干；
        · 待确认期间：回「开始/确认…」= 执行；「不要/算了/改…」= 作废；
          其他输入 = 计划自然失效，走正常聊天（不被卡住）。
        """
        pend = getattr(self, "_pending_plan", None)
        low = (text or "").strip()
        if pend:
            self._pending_plan = None
            if _RE_PLAN_CONFIRM.match(low):
                self._planned_execute(pend)
                return True
            if _RE_PLAN_REPLAN.match(low):
                self._append("系统", "好，这份计划先放下——直接说你的新想法就行～")
                return True
            # 其他输入：计划自然失效，继续走正常流程
        if agent is not None or direct_copy:
            return False
        if not self._endpoint() or not PLN.needs_plan(text):
            return False
        steps = PLN.rule_steps(text)
        turn = self._begin_turn(self._slot_key(), self.conv_id,
                                self.history, self.cog, text, "chat")
        if len(steps) >= 2:                      # 规则直接切好 → 展示计划
            self._pending_plan = {"text": text, "steps": steps}
            reply = ("这件事我拆成了这么几步，你过目：\n\n" + PLN.format_plan(steps) +
                     "\n\n没问题回我一句「开始」，我就按这个一步步干；要调整就直接说。")
            self._ui(lambda: self._turn_done(turn, reply))
            return True

        def job():
            # v0.27 Phase C 量子策略：模糊区（规则拆不开）由叠加态测量决定
            # 「问模型拆一步(decompose)」还是「直接单步干(direct)」——经验+情绪共同演化
            mood = None
            if QTM:
                try:
                    sn = self.agent.snapshot()["emotion"]
                    mood = QTM.mood_bias(sn.get("valence", 0), sn.get("arousal", 0))
                except Exception:
                    mood = None
            choice = (QTM.strategy_for("plan_ambiguous", ["decompose", "direct"],
                                       mood=mood) if QTM else "decompose")
            if choice == "direct":
                try:
                    reply = self._single_step_reply(text, stop=turn["cancel"])
                    if QTM:
                        QTM.record("plan_ambiguous", "direct",
                                   bool(reply and not reply.startswith("⚠")),
                                   note="模糊区直出")
                except GW.Cancelled:
                    turn["cancel"].set()
                    reply = "⏹ 已停止。"
                except Exception as ex:
                    logging.exception("plan gate direct failed")
                    reply = "（直出时出了点岔子：" + str(ex) + "。）"
                self._ui(lambda: self._turn_done(turn, reply))
                return
            try:
                # 规则切不开 → 让模型做一次轻量分解（task=study 小预算）
                steps2 = PLN.make_plan(
                    text, llm_fn=lambda up, sp: self._brain(
                        up, system=sp, max_tokens=300, task="study"))
                if QTM:                          # 量子层反馈闭环：拆解成没成，如实回灌
                    QTM.record("plan_ambiguous", "decompose", len(steps2) >= 2,
                               note="模糊区拆解")
                if len(steps2) >= 2:
                    self._pending_plan = {"text": text, "steps": steps2}
                    reply = ("这件事我拆成了这么几步，你过目：\n\n" +
                             PLN.format_plan(steps2) +
                             "\n\n没问题回我一句「开始」，我就按这个一步步干；要调整就直接说。")
                else:                            # 连模型都拆不开 → 结构化单步直出
                    self._pending_plan = None
                    reply = self._single_step_reply(text, stop=turn["cancel"])
            except GW.Cancelled:
                turn["cancel"].set()
                reply = "⏹ 已停止。"
            except Exception as ex:
                logging.exception("plan gate failed")
                self._pending_plan = None
                reply = "（规划时出了点岔子：" + str(ex) + "。直接说需求我按老办法来。）"
            self._ui(lambda: self._turn_done(turn, reply))
        threading.Thread(target=job, daemon=True).start()
        return True

    def _single_step_reply(self, text: str, stop=None) -> str:
        """结构化单步生成：分不了步的任务也吃到"模板+验证器+双脑协同"的框架增益。"""
        task = PRT.classify_task(text)
        u, sys_extra = PRT.build_prompt(task, text)
        snap = self.agent.snapshot()
        system = self._build_system(snap, user_text=text, cogd=None) + sys_extra
        # v0.25 性能修复：双脑协同（起草→评审→修正=2~3 次模型调用）只花在"重活"上——
        # 闲聊/简单问答单次直出（此前每条消息都跑协同，首字延迟翻倍，真机反馈变慢）
        if self.cfg.get("coop", True) and self._coop_worth(text, task):
            ans, ev = self._brain_coop(u, system=system, max_tokens=1500, rounds=1)
            if ev:
                return self._symbolic_guard(text, ans) + self._coop_footer(ev)
            return self._symbolic_guard(text, ans or "（这次没产出内容，换种说法再试？）")
        ep = self._endpoint()
        ans = self._llm_call(system, u, ep[0], ep[1], ep[2],
                             temperature=0.7, stop=stop)
        ok, probs = VAL.check("generation" if task != "code" else "code", ans)
        if not ok and not ans.startswith("⚠"):
            try:
                ans2 = self._llm_call(
                    system, u + "\n\n上次输出有以下问题，请修正后重新输出完整内容：\n- "
                    + "\n- ".join(probs), ep[0], ep[1], ep[2],
                    temperature=0.6, stop=stop)
                ok2, _ = VAL.check("generation" if task != "code" else "code", ans2)
                if ok2:
                    ans = ans2
            except Exception:
                pass
        # v0.28.1：工具轮也走符号守卫，确保「向量检索↔符号推理」闭环在每一步都生效
        return self._symbolic_guard(text, ans)

    def _planned_execute(self, plan: dict):
        """确认后的逐步执行：每步 = 结构化提示 + 生成 + 验证（不过就带错重生成一次）。

        每完成一步就刷新一次界面（渐进可见，不等全部干完才出声）。
        """
        text = plan.get("text", "")
        steps = plan.get("steps", [])
        turn = self._begin_turn(self._slot_key(), self.conv_id,
                                self.history, self.cog, text, "chat")
        try:
            fewshot = ML.few_shot_cases(text, 2)
        except Exception:
            fewshot = ""
        marks = "①②③④⑤⑥"

        def job():
            outs = []
            coop_notes = []
            n = len(steps)
            try:
                for i, st in enumerate(steps):
                    if turn["cancel"].is_set():
                        break
                    up, sp = PRT.build_step_prompt(st.get("title", ""), st.get("detail", ""), text)
                    if fewshot:
                        sp += "\n" + fewshot
                    # v0.25 PASM×LLM 真协同：规划好的步骤都是重活，保留协同但只复核一轮
                    if self.cfg.get("coop", True):
                        ans, ev = self._brain_coop(up, system=sp, max_tokens=1800, rounds=1)
                        if ev:
                            coop_notes.append(f"{marks[i]}{st.get('title','')[:12]}："
                                              + ("起草→评审→修正→采纳" if len(ev) > 3
                                                 else "一次过✓"))
                    else:
                        ans = self._brain(up, system=sp, max_tokens=1800)
                    outs.append(f"**{marks[i]} {st.get('title', '')}**\n\n{(ans or '').strip()}")
                    part = ("\n\n---\n\n".join(outs) +
                            (f"\n\n（正在做：{marks[i + 1]} {steps[i + 1].get('title', '')}…）"
                             if i + 1 < n else ""))
                    self._ui(lambda p=part: self._turn_live_push(turn, p))
                if turn["cancel"].is_set():
                    reply = "⏹ 已停止。"
                else:
                    reply = "✅ 按计划干完了：\n\n" + "\n\n---\n\n".join(outs)
                    if coop_notes:
                        reply += ("\n\n---\n<sup style='color:#94a3b8'>PASM×LLM 真协同（每步："
                                  + "；".join(coop_notes)
                                  + "），教训已回写记忆</sup>")
            except GW.Cancelled:
                turn["cancel"].set()
                reply = "⏹ 已停止。"
            except Exception as ex:
                logging.exception("planned execute failed")
                reply = ("执行到一半出了岔子：" + str(ex) +
                         ("\n\n已完成的部分：\n\n" + "\n\n---\n\n".join(outs)
                          if outs else ""))
            self._ui(lambda: self._turn_done(turn, reply))
        threading.Thread(target=job, daemon=True).start()

    def _valid_local_model(self) -> str:
        """模型选择：默认最小已装(快)；仅用户显式锁定(设置里选过)才保持大模型。"""
        installed = list_ollama_models()
        if self.cfg.get("model_locked"):
            cur = self.cfg.get("local_model")
            if cur in installed:
                return cur
        pick = pick_local_model(installed) if installed else ""
        if pick and pick != self.cfg.get("local_model"):
            self.cfg["local_model"] = pick
            _save_json(CONFIG, self.cfg)
        return pick or "qwen2.5"

    # ============ B3 双脑总线：LLM 主动调用 PASM 服务 ============
    TOOLS = [
        {"type": "function",
         "function": {"name": "recall", "description": "按需回忆与用户相关的事（偏好/经历/学过知识），"
                       "当回答需要'记得你说过/你偏好/你学过'时调用",
                      "parameters": {"type": "object",
                                     "properties": {"kw": {"type": "string",
                                        "description": "要回忆的关键词或主题，简短"}},
                                     "required": ["kw"]}}},
        {"type": "function",
         "function": {"name": "verify", "description": "回答涉及用户个人事实或行动建议前，自检是否与"
                       "用户偏好/记录/已学知识冲突（预防说错）",
                      "parameters": {"type": "object",
                                     "properties": {"claim": {"type": "string",
                                        "description": "想对用户说的话或建议"}},
                                     "required": ["claim"]}}},
        {"type": "function",
         "function": {"name": "learn", "description": "用户明确纠正、夸奖或给出新事实/偏好时，立即记住",
                      "parameters": {"type": "object",
                                     "properties": {"category": {"type": "string",
                                        "enum": ["偏好", "事实", "纠正", "夸奖"]},
                                        "content": {"type": "string",
                                        "description": "要记住的内容"}},
                                     "required": ["category", "content"]}}},
        {"type": "function",
         "function": {"name": "mood", "description": "读取我当前的情绪与性格状态",
                      "parameters": {"type": "object", "properties": {}}}},
        # v0.30.7：只读的系统真实状态。加它的原因很直接 —— 用户问"我电脑卡不卡"
        # 时，如果正则没命中，模型原本只能凭记忆答"应该还行"，那是**编造**。
        # 只读、无参数、无副作用，所以不给它加确认环节（写操作才需要）。
        {"type": "function",
         "function": {"name": "sysinfo",
                      "description": "读取这台电脑的真实状态：CPU 占用与核数、内存、"
                                     "磁盘剩余、开机时长。回答「我电脑卡不卡 / 内存还剩"
                                     "多少 / 配置怎么样」这类问题前调用；不要凭记忆猜。",
                                     "parameters": {"type": "object", "properties": {}}}},
        # v0.30.11 #2-A：真装框架。用户说"搭个网站/开发一个后台"时，
        # 要的是**真跑官方脚手架 + 真装依赖**，不是一堆文件。
        {"type": "function",
         "function": {"name": "scaffold_project",
                      "description": "真装框架建项目：按技术栈跑官方脚手架并真装依赖，"
                                     "落盘一个可运行工程（Vue / React / Express / "
                                     "FastAPI / Flask / 静态页）。用户说「搭个网站 / "
                                     "开发一个后台 / 建个项目 / 写个能跑的」时调用。"
                                     "缺运行器或装不上会如实说明缺什么，不会谎报成功。",
                      "parameters": {"type": "object",
                                     "properties": {
                                         "goal": {"type": "string",
                                                  "description": "用户原话或项目目标（用来识别技术栈与取名）"},
                                         "name": {"type": "string",
                                                  "description": "项目目录名，可省略（自动取目标里的名字）"},
                                         "stack": {"type": "string",
                                                   "enum": ["", "vite-vue", "vite-react",
                                                            "node-express", "fastapi",
                                                            "flask", "static", "python-script"],
                                                   "description": "技术栈；留空则按 goal 自动识别"}},
                                     "required": ["goal"]}}},
        # v0.30.11 #2-B：支付接入状态（只读；密钥永不回显）
        {"type": "function",
         "function": {"name": "payment_status",
                      "description": "查看支付接入状态：当前渠道、是否真实收款、是否就绪、"
                                     "配置文件位置；可按技术栈列出可用的收单适配层文件。"
                                     "回答「怎么收款 / 支付接好没 / 给商城接支付」时调用。"
                                     "默认沙箱（不收真钱），任何密钥都不会回显。",
                      "parameters": {"type": "object",
                                     "properties": {
                                         "stack": {"type": "string",
                                                   "description": "要接入的技术栈（fastapi / flask / node-express），可省略"},
                                         "with_files": {"type": "boolean",
                                                        "description": "是否列出适配层文件清单"}},
                                     "required": []}}},
    ]

    def _all_tools(self):
        """喂给模型的完整工具清单：本地工具 + MCP 工具（#11）。

        仅当 pasm-mcp-server 可连接时才追加 MCP 工具；连不上（未装/未启动）
        就只给本地工具，对话完全不受影响（桥自带失败缓存，不会反复重连）。
        """
        tools = list(self.TOOLS)
        if MCPB is not None:
            try:
                b = MCPB.get_bridge()
                if b is not None and b.available:
                    tools += b.openai_tools()
            except Exception:  # noqa: BLE001
                pass
        return tools

    def _tool_scaffold(self, args):
        """真装框架：跑官方脚手架 + 真装依赖，落盘可运行工程。

        安全：命令来自**固定模板**（argv 列表、shell=False），用户原话只用来
        识别技术栈和取名，绝不拼进命令。装不上就把缺什么原样说清。
        """
        try:
            import scaffold as SC
        except Exception as e:                       # noqa: BLE001
            return "⚠️ 脚手架模块不可用：%s" % e
        goal = (args.get("goal") or "").strip()
        if not goal:
            return "⚠️ 需要给出要建什么（goal）"
        p = SC.plan(text=goal, name=(args.get("name") or "").strip(),
                    root=(self.cfg.get("ws_dir") or "").strip(),
                    stack=(args.get("stack") or "").strip())
        return SC.run(p)["log"]

    def _tool_payment(self, args):
        """支付接入状态（只读）。**任何密钥都不回显**，只报"填没填"。"""
        try:
            import payment as PAY
        except Exception as e:                       # noqa: BLE001
            return "⚠️ 支付模块不可用：%s" % e
        try:
            st = PAY.status()
        except Exception as e:                       # noqa: BLE001
            return "⚠️ 读支付状态失败：%s" % e
        out = ["💳 **支付接入状态**（不含任何密钥明文）",
               "· 当前渠道：%s%s" % (
                   st.get("provider"),
                   "（未知渠道已回落沙箱：%s）" % st["fallback"]
                   if st.get("fallback") else ""),
               "· 真实收款：%s" % ("✅ 已开启（live=true）" if st.get("live")
                                   else "⛔ 未开启（沙箱模式，不会收真钱）"),
               "· 可用性：%s" % ("✅ 就绪" if st.get("ready")
                                 else "⚠️ 未就绪 —— %s" % st.get("reason")),
               "· 配置文件：%s" % st.get("config_path"),
               "· 微信：%s ｜ 支付宝：%s" % (
                   "已填凭据" if st.get("wechat_configured") else "未填凭据",
                   "已填凭据" if st.get("alipay_configured") else "未填凭据")]
        if args.get("with_files"):
            try:
                f = PAY.adapter_files((args.get("stack") or "").strip(),
                                      PAY.load_config())
                out.append("· 可接入的适配层文件：%s" % "、".join(sorted(f)))
            except Exception as e:                   # noqa: BLE001
                out.append("· 列适配层文件失败：%s" % e)
        return "\n".join(out)

    def _run_tool(self, name, args):
        # v0.30.11 #11：MCP 工具统一带 `mcp_` 前缀，交给桥转发给 pasm-mcp-server
        if name.startswith("mcp_"):
            if MCPB is None:
                return "⚠️ MCP 模块未加载（mcp_bridge 缺失）"
            b = MCPB.get_bridge()
            if b is None:
                return "⚠️ MCP 未连接（pasm-mcp-server 未安装或启动失败）"
            return b.call(name, args)
        # v0.30.11 #2：真装框架 / 支付状态（固定模板或只读，不接受任意命令）
        if name == "scaffold_project":
            return self._tool_scaffold(args)
        if name == "payment_status":
            return self._tool_payment(args)
        snap = self.agent.snapshot()
        e = snap["emotion"]
        if name == "recall":
            kw = (args.get("kw") or "").strip()
            parts = []
            # 情景/程序/知识层（自动检索，最贴题的部分放最前）
            if kw:
                layer = ML.recall_layers(kw, self.notes, self.prefs, top=3,
                                         with_user_hits=True)
                if layer:
                    parts.append(layer)
            for n in reversed(self.notes[-40:]):
                t = n.get("tag", "")
                if not kw or kw in t or any(c in t for c in kw if c.strip()):
                    parts.append("经历：" + t[:70])
                    break
            for p in reversed(self.prefs[-40:]):
                t = p.get("tag", "")
                if not kw or kw in t:
                    parts.append(f"偏好[{p.get('cat', '')}]：" + t[:70])
                    break
            know = knowledge.learned_bullets(2)
            if kw and know and kw in know:
                parts.append("学过：" + know[:120])
            return "\n".join(parts[:4]) or (
                "（没想起与「" + kw + "」相关的记录）" if kw else "（还没有相关记录）")
        if name == "verify":
            claim = args.get("claim", "")
            hits = []
            for p in self.prefs:
                t = p.get("tag", "")
                if t and (t[:6] in claim or claim[:6] in t or any(w in claim and w in t
                        for w in ("不", "别", "喜欢", "讨厌", "过敏") if len(w) == 1)):
                    hits.append(t)
            base = "我无法用世界模型验证这句话的客观事实（真实世界验证需要工具/联网），但我做了个人记录一致性检查。"
            if hits:
                return "需注意：" + base + "它与你的一条记录可能冲突：「" + hits[0][:60] + "」建议措辞更稳妥。"
            return "个人记录一致性：未发现明显冲突。" + base
        if name == "learn":
            cat = args.get("category", "事实")
            content = args.get("content", "")
            if not content:
                return "（没有内容可记）"
            if cat in ("纠正", "夸奖"):
                note = f"（用户{cat}了我）{content}"
                self.notes.append({"t": time.strftime("%m-%d %H:%M"), "tag": note[:60]})
                self.notes = self.notes[-200:]
                _save_json(NOTES, self.notes)
                if cat == "夸奖":
                    self._grow(0.5, content)
                return "已记住并反思：我会调整自己。"
            self.prefs.append({"t": time.strftime("%m-%d %H:%M"),
                               "cat": cat if cat in ("偏好", "事实") else "事实",
                               "tag": content[:80]})
            self.prefs = self.prefs[-200:]
            _save_json(PREFS, self.prefs)
            return "已写入我的长期记忆。"
        if name == "mood":
            return (f"愉悦 {e['valence']:+.2f} / 平静 {e['serotonin']:.2f} / "
                    f"成长阶段 {snap['development']['stage']} / "
                    f"性格开放{snap['personality']['openness']:+.2f}")
        if name == "sysinfo":
            # 只读、无副作用：把真实数字压缩成一行给模型，让它自己组织语言回答
            try:
                import sysops as SYS
                r = SYS.sysinfo()
            except Exception as ex:
                return "（读系统信息失败：{0}）".format(ex)
            if not r.get("ok"):
                return "（没能读到系统信息：{0}）".format(r.get("err") or "未知原因")
            bits = []
            if r["cpu_pct"] is not None:
                bits.append("CPU {0}%".format(r["cpu_pct"]))
            if r["cores"]:
                bits.append("{0} 核".format(r["cores"]))
            if r["mem_total_gb"]:
                used = r["mem_total_gb"] - (r["mem_free_gb"] or 0)
                bits.append("内存 {0:.1f}G/共{1:.1f}G（已用 {2}%）".format(
                    used, r["mem_total_gb"], r["mem_pct"]))
            for d in (r["disks"] or [])[:3]:
                bits.append("{0} 盘剩余 {1:.0f}G".format(d["drive"], d["free_gb"]))
            if r["uptime_h"] is not None:
                bits.append("已开机 {0:.1f} 小时".format(r["uptime_h"]))
            if r["os"]:
                bits.append(r["os"])
            return "；".join(bits) or "（取不到具体数值）"
        return "（未知工具）"

    def _chat_tool(self, system, user, temperature=0.85, hist=None, stop=None):
        """工具化一轮：LLM→(recall/verify/learn/mood)→PASM→LLM。

        v0.22 提速：最多 1 轮工具 + 直接给最终回答（以前最多 4 轮，感知延迟翻倍）；
        并在系统提示里明确"无需回忆就直接回答"，工具轮只在真正需要时发生。
        """
        ep = self._endpoint()
        if not ep:
            raise RuntimeError("no model")
        base, model, apikey = ep
        apikey = apikey or "local"
        hist = self._ctx_msgs(hist)
        if hist and hist[-1].get("role") == "user" and hist[-1].get("content") == user:
            hist = hist[:-1]
        system += ("\n【工具使用纪律】只有当回答确实需要回忆用户相关记录（recall）、"
                   "自检偏好冲突（verify）或记录新偏好（learn）时才调用工具；"
                   "不需要就绝不调用，直接回答用户。")
        msgs = [{"role": "system", "content": system}] + hist + \
               [{"role": "user", "content": user}]
        for _ in range(2):
            try:
                resp = GW.gw.create(
                    base, model, apikey, messages=msgs,
                    temperature=temperature, max_tokens=900, task="tool", attempts=2,
                    extra={"tools": self.TOOLS, "tool_choice": "auto"},
                    stop=stop)
            except GW.Cancelled:
                raise
            except Exception as ex:
                logging.info("tools unsupported -> fallback plain (%s)", str(ex)[:80])
                return self._llm_call(system, user, base, model, apikey,
                                      temperature=temperature, hist=hist,
                                      stop=stop)
            msg = resp.choices[0].message
            if not getattr(msg, "tool_calls", None):
                return (msg.content or "").strip()
            msgs.append({"role": "assistant",
                         "content": msg.content or "", "tool_calls": [
                             {"id": c.id, "type": "function", "function": c.function}
                             for c in msg.tool_calls]})
            for c in msg.tool_calls:
                try:
                    import json as _j
                    args = _j.loads(c.function.arguments or "{}")
                    out = self._run_tool(c.function.name, args)
                except Exception as ex:
                    out = f"工具出错：{ex}"
                msgs.append({"role": "tool", "tool_call_id": c.id, "content": out})
        return "（对话循环过深，已自动收敛）"

    def _needs_recall(self, text: str, dec: dict | None = None) -> bool:
        """v0.22：这条消息是否需要走工具轮回忆个人相关内容（智能总线门控）。"""
        if (dec or {}).get("intent") in ("remember", "followup"):
            return True
        if (dec or {}).get("intent") == "task_do":
            return False
        return bool(_RE_RECALL_HINT.search(text or ""))

    def _turn_live_start(self, turn: dict):
        """真流式：首包到达就在聊天区开出名牌块，后续增量直接刷这一块。"""
        if turn.get("live_started"):
            return
        turn["live_started"] = True
        turn["name_html"] = f"<b>{html.escape(self.cfg['name'])}</b>："
        turn["live_len"] = 0
        self.chat.moveCursor(QTextCursor.End)
        self.chat.append("")
        self._patch_last_block(turn["name_html"], self._name_prefix())

    def _turn_live_push(self, turn: dict, full_text: str):
        """网关线程经 _ui 调度到 UI 线程的增量刷新（节流后调用）。

        已切走/已停止的回合不再刷屏——结果仍会按归属整体归档。
        """
        if not isinstance(full_text, str):
            return
        if turn.get("cancel") is not None and turn["cancel"].is_set():
            return
        self._turn_live_start(turn)
        turn["live_full"] = full_text          # 两条调用路径（聊天流式/干活分步）都在此对齐
        if len(full_text) - turn.get("live_len", 0) < 6:
            return
        turn["live_len"] = len(full_text)
        self._turn_render(turn)

    def _turn_think_push(self, turn: dict, full_think: str):
        """v0.28.3 思考增量刷新：灰度思考实时上屏（DeepSeek 式"想→答"）。"""
        if not isinstance(full_think, str):
            return
        if turn.get("cancel") is not None and turn["cancel"].is_set():
            return
        self._turn_live_start(turn)
        turn["think_text"] = full_think
        self._turn_render(turn)

    @staticmethod
    def _think_html(think: str) -> str:
        """思考文本 → 灰度小字 HTML（空思考返回空串，不打扰）。"""
        t = (think or "").strip()
        if not t:
            return ""
        t = html.escape(t[-4000:]).replace("\n", "<br>")
        return ("<span style='color:#8a9099;font-size:small;'>💭 " + t
                + "</span><br>")

    def _turn_render(self, turn: dict):
        """把「名牌 + 灰度思考 + 正文」重画进当前回合块（单 block，<br> 软换行）。"""
        body = turn.get("live_full") or ""
        self._patch_last_block(turn["name_html"]
                               + self._think_html(turn.get("think_text") or "")
                               + html.escape(body).replace("\n", "<br>"),
                               self._name_prefix())
        self.chat.verticalScrollBar().setValue(
            self.chat.verticalScrollBar().maximum())

    def _finish(self, reply, turn=None):
        self._set_busy(False)
        try:
            streamed = bool(turn and turn.get("live_started") and turn.get("name_html"))
            if streamed:
                # 真流式已把内容打上屏 → 只做最终 Markdown 渲染与朗读，不再假打字
                snap = self.agent.snapshot()
                e = snap["emotion"]
                self.avatar.set_state(e["valence"], e["arousal"],
                                      serotonin=e["serotonin"], speaking=False)
                # v0.28.3：最终态 = 名牌 + 灰度思考 + Markdown 正文
                self._patch_last_block(turn["name_html"]
                                       + self._think_html(turn.get("think_text") or "")
                                       + self._reply_html(reply),
                                       self._name_prefix())
                self.chat.verticalScrollBar().setValue(
                    self.chat.verticalScrollBar().maximum())
                if self.cfg.get("auto_speak"):
                    self.speak(reply)
            else:
                self._reveal(self.cfg["name"], reply)
        except Exception:
            logging.exception("finish display failed")   # v0.26.1：展示失败不吞回复
        # 回复落盘绝不因上面展示异常而跳过（聊天永不丢）
        self.history.append({"role": "assistant", "content": reply})
        self.history = self.history[-500:]
        try:
            self._persist_conv()                     # 回复也立刻落盘
        except Exception:
            logging.exception("persist conv failed")
        # v0.28.x：记住这条回复 —— 用户说「推到我手机」时，推的就是它
        try:
            self._last_reply_text = reply
        except Exception:
            pass
        # v0.28.x：这一轮是**手机发来的**（跨端）→ 把回复推回手机
        if getattr(self, "_bridge_pending", False):
            self._bridge_pending = False

            def _push_back(txt=reply):
                try:
                    import remote_bridge as RB
                    RB.notify(str(txt)[:3000], title="PASM 回复", cfg=self.cfg)
                except Exception:
                    logging.exception("bridge push back failed")

            threading.Thread(target=_push_back, name="pasm-bridge-reply",
                             daemon=True).start()
        snap = self.agent.snapshot()
        self._render_mind(snap)
        # v0.30.17：**效果优先** —— 各工种在产出时已经把"效果"结构化登记好了
        # （`_mark_effect`），这里优先按登记开页（图/视频/站点/广告语一起给全）。
        # 登记不到才退回 v0.30.6 的兜底（从回复正文里正则捞路径）。
        # ⚠️ `_append_effect()` 返回 False 是"确实什么都没展示"的意思，
        #    所以兜底逻辑必须真的挂在这个 False 上 —— 否则兜底永远不生效
        #    （上一版"覆写了钩子却没被调到"就是这么栽的）。
        _shown = False
        try:
            _shown = self._append_effect()
        except Exception:
            logging.exception("append effect failed")
        if not _shown:
            try:
                self._present_in_panel(reply)
            except Exception:
                logging.exception("auto present failed")

    def _name_prefix(self) -> str:
        """当前角色名的气泡前缀（纯文本形式），用于核对块归属。"""
        return str(self.cfg.get("name", "小U")) + "："

    def _patch_last_block(self, inner_html: str, expect_prefix: str | None = None):
        """重画「本回合自己那一块」。

        ⚠️ 身份核对是 v0.30.2 补的：原实现是「删掉文档最后一块再插入」，
        一旦末尾块**不是自己的**就会删掉别人的内容。真机踩到的正是这个 ——
        拟人打字机还在播（约 1.2s）时用户抢着发了新消息，新消息成了「最后一块」，
        于是被删掉、替换成 AI 的半截回复；而 history 里早已落盘，
        所以**切走再切回又看得见**（症状：我说的话不见了，点别的对话再回来才出现）。

        修法：非空块必须带本回合的名牌前缀才允许覆盖；否则**另起一块**，
        绝不删别人的内容。宁可多一个空块，也不能吞掉用户的话。
        """
        doc = self.chat.document()
        blk = doc.lastBlock()
        txt = (blk.text() or "").strip()
        # 空块（刚 append 出来的新块）视为自己的；非空块必须前缀匹配
        mine = (not txt) or (expect_prefix is None) or txt.startswith(expect_prefix)
        tc = self.chat.textCursor()
        tc.movePosition(QTextCursor.End)
        if not mine:
            tc.insertBlock()                    # 另起一块，不碰别人的
        else:
            tc.movePosition(QTextCursor.StartOfBlock)
            tc.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
            tc.removeSelectedText()
        tc.insertHtml(inner_html)

    def _finish_reveal(self):
        """把还在播的打字机**立刻补完并停下**（用户抢话时调用）。

        只 stop() 不够：屏幕会永久停在半截，读起来像"AI 的后半句丢了"——
        与"用户消息被吞"同属内容缺失。这里有完整原文，直接补全。
        必须在 append 用户新消息**之前**调用，否则末尾块已变成用户消息，
        补全会另起一块 → AI 的话被拆成两段。
        """
        st = getattr(self, "_reveal_state", None)
        tm = getattr(self, "_reveal_timer", None)
        if tm is not None:
            try:
                tm.stop()
                tm.deleteLater()
            except Exception:
                pass
            self._reveal_timer = None
        if not st:
            return
        self._reveal_state = None
        try:
            self._patch_last_block(st["name_html"] + st["final_html"],
                                   st["prefix"])
        except Exception:
            logging.exception("finish reveal failed")

    def _reveal(self, name: str, reply: str):
        """拟人流式：逐字显示回复，结束时渲染成 Markdown。"""
        plain = reply
        final_html = self._reply_html(reply)
        name_html = f"<b>{html.escape(name)}</b>："
        _pfix = str(name) + "："              # 身份核对用（纯文本前缀）
        self.avatar.set_state(self.agent.snapshot()["emotion"]["valence"],
                              self.agent.snapshot()["emotion"]["arousal"],
                              serotonin=self.agent.snapshot()["emotion"]["serotonin"],
                              speaking=True)
        # 登记"正在播"：用户中途抢话时，_finish_reveal() 靠它把话补完整
        self._reveal_state = {"name_html": name_html, "final_html": final_html,
                              "prefix": _pfix}
        self.chat.moveCursor(QTextCursor.End)
        self.chat.append("")                     # 新起一块
        self._patch_last_block(name_html, _pfix)
        step = max(1, len(plain) // 40)          # 约 40 帧播完
        i = [0]

        def tick():
            i[0] += step
            if i[0] >= len(plain):
                self._reveal_state = None        # 播完了，无半截残留
                self._patch_last_block(name_html + final_html, _pfix)
                self.avatar.set_state(self.agent.snapshot()["emotion"]["valence"],
                                      self.agent.snapshot()["emotion"]["arousal"],
                                      serotonin=self.agent.snapshot()["emotion"]["serotonin"],
                                      speaking=False)
                if self.cfg.get("auto_speak"):
                    self.speak(reply)
                self.chat.verticalScrollBar().setValue(
                    self.chat.verticalScrollBar().maximum())
                return True
            self._patch_last_block(name_html + html.escape(plain[:i[0]]), _pfix)
            sb = self.chat.verticalScrollBar()
            sb.setValue(sb.maximum())          # 打字时始终跟着滚动，不丢内容
            return False

        def drive():
            if tick() is False:
                timer.start(30)
            else:
                timer.stop()
        # 取消上一轮可能还在播的打字机 —— 两个 timer 同时刷同一块会互相覆盖。
        # （这也是「回复内容错乱/被前一条覆盖」的另一条成因，一并堵住。）
        _old = getattr(self, "_reveal_timer", None)
        if _old is not None:
            try:
                _old.stop()
                _old.deleteLater()
            except Exception:
                pass
        timer = QTimer(self)
        timer.timeout.connect(drive)
        self._reveal_timer = timer
        drive()


    # ================= v0.17.5 回合机制（思考不再锁死界面） =================
    def _slot_turn(self):
        """当前栏目正在跑的回合（无则 None）。"""
        return self._turns.get(self._slot_key())

    def _begin_turn(self, slot: str, conv_id, hist, cog, user_text: str,
                    kind: str, agent=None) -> dict:
        """登记一个回合：冻结上下文快照；界面进入该栏目的"思考中"。"""
        self._turn_seq += 1
        turn = {"no": self._turn_seq, "slot": slot, "conv_id": conv_id,
                "user": user_text, "hist": list(hist or []), "cog": cog,
                "cancel": threading.Event(), "t0": time.time(),
                "kind": kind, "agent": agent, "detached": False,
                "dec": None, "score": 0.0}
        self._turns[slot] = turn
        self._sync_turn_ui()
        return turn

    def _detach_turn(self, slot=None):
        """让当前栏目的回合"让位"（用户开了新对话/点了别的会话）：界面立即恢复可用；
        回合线程继续把话说完，结果按归属收进它原属的会话文件，不打扰新界面。"""
        slot = slot or self._slot_key()
        t = self._turns.pop(slot, None)
        if t:
            t["detached"] = True
            self._sync_turn_ui()
        return t

    def _stop_turn(self):
        """⏹ 停止：取消所有等待中的回合，界面立即恢复，后台结果丢弃。"""
        if not self._turns:
            return
        for t in self._turns.values():
            t["cancel"].set()
        self._turns.clear()
        self._sync_turn_ui()
        self._append("系统", "已停止等待这条回复——界面已恢复，你可以直接发新消息或开新对话了。")

    def _sync_turn_ui(self):
        """按"当前栏目是否有回合在跑"刷新发送区/秒数/停止按钮（唯一 busy 出口）。"""
        t = self._slot_turn()
        self.busy = t is not None
        if not hasattr(self, "send_btn"):
            return
        self.send_btn.setEnabled(not self.busy)
        self.send_btn.setText("思考中…" if self.busy else "发送")
        self.input.setEnabled(not self.busy)
        self.stop_btn.setVisible(self.busy)
        if self.busy:
            self._beat = 0
            if not getattr(self, "_beat_timer", None):
                self._beat_timer = QTimer(self)
                self._beat_timer.timeout.connect(self._tick_status)
            self._beat_timer.start(1000)
            self.status.setText("⏳ 思考中…")
            if getattr(self, "_warn_timer", None):
                self._warn_timer.stop()
            self._warn_timer = QTimer(self)
            self._warn_timer.timeout.connect(self._warn_slow)
            self._warn_timer.setSingleShot(True)
            self._warn_timer.start(20000)
        else:
            if getattr(self, "_beat_timer", None):
                self._beat_timer.stop()
            if getattr(self, "_warn_timer", None):
                self._warn_timer.stop()
            self._set_status(self._status_base or "")
            self.input.setFocus()

    def _set_busy(self, b):
        """兼容旧调用（_finish 等）：结束当前栏目回合并刷新界面。"""
        if not b:
            self._turns.pop(self._slot_key(), None)
        self._sync_turn_ui()

    def _warn_slow(self):
        if self._turns:
            t = next(iter(self._turns.values()))
            secs = int(time.time() - t["t0"])
            self._append("系统", f"⏳ 还在等待模型响应（已 {secs}s）。首次加载本地模型可能要一会；"
                                 f"等太久可点右侧「⏹ 停止」，或切个更小的模型。")

    def _tick_status(self):
        self._beat = getattr(self, "_beat", 0) + 1
        t = self._slot_turn() or (next(iter(self._turns.values()))
                                  if self._turns else None)
        if t is None:
            return
        secs = int(time.time() - t["t0"])
        tip = "　⏹ 点右侧「停止」可中断" if secs > 8 else ""
        self.status.setText(f"⏳ 思考中… {secs}s（{self._status_base or '…'}）{tip}")

    def _set_status(self, s):
        self._status_base = s
        self.status.setText("🟢 " + s)

    def _ui(self, fn):
        """从任意线程安全地切回 UI 线程执行（Qt 跨线程回调修复，双后端兼容）。"""
        ui_dispatch(fn)

    # ---------- 回合结果落位（主线程） ----------
    def _drain_pend(self):
        """v0.23 并发优化：当前会话空闲时，自动执行它排队中的消息（完成自动接续）。"""
        try:
            while self._pend and not self.busy:
                hit = None
                for i, p in enumerate(self._pend):
                    if p["conv_id"] == self.conv_id:
                        hit = i
                        break
                if hit is None:               # 排队属于别的会话 → 切回去再执行
                    break
                p = self._pend.pop(hit)
                self.input.setText(p["text"])
                self.send(skip_busy=True)
                if self.busy or not self._pend:
                    break
        except Exception:
            pass

    def _turn_done(self, turn: dict, reply: str):
        """回合线程把回复交回来：按"还在不在原会话"决定 正常展示 / 收进原会话 / 丢弃。

        认知收尾（cog.after / 情景归档 / 程序记忆）也在此统一收敛，
        且一律用发起回合时冻结的 cog，避免污染用户中途新开的会话。

        v0.26.1 加固（真机"任务做完但会话只有提问"根因）：
        1) 全程 try 兜底——展示/落盘任何一步异常都不让回复蒸发，
           兜底直接写回原会话文件并记日志；
        2) same 只看 槽位+会话 是否一致，不再看 detached——
           用户中途切走又回到原会话时，完成的回复要当场显示出来
           （旧逻辑一律收进文件、UI 不刷新，看起来像"没干"）。
        """
        try:
            self._turn_done_impl(turn, reply)
        except Exception:
            logging.exception("turn_done failed")
            self._file_away(turn, reply)          # 兜底：无论如何把回复落进原会话
            try:
                self._drain_pend()
            except Exception:
                pass

    def _turn_done_impl(self, turn: dict, reply: str):
        cur = self._turns.get(turn["slot"])
        if cur is turn:
            del self._turns[turn["slot"]]
        self._sync_turn_ui()
        if turn["cancel"].is_set():
            return                                # 已停止：结果丢弃、不做认知收尾
        same = (turn["slot"] == self._slot_key()
                and turn["conv_id"] == self.conv_id
                and self.conv_id is not None)
        try:
            cog = turn["cog"]
            kind = turn.get("kind", "chat")
            if kind == "agent" and turn.get("agent"):
                ak = turn["agent"][0]
                self._note_proc(turn.get("user", ""), ak, reply)
                if ak in ("script", "project", "genppt", "gendoc", "genxls",
                          "weblearn", "skill", "readfile", "readfolder", "runlast"):
                    cog.set_goal(turn.get("user", ""), ak)
                cog.after(turn.get("user", ""), turn.get("score", 0.0))
            else:
                dec = turn.get("dec")
                if dec:
                    cog.last_intent = dec.get("intent", "chitchat")
                cog.after(turn.get("user", ""), turn.get("score", 0.0))
            if same and self.cog is cog:       # 只有确实在同一条线上才做"每 8 轮归档"
                if turn.get("kind") == "agent":
                    self._maybe_episode({"intent": "task_do"})
                else:
                    self._maybe_episode(turn.get("dec"))
        except Exception:
            pass
        if not same:
            self._file_away(turn, reply)
            self._drain_pend()                     # v0.23 排队消息自动接续
            return
        self._finish(reply, turn)                 # 还在原会话：正常展示并落盘
        self._drain_pend()                        # v0.23 同会话排队消息自动接续

    def _file_away(self, turn: dict, reply: str):
        """用户已切走/新开对话：把回复收进它原属的会话文件（不污染当前界面）。"""
        cid = turn.get("conv_id")
        try:
            if not cid:
                self._toast("刚才问的那条已完成，但你已开新对话没有地方放；需要的话重问一次即可～")
                return
            p = self._conv_path(cid + ".json")
            d = _load_json(p, {})
            hist = d.get("history") or []
            hist.append({"role": "assistant", "content": reply})
            d["history"] = hist[-500:]
            d["updated"] = int(time.time())
            _save_json(p, d)
            self._refresh_conv_list()
            head = (turn.get("user") or "")[:16]
            self._toast(f"「{head}…」的回答已完成，收进了左侧那条会话（点它即可查看）。")
        except Exception:
            logging.exception("file_away failed")   # v0.26.1：收尾失败留痕可查

    def _ensure_local_warm(self):
        """v0.29.1：只要当前生效的是本地模型，就保证它已经驻留。

        旧版只在「没填 Key」那一支预热，于是**从云端切到本地、或在设置里换本地模型**
        时预热根本不会跑 —— 首条消息白等约 40s 冷加载。
        真机 2026-09-14 实测：日志里从头到尾没有 `warmup ok`，首句装载 40.2s。
        """
        try:
            ep = self._endpoint()
        except Exception:
            ep = None
        if not ep:
            return
        base_url, model = ep[0], ep[1]
        try:
            if not GW.is_local_url(base_url):
                return
            if GW.local_model_resident(base_url, model):
                return
        except Exception:
            return
        self._warm_local(model)

    def _warm_local(self, model):
        """后台预热：提前把模型加载进内存，首条消息不再等冷加载。"""
        if not model:
            return
        def job():
            try:
                GW.gw.complete(
                    "http://127.0.0.1:11434/v1", model, "local",
                    [{"role": "user", "content": "hi"}],
                    task="warm", max_tokens=1)
                logging.info("warmup ok %s", model)
            except Exception as ex:
                logging.warning("warmup fail %s: %s", model, ex)
        threading.Thread(target=job, daemon=True).start()

    def _inline_image_tag(self, path: str, maxw: int = 220) -> str:
        """把本地图片压成缩略图、以 data URI 内联进聊天（不落临时文件）。

        v0.30.10：小志反馈"上传图片只在聊天里显示系统提示、看不到图"——
        这里让**用户发出的图片直接在气泡里可见**（即使本机没装视觉模型也能看到自己发了什么）。
        返回 '' 表示读不出图（调用方退化为纯文本）。
        """
        try:
            from PySide6.QtGui import QPixmap
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
            pm = QPixmap(path)
            if pm.isNull():
                return ""
            if pm.width() > maxw:
                pm = pm.scaledToWidth(maxw, Qt.SmoothTransformation)
            ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)
            if not pm.save(buf, "PNG"):
                return ""
            b64 = ba.toBase64().data().decode("ascii")
            return ('<img src="data:image/png;base64,%s" width="%d" height="%d" '
                    'style="border-radius:8px;margin-top:2px;max-width:%dpx">'
                    % (b64, pm.width(), pm.height(), maxw))
        except Exception:
            return ""

    def _append(self, who, content):
        self.chat.moveCursor(QTextCursor.End)
        self.chat.append(f"<b>{html.escape(who)}</b>：{content}")
        self.chat.moveCursor(QTextCursor.End)

    # ============ v0.31.2 过程流：把"干活的过程"实时落成卡片 ============
    # 小志原话：「我还想让它能像 workbuddy 一样，不管是聊天，还是干活，均能显示过程」。
    # 改动前的真相：聊天有真流式与真思考（`on_delta` / `on_think`），**重活却全程静默** ——
    # `_project_run` 只调一次 `_brain()`（无流式/思考回调），跑完才出结果；
    # 聊天区除了「⏳ 正在干活」那一句，中间什么都看不到（本地模型下可能是几分钟）。
    # 于是"在干活"和"卡死了"在界面上完全一样。
    #
    # 这里补一条**步骤总线**：各工种把"我刚做了什么"播报过来，按 WorkBuddy 那种
    # 卡片流呈现（运行命令 / 新建 / 修改 +N -M / 已读取 / 规划）。三条纪律：
    #   ① 只报**真实发生**的事（真跑的命令、真写的文件、真读的路径）——
    #      不生成"假思考"来凑视觉热闹；
    #   ② 线程安全：worker 线程可直接调 `_step()`，内部统一派发到 UI 线程；
    #   ③ 同一轮的步骤写进**同一个块**（`_patch_last_block` 原地重画），
    #      不刷成几十条气泡。
    _STEP_STYLE = {
        "think": ("◍", "#5F5E5A"),
        "plan":  ("◇", "#185FA5"),
        "cmd":   ("›_", "#185FA5"),
        "new":   ("＋", "#3B6D11"),
        "edit":  ("✎", "#BA7517"),
        "read":  ("◉", "#534AB7"),
        "ok":    ("✓", "#0F6E56"),
        "err":   ("✕", "#A32D2D"),
    }
    _STEP_HEAD = "过程"
    #: 身份核对用的前缀（比 `_STEP_HEAD` 多一个分隔号，进一步降低误撞别人文本的概率）
    _STEP_MARK = "过程 ·"
    _STEP_MAX = 40                       # 一轮最多留这么多条（旧的自然折叠掉）
    #: **过程块的块标记基址**（写进 `QTextBlock.setUserState`）。
    #: 工程里没有任何 `QSyntaxHighlighter`，userState 无人占用，可安全自用。
    #: 每开一轮 +1 —— 这样"本轮那一块"能被**精确找回**，跨轮绝不重用上一轮的历史卡片。
    _STEP_TAG_BASE = 0x5B0C0000

    def _step_reset(self):
        """开一轮新的过程流（每次开工前调一次）。"""
        self._steps = []
        self._step_closed = False
        # 换一个"轮次标记"：新的一轮会另起一块，上一轮的过程卡片**原样留在历史里**。
        self._step_gen = int(getattr(self, "_step_gen", 0)) + 1
        self._step_tag = self._STEP_TAG_BASE + (self._step_gen & 0xFFFF)

    def _step(self, kind: str, title: str, target: str = "",
              detail: str = "", status: str = "ok", key: str = ""):
        """追加一条过程步骤并立刻上屏。**线程安全**：worker 线程可直接调。

        `key` 非空时表示"这条是可原地刷新的"（例如模型思考增量）：末尾步骤的 key
        相同就**替换**它，不重复追加 —— 否则思考流会把卡片流刷爆。
        """
        try:
            self._ui(lambda: self._step_ui(kind, title, target, detail, status, key))
        except Exception:
            logging.exception("step dispatch failed")     # 播报失败绝不影响干活

    def _step_ui(self, kind, title, target="", detail="", status="ok", key=""):
        if not hasattr(self, "_steps") or self._steps is None:
            self._step_reset()
        item = {"kind": str(kind or "plan"), "title": str(title or ""),
                "target": str(target or ""), "detail": str(detail or ""),
                "status": str(status or "ok"), "key": str(key or "")}
        if key and self._steps and self._steps[-1].get("key") == key:
            self._steps[-1] = item                      # 原地刷新，不追加
        else:
            self._steps.append(item)
        if len(self._steps) > self._STEP_MAX:
            self._steps = self._steps[-self._STEP_MAX:]
        self._steps_render()
        # 同步落进工作台账（v0.31.2：`worklog.set_status` 在本项目里**从未被调用过**，
        # 所以工作台的"进度"一栏一直是空的 —— 顺手补上，同一份事实两处可见）
        try:
            wid = getattr(self, "_cur_wid", "") or ""
            if wid and WORKLOG:
                WORKLOG.set_status(
                    wid, "running",
                    progress="第 %d 步 · %s" % (len(self._steps), self._steps[-1]["title"]))
        except Exception:
            pass

    def _steps_html(self) -> str:
        """整段步骤流的 HTML。

        ⚠️ **必须只产出"单块"HTML**（只用 `<span>` + `<br>`，绝不用 `<div>`/`<p>`）。
        实测（Qt 富文本）：`<div>a</div><div>b</div>` → **2 个块**，
        `<span>a</span><br><span>b</span>` → **1 个块**。
        而 `_patch_last_block` 的"这块是不是我的"判定是**按最后一块的文本前缀**做的 ——
        多块 HTML 会让"最后一块"退化成最后那行（文本是"＋ 新建 x"而不是"过程 ·…"），
        身份核对失败 → 每刷新一次就**另起一块**，实测 5 步把聊天文档从 1 块刷到 20 块，
        老内容全部残留，越滚越长。收敛成单块后，整段过程流永远只有一块。
        （第二层保险见 `_step_block()`：块身份用**轮次标记**认，而不是"是不是最后一块"。）
        """
        out = ["<span style='color:#64748b;font-size:12px;'>%s · %d 步</span>"
               % (self._STEP_HEAD, len(self._steps))]
        for st in self._steps:
            icon, color = self._STEP_STYLE.get(st["kind"], ("·", "#5F5E5A"))
            col = color
            if st["status"] == "err":
                col = "#A32D2D"
            elif st["status"] == "run":
                col = "#B45309"
            line = ("<br><span style='color:%s;'>%s</span>"
                    "<span style='color:#1e293b;'> %s</span>"
                    % (col, html.escape(icon), html.escape(st["title"])))
            if st["target"]:
                line += ("<span style='font-family:monospace;font-size:12px;"
                         "color:#334155;'> %s</span>"
                         % html.escape(st["target"][:160]))
            if st["detail"]:
                _ind = "&nbsp;" * 5
                line += ("<br>%s<span style='font-family:monospace;font-size:12px;"
                         "color:#94a3b8;'>%s</span>"
                         % (_ind, html.escape(st["detail"][:400]).replace("\n", "<br>" + _ind)))
            out.append(line)
        return "".join(out)

    def _step_block(self):
        """找回**本轮**那一块过程块；没有就返回 None。

        ⚠️ 为什么不能再用"最后一块"定位（v0.31.2 实测的第二个坑）：
        `_patch_last_block` 是"末尾块不是我的一律另起一块"。可干活期间**别人也会往
        末尾追加** —— 实测一次真实开工里就有：`_append("系统","⏳ 正在干活")`、
        `_replay_history()` 整段重放、`_turn_live_start()` 先 `append("")` 再填名牌。
        每一次都会把"我那一块"挤到上面去；下一次 `_steps_render` 便**另起一块**，
        于是老卡片（步数少）留在原地、新卡片接着长大 → 文档里出现 2 个「过程 ·」
        开头的块（e2e 实测就是这么来的）。
        改用**块标记**认领：只要块还在，永远原地重画；块被整段重放冲掉，才会新建一块。
        """
        tag = getattr(self, "_step_tag", None)
        if tag is None:
            return None
        try:
            b = self.chat.document().lastBlock()
            while b.isValid():
                if int(b.userState()) == int(tag):
                    return b
                b = b.previous()
        except Exception:
            logging.exception("step block lookup failed")
        return None

    def _steps_render(self):
        """把当前步骤流原地重画进"过程块"（不新增气泡、不残留旧内容、跨轮不串卡）。"""
        try:
            html = self._steps_html()
            blk = self._step_block()
            if blk is not None and (blk.text() or "").lstrip().startswith(self._STEP_MARK):
                # 认领成功 → 在这一块里原地重画（位置不动，始终是同一条卡片）
                c = QTextCursor(blk)
                c.movePosition(QTextCursor.StartOfBlock)
                c.movePosition(QTextCursor.EndOfBlock, QTextCursor.KeepAnchor)
                c.removeSelectedText()
                c.insertHtml(html)
                c.block().setUserState(self._step_tag)
            else:
                # 真找不到了（首次落位 / 文档被整段重放冲掉）→ 末尾另起一块并**做上标记**
                c = self.chat.textCursor()
                c.movePosition(QTextCursor.End)
                if (c.block().text() or "").strip():
                    c.insertBlock()
                c.insertHtml(html)
                c.block().setUserState(self._step_tag)
            self.chat.verticalScrollBar().setValue(
                self.chat.verticalScrollBar().maximum())
        except Exception:
            logging.exception("steps render failed")

    def _diff_stat(self, old: str, new: str):
        """旧文 → 新文 的 (+新增 / -删除) 行数（按最长公共子序列口径，粗略但诚实）。"""
        try:
            import difflib
            a = (old or "").splitlines()
            b = (new or "").splitlines()
            add = dele = 0
            for ln in difflib.unified_diff(a, b, n=0, lineterm=""):
                if ln.startswith("+") and not ln.startswith("+++"):
                    add += 1
                elif ln.startswith("-") and not ln.startswith("---"):
                    dele += 1
            return add, dele
        except Exception:
            return (len((new or "").splitlines()), 0)

    def _step_files(self, files: dict, base: str = "", backup: str = ""):
        """把一批落盘文件播报成"新建/修改 +N -M"卡片。

        `backup` 非空 = 原地更新模式（归档目录里**有**同名旧文件才算"修改"，
        否则是新加的文件）；`backup` 为空 = 全新项目 → 一律"新建"。
        """
        for rel, content in list((files or {}).items())[:24]:
            try:
                full = os.path.join(base, *str(rel).lstrip("/ ").split("/")) if base else ""
                old, is_edit = "", False
                if backup and full and os.path.isfile(full):
                    bp = os.path.join(backup, *str(rel).lstrip("/ ").split("/"))
                    if os.path.isfile(bp):
                        old = open(bp, encoding="utf-8", errors="ignore").read()
                        is_edit = True
                new = str(content or "")
                if is_edit:
                    add, dele = self._diff_stat(old, new)
                    self._step("edit", "修改", str(rel),
                               "+%d / -%d 行" % (add, dele))
                else:
                    self._step("new", "新建", str(rel),
                               "+%d 行" % len(new.splitlines()))
            except Exception:
                continue

    def _step_run(self, label: str, cmd: str, out: str, ok: bool = True):
        """播报"运行命令 + 输出尾"。`cmd` 是真实执行的东西，`out` 是真实输出。"""
        tail = (out or "").strip().splitlines()
        tail = "\n".join(tail[-6:])[:400] if tail else "（无输出）"
        self._step("cmd", label, cmd, tail, "ok" if ok else "err")

    def _render_mind(self, snap):
        e, p, d = snap["emotion"], snap["personality"], snap["development"]
        # 成长阶段统一以 pet_state.json 经验为准（与桌面小人完全一致）
        stage = GROWTH.stage_name()
        self.avatar.set_growth(GROWTH.current_growth())
        self.avatar.set_gender(GROWTH.pet_state().get("gender", "none"))
        arch = ARCH_META.get(self.cfg.get("persona", "温和沉稳"), {})
        emo = "心情好😊" if e["valence"] > 0.2 else "平静" if e["valence"] > -0.2 else "低落😔"
        self.avatar.set_state(e["valence"], e["arousal"], serotonin=e["serotonin"])
        # 头部今日心情 + 状态
        day_mood = "开心" if e["valence"] > 0.2 else ("平和" if e["valence"] > -0.2 else "有点低落")
        state = "发呆" if not self.busy else "思考中…"
        self.mood_lbl.setText(f"今日心情：{day_mood} · 此刻：{state}")
        pname = self.cfg.get("persona", "温和沉稳")
        # v0.30.9：内心 = **单行**（QLabel，无滚动条）；完整细节放 tooltip。
        _exp = GROWTH.pet_state().get("exp", 0)
        self.mind.setText(
            f"💭 此刻 <b>{emo}</b> ｜ 愉悦 {e['valence']:+.2f} ｜ {stage}（经验 {_exp}）"
            f" ｜ 关于你 {len(self.notes)} 件 · 情景记忆 {snap['memory']['episodic']}")
        self.mind.setToolTip(
            f"此刻情绪：{emo}\n"
            f"愉悦 {e['valence']:+.2f} · 平静度 {e['serotonin']:+.3f}\n"
            f"性格档案：{pname}\n{arch.get('label', '')}\n"
            f"开放 {p['openness']:+.2f} · 谨慎 {p['caution']:+.2f} · "
            f"亲社交 {p['sociability']:+.2f}\n"
            f"阶段：{stage}（经验 {_exp}）\n"
            f"关于你：{len(self.notes)} 件 · 情景记忆 {snap['memory']['episodic']}")

    def _react_affect(self, text: str):
        """拟人情绪反应（P3）：根据用户这句话，触发头像/桌面小人的表情与动作事件。

        只做轻量判定，不打断正常对话；情绪事件写入 self.pet_event 供桌面小人拾取。
        """
        t = (text or "").strip()
        if not t:
            return
        now = time.time()
        is_quiet_ask = bool(re.search(
            r"(?:别(?:打扰|烦|吵)(?:我)?|别来打扰|去(?:旁边|边上|一边|角落里)|"
            r"自己(?:玩|待|看书)(?:会|着|一下)?|别(?:管|理)我|别闹我|去睡吧|"
            r"你安静点|别出声|别说话)", t))
        is_call = bool(re.search(r"(?:出来|过来|回来|别躲|在吗|找我|叫(?:它|你|咱|小)|"
                                 r"喊|别藏了|露个面)", t))
        if not is_quiet_ask and self.pet_event[0] == "quiet" and is_call:
            self.pet_event = ("call", now)
            self.avatar.set_expr("joy", 2.0)
            self.avatar.set_act("hop", 2.0)
            return
        if is_quiet_ask:
            self.pet_event = ("quiet", now)
            self.avatar.set_expr("calm")
            return
        apology = any(w in t for w in
                      ("对不起", "抱歉", "错怪", "冤枉", "骂错", "我错了",
                       "不是你的错", "不该骂", "别生气", "原谅我"))
        if apology and self._hurt_until > now:
            # 先被凶（委屈）→ 又发现骂错了来哄 → 会短暂"闹别扭"地生气
            self._hurt_until = 0.0
            self.pet_event = ("pardon", now)
            self.avatar.set_expr("angry", 3.0)
            return
        scold = bool(re.search(
            r"(?:笨蛋|真笨|笨死|笨|蠢|白痴|傻瓜|废物|没用|讨厌你|滚|闭嘴|"
            r"滚开|走开|去死|气死我|恨死你|你不行|你不行啦|"
            r"你(?:怎么|总是|又).{0,8}(?:这样|不行|错|笨|差))", t))
        if scold:
            self._hurt_until = now + 480        # 记住这次委屈：之后若被哄 → 闹别扭
            self.pet_event = ("scold", now)
            self.avatar.set_expr("aggrieved", 4.0)
            return
        praise = any(w in t for w in
                     ("夸", "棒", "厉害", "真好", "乖", "聪明", "爱你", "喜欢",
                      "好棒", "优秀", "赞", "贴心", "真行", "靠谱", "辛苦了",
                      "谢谢你", "可爱", "聪明绝顶", "太好啦"))
        if praise:
            self.pet_event = ("praise", now)
            self.avatar.set_expr("joy", 3.2)
            arch = ARCH_META.get(self.cfg.get("persona", "温和沉稳"), {})
            if arch.get("play", 0.0) >= 0.6:
                self.avatar.set_act("dance", 4.5)     # 活泼/调皮型被夸 → 跳舞
            else:
                self.avatar.set_act("hop", 2.5)       # 沉稳/内向型被夸 → 轻轻蹦两下

    def _on_growth_changed(self):
        """桌面小人成长变化时同步过来（由 PetShell 调用）。"""
        try:
            st = GROWTH.pet_state()
            self.avatar.set_growth(GROWTH.level_of(st.get("exp", 0)))
            self.avatar.set_skin(st.get("skin", "tech"))
            self._render_mind(self.agent.snapshot())
        except Exception:
            pass

    # ---------- 动作 ----------
    def _note_latest(self, latest):
        if latest:
            self._append("系统", f"🆕 新版本 v{latest.get('version')}：{latest.get('download_url', '')}")

    # ---------- 多会话：左侧列表 + 聊天内容全程落盘（永不丢） ----------
    # ---------- v0.17.2：会话-栏目槽位（slot）基础 ----------
    def _gen_restore(self):
        """恢复当前会话的产物记忆（表格/文档/PPT 同文件续改用）。"""
        try:
            d = _load_json(GEN_MEM, {}) or {}
            rec = d.get(self.conv_id) or {}
            self._ses_gen = dict(rec) if isinstance(rec, dict) else {}
        except Exception:
            self._ses_gen = {}

    def _gen_remember(self):
        """把当前会话的产物记忆写回磁盘（切会话/重启不丢）。"""
        if not getattr(self, "conv_id", None) or not self._ses_gen:
            return
        try:
            d = _load_json(GEN_MEM, {}) or {}
            if not isinstance(d, dict):
                d = {}
            d = {k: v for k, v in d.items() if isinstance(v, dict)}
            d[self.conv_id] = dict(self._ses_gen)
            items = sorted(d.items(), key=lambda kv: (kv[1].get("_t") or 0),
                           reverse=True)[:20]
            _save_json(GEN_MEM, dict(items))
        except Exception:
            pass

    def _slot_key(self) -> str:
        """当前对话应归属的栏目标签（= 会话文件 chip 字段 = 列表图标）。"""
        if self.mode == "work" and self._chip:
            return self._chip
        return "chat" if self.mode == "chat" else "work"

    def _load_slots(self) -> dict:
        m = _load_json(SLOTS_FILE, {})
        return m if isinstance(m, dict) else {}

    def _save_slots(self):
        _save_json(SLOTS_FILE, self.slot_map)

    def _slot_conv(self, key: str | None = None):
        """槽位 key 当前指向的会话 id；指向的会话文件已不存在则视为无。"""
        key = key or self._slot_key()
        cid = self.slot_map.get(key)
        if cid and os.path.exists(self._conv_path(cid + ".json")):
            return cid
        return None

    def _remember_slot(self, cid=None, key: str | None = None):
        """把槽位 key 指向会话 cid（cid=None 表示该栏目还没有会话=空对话）。"""
        key = key or self._slot_key()
        if cid:
            self.slot_map[key] = cid
        else:
            self.slot_map.pop(key, None)
        self._save_slots()

    def _load_conv_into(self, cid: str):
        """把指定会话载入当前槽位（不切 chip、不 touch 文件时间戳=不顶置列表）。"""
        d = _load_json(self._conv_path(cid + ".json"), {})
        self.history = list(d.get("history", []))[-500:]
        self.conv_id = cid
        self._ses_img_dir = d.get("img_dir") or None
        self._ses_story_dir = d.get("story_dir") or None
        self._ses_img_prompt = d.get("img_prompt") or None   # 上一张图的精修提示词
        self._ses_proj_dir = d.get("proj_dir") or None
        self._ses_img_req = d.get("img_req") or None
        # v0.18.0：漫剧分步状态恢复——story.json 在成片目录里，切会话/重启接着做
        self._ses_story = self._story_load()
        self.slot_map[self._slot_key()] = cid
        self._save_slots()
        self._gen_restore()               # v0.26.2：切回会话恢复"上次做的那份文件"记忆

    def _conv_path(self, fn: str) -> str:
        return os.path.join(SESSION_DIR, fn)

    def _conv_title(self) -> str:
        """会话标题：剥句首客套 → 按标点断完整分句 → 超长加省略号。

        修复"已载入会话「好的，谢谢你…不过我还要再」"这类半句标题：
        不再机械 [:18] 硬截首句。
        """
        for m in self.history:
            if m.get("role") != "user":
                continue
            t = (m.get("content") or "").replace("\n", " ").strip()
            if not t:
                continue
            # 剥句首纯客套："好的，谢谢你提醒我下班…" → "谢谢你提醒我下班…"
            t2 = re.sub(r"^(?:好的?|好|嗯|恩|行|可以|ok|OK|收到|知道了|了解|"
                        r"谢谢|谢谢你|谢谢您|感谢|多谢|辛苦)[，,。!！~～\s]*", "", t)
            if len(t2) > 18:
                # 优先按标点断在完整分句，避免"我还要再"这种切半
                seg = re.split(r"[，。！？；、!?,]", t2, maxsplit=1)[0]
                if 4 < len(seg) < len(t2):
                    t2 = seg
            if len(t2) > 18:
                t2 = t2[:18] + "…"
            return t2 or "对话"
        return "对话"

    def _persist_conv(self, refresh=True):
        """把当前会话全量落盘成会话文件（每轮对话都会调用，聊天永不丢）。"""
        if not self.history:
            return None
        if not self.conv_id:
            self.conv_id = time.strftime("%Y%m%d_%H%M%S")
        fn = self.conv_id + ".json"
        meta = {"ts": time.strftime("%m-%d %H:%M"), "title": self._conv_title(),
                "history": self.history, "updated": int(time.time()), "conv": True}
        # 该会话属于哪个栏目 → 之后点开会话自动切回对应栏目（槽位键=chip 字段=列表图标）
        meta["chip"] = self._slot_key()
        _save_json(self._conv_path(fn), meta)
        self._remember_slot(self.conv_id)        # 该栏目线程指向本会话
        _save_json(HISTORY, self.history[-24:])    # 兼容镜像（真实来源已是会话文件）
        if refresh and hasattr(self, "conv_list"):
            self._refresh_conv_list()
        return fn

    def _conv_items(self):
        """全部会话 (fn, ts, title, updated, chip)，最近更新在前（跳过 _ 开头的元文件）。"""
        out = []
        for fn in os.listdir(SESSION_DIR):
            if not fn.endswith(".json") or fn.startswith("_"):
                continue
            p = self._conv_path(fn)
            try:
                d = _load_json(p, {})
                updated = int(d.get("updated", 0) or 0)
                if not updated:
                    updated = int(os.path.getmtime(p))
                out.append((fn, d.get("ts", ""), d.get("title", "") or "对话",
                            updated, d.get("chip", "")))
            except Exception:
                continue
        out.sort(key=lambda x: -x[3])
        return out

    _CHIP_ICON = {"chat": "💬", "copy": "✍️", "xls": "📊", "ppt": "📽",
                  "doc": "📄", "project": "🖥", "video": "🎬",
                  "image": "🎨", "manga": "📖", "work": "🛠"}

    def _chip_icon_of(self, chip):
        if chip in self._CHIP_ICON:
            return self._CHIP_ICON[chip]
        return ""

    # ---- v0.17.2 图像"在上一张基础上改"的意图判定（v0.20.0 词表扩充） ----
    _IMG_EDIT_RE = re.compile(
        r"优化|调整|修改|改(成|一|一下|下|好)?|换成?|换为|弄成|柔和|柔光|提亮|调亮|变亮|增亮|"
        r"再亮|亮一?[点些]|再暗|暗一?[点些]|暖一?[点些]|冷一?[点些]|去雾|增加|添加|加上|添上|"
        r"加(个|只|条|位|点|些|几|两|三|上)|去掉|删除|移除|抹掉|擦掉|更(加|亮|暗|柔|好|真实|"
        r"精细|现代)?|氛围|背景|天空|光线|光影|光泽|细节|构图|主体|保留|保持|清晰|模糊|虚化|"
        r"磨皮|上色|配色|色调|色温|饱和|对比|滤镜|风格|重绘|润色|微调|上一(张|幅)|刚才|它",
        re.I)
    _IMG_EDIT_STRONG_RE = re.compile(r"改成|换成|换为|弄成|去掉|删除|移除|抹掉|擦掉", re.I)
    _IMG_NEW_RE = re.compile(r"一张|一幅|新的一?[张个]|重新画一?[张个]|全新|换个?主题", re.I)
    _IMG_REF_RE = re.compile(
        r"^(把|将|基于|在|请|帮|能不能|可不可以)?(上?一?张|刚才|它|这(张|幅|个图)|"
        r"那张|原图|当前|现在的?图)", re.I)
    # v0.18.0：轻改词表——命中走低重绘幅度（0.35），构图/主体基本保留
    _IMG_LIGHT_RE = re.compile(
        r"亮度|对比|色调|色彩|饱和|色温|柔光|柔化|锐化|磨皮|调亮|提亮|调暗|压暗|"
        r"变亮|变暗|去雾|清晰|虚化|景深|滤镜|稍微|微微|轻[微轻一点]*|淡一?[点些]|"
        r"深一?[点些]|微调|略|一点|一些|润色|降噪|质感", re.I)

    def _img_edit_strength(self, req: str) -> float:
        """编辑重绘幅度：轻改 0.35（保构图只调画面属性），大改 0.62。"""
        return 0.35 if self._IMG_LIGHT_RE.search(req or "") else 0.62

    def _wants_image_edit(self, req: str) -> bool:
        """判断图像需求是在"上一张图基础上改"（柔光/加小动物/换风格…）。

        v0.20.0 判定顺序：引用上一张 → 编辑；强编辑动词（改成/换成/去掉…）→
        即使句中带"一张"也是编辑（"画一张海报，把标题改成…"）；带"一张/一幅"
        等新主题量词 → 新图；以画/生成开头且无修改语义 → 新图；其余看编辑词表。
        """
        s = (req or "").strip()
        if not s:
            return False
        if self._IMG_REF_RE.search(s):
            return True
        # 强编辑动词压过"一张"量词（"画一张海报，把标题改成周末特惠"= 编辑）
        if self._IMG_EDIT_STRONG_RE.search(s):
            return True
        # 以"画/生成/做…"等开头的完整新任务 → 不是编辑（除非句子里也引用上一张）
        if re.match(r"^\s*(帮|请|给我)?(再|重新)?(画|生成|出图|做|制作|设计|来)",
                    s, re.I):
            return not self._IMG_NEW_RE.search(s) and \
                bool(self._IMG_EDIT_RE.search(s))
        return bool(self._IMG_EDIT_RE.search(s))

    def _load_latest_conv(self):
        """启动：回到上次所在栏目，并载入该栏目自己的会话线程。

        槽位隔离后每条会话归属一个栏目。启动顺序：
        1) 当前栏目槽位有映射会话 → 载入它；
        2) 否则找 chip==当前栏目的最近一条会话载入（启动后看到的是自己栏目的记录）；
        3) 都没有 → 空对话（旧版全局 chat_last.json 若比所有会话新，迁成一条会话）。
        """
        key = self._slot_key()
        items = self._conv_items()
        # 旧版本升级场景：HISTORY 明显比最近会话新 → 迁移为一条会话，避免丢最后一段
        try:
            hist_new = bool(_load_json(HISTORY, [])) and \
                (not items or os.path.getmtime(HISTORY) > items[0][3] + 2)
        except Exception:
            hist_new = False
        if hist_new:
            self.history = _load_json(HISTORY, [])[-500:]
            self.conv_id = None
            self._persist_conv(refresh=False)
            self._ses_img_dir = self._ses_story_dir = None
            self._ses_story = None
            self._ses_img_prompt = self._ses_img_req = None
            self._ses_proj_dir = None
            return
        # 1) 当前栏目槽位映射
        cid = self._slot_conv(key)
        if cid:
            self._load_conv_into(cid)
            return
        # 2) 该栏目最近一条
        for fn, _ts, _t, _up, chip in items:
            if chip == key:
                self._load_conv_into(fn[:-5] if fn.endswith(".json") else fn)
                return
        # 3) 空对话（仍有旧全局历史就迁成当前栏目的一条会话）
        self.history = _load_json(HISTORY, [])[-500:]
        self._ses_img_dir = self._ses_story_dir = None
        self._ses_story = None
        self._ses_img_prompt = self._ses_img_req = None
        self._ses_proj_dir = None
        if self.history:
            self.conv_id = None
            self._persist_conv(refresh=False)

    def _refresh_conv_list(self):
        if not hasattr(self, "conv_list"):
            return
        self.conv_list.blockSignals(True)
        self.conv_list.clear()
        cur = (self.conv_id + ".json") if self.conv_id else None
        idx_cur = -1
        for i, (fn, ts, title, _up, chip) in enumerate(self._conv_items()):
            icon = self._chip_icon_of(chip)
            disp = ((icon + " " + title) if icon else title).replace("\n", " ")[:32]
            it = QListWidgetItem(f"[{ts[-5:]}] {disp}")
            it.setData(0x0100, fn)
            tip = (self._CHIP_LABEL.get(chip, "对话") if chip in self._CHIP_LABEL
                   else "会话")
            it.setToolTip(f"{ts}  {title}\n（{tip} · {fn}）")
            self.conv_list.addItem(it)
            if cur and fn == cur:
                idx_cur = i
        if idx_cur >= 0:
            self.conv_list.setCurrentRow(idx_cur)
        self.conv_list.blockSignals(False)

    def _conv_open_item(self, item):
        fn = item.data(0x0100)
        if fn:
            self._open_conv(fn)

    def _conv_menu(self, pos):
        it = self.conv_list.itemAt(pos)
        if not it:
            return
        fn = it.data(0x0100)
        menu = QMenu(self)
        act_open = menu.addAction("打开这个会话")
        act_del = menu.addAction("🗑 删除这个会话")
        chosen = menu.exec(self.conv_list.mapToGlobal(pos))
        if chosen is act_open:
            self._open_conv(fn)
        elif chosen is act_del:
            self._del_conv(fn)

    def _open_conv(self, fn: str):
        # v0.17.5：思考中也允许点开别的会话——当前栏回合"让位"，回复收进它原属会话
        # v0.27.3：切到别的会话 → 先掐断正在读的语音（真机痛点：换了对话还在念）
        try:
            import tts as _tts_m
            _tts_m.stop_speaking()
            if hasattr(self, "pause_btn"):
                self.pause_btn.setText("⏸")
        except Exception:
            pass
        self._detach_turn()
        # 无论现在停在任务/工作台等哪个功能页，点会话都回到对话页展示
        if hasattr(self, "stack") and self.stack.currentIndex() != 0:
            self._switch_page("chat")
        d = _load_json(self._conv_path(fn), {})
        if not d.get("history"):
            self._append("系统", "这个会话是空的。")
            return
        cid = fn[:-5] if fn.endswith(".json") else fn
        # 该会话属于别的栏目 → 先切到那个栏目（换对话信息框），保证"点图像会话就
        # 看到图像栏目自己的记录"。槽位已切回时不会重复触发。
        chip = (d.get("chip") or "").strip()
        cur_key = self._slot_key()
        if chip in dict(self.WORK_CHIPS) or chip == "work":
            if chip != cur_key:
                self._set_chip(chip, quiet=True)
        elif chip == "chat" and cur_key != "chat":
            self._set_chip("chat", quiet=True)
        self._load_conv_into(cid)                # 载入目标会话（不动文件时间戳，不顶置）
        self.cog = COG.CogState(persona=self.cfg.get("persona", "温和沉稳"))
        self._episode_turn = 0
        self._replay_history()
        # v0.18.0：不再往聊天框里塞"已载入会话…"系统行——列表高亮已足够
        self._render_mind(self.agent.snapshot())
        self._refresh_conv_list()
        self._drain_pend()                  # v0.23：切回来且空闲时自动接着跑排队消息

    def _stamp_conv_meta(self, **fields):
        """轻量更新当前会话文件的扩展字段（img_dir/story_dir），不动 history。"""
        if not self.conv_id or not fields:
            return
        try:
            p = self._conv_path(self.conv_id + ".json")
            d = _load_json(p, {})
            changed = False
            for k, v in fields.items():
                if d.get(k) != v:
                    d[k], changed = v, True
            if changed:
                d["updated"] = int(time.time())
                _save_json(p, d)
        except Exception:
            pass

    def _del_conv(self, fn: str):
        if not fn:
            return
        d = _load_json(self._conv_path(fn), {})
        title = (d.get("title") or "这个对话").replace("\n", " ")[:26]
        ret = QMessageBox.question(
            self, "删除对话",
            f"确定删除左侧记录「{title}」吗？\n"
            "只删除列表里的这条对话记录，已生成的文件不会受影响。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            os.remove(self._conv_path(fn))
        except Exception:
            pass
        if self.conv_id and fn == self.conv_id + ".json":
            self._start_fresh("该对话已删除，当前栏目开了一个新的空对话。")
        else:
            self._refresh_conv_list()

    def _new_session(self):
        """开新对话：只重置当前栏目线程（该栏目旧的对话已全程落盘，留在左侧列表）。

        v0.17.5：思考中也允许新建——正在想的回合"让位"，答完自动收进它原属的旧会话。
        """
        self._detach_turn()
        try:
            self._auto_episode()                 # 归档旧会话（情景记忆）
        except Exception:
            pass
        self._start_fresh(
            "已开启新对话（它仍然记得关于你的事；旧对话都在左侧列表，随时可点回）。")

    def _start_fresh(self, msg: str = ""):
        """清空当前栏目的对话线程：该栏目回到"空的新对话"，左侧列表不出现它；
        旧会话文件的去留由调用方决定（/clear 删文件，开新对话保留在列表）。"""
        # v0.27.3：换对话 / 开新对话 / 清空重聊 —— 正在读的一律掐断。
        #   （真机痛点：切到另一个对话，上一个对话的语音还在继续念）
        try:
            import tts as _tts_m
            _tts_m.stop_speaking()
            if hasattr(self, "pause_btn"):
                self.pause_btn.setText("⏸")
        except Exception:
            pass
        self._remember_slot(None)                # 当前栏目槽位清空
        self.history = []
        self.conv_id = None
        self._ses_gen = {}                       # v0.26.2：新对话清空产物记忆
        self._ses_img_dir = self._ses_story_dir = None
        self._ses_story = None
        self._ses_img_prompt = self._ses_img_req = None
        self._ses_proj_dir = None
        self.cog = COG.CogState(persona=self.cfg.get("persona", "温和沉稳"))
        self._episode_turn = 0
        _save_json(HISTORY, [])
        self.chat.clear()
        self._refresh_conv_list()
        # 新对话/清空 → 一律回到对话页（别停在任务/工作台还以为是那边没反应）
        if hasattr(self, "stack") and self.stack.currentIndex() != 0:
            self._switch_page("chat")
        if msg:
            self._append("系统", msg)
        self._render_mind(self.agent.snapshot())
        if hasattr(self, "input"):
            self.input.setFocus()

    def _ctx_msgs(self, hist=None):
        """送模型的上下文：取最近 ≤40 条且累计 ≤8000 字（全量仍在会话文件里）。

        v0.27.12：上限由 1.2 万字收到 8000 字 —— 本地模型预填充约 580 tok/s，
        1.2 万字≈8000 token≈14 秒白等；8000 字已在 16k 上下文里留足余量，
        又不会让每轮回复的"思考前空窗"被历史撑长。

        hist：回合线程传入冻结快照后只读它，用户中途开新对话不干扰本回合上下文。
        """
        src = self.history if hist is None else hist
        # v0.30.7：历史预算同样按**本机实测速度**定 —— 慢机上"上一轮说过什么"
        # 要花十几秒去读，不如只带最近一两句。快机（本机 580 tok/s）仍是 8000 字，
        # 与旧版完全一致。
        _cap = 8000
        try:
            _ep = self._endpoint() or ("", "", "")
            _cap = GW.budget(_ep[1] or "", _ep[0] or "",
                             want_first_secs=12.0, what="hist")
        except Exception:
            pass
        keep, total = [], 0
        for m in reversed(src):
            if len(keep) >= 40:
                break
            s = len(m.get("content") or "")
            if keep and total + s > _cap:
                break
            if not keep and s > _cap:
                # 最新一条自己就超预算：**截断但保留**，不要整条丢掉 ——
                # 一句都没有的历史，模型会彻底接不上刚才在说什么。
                # 截断的只是"记忆线索"，不是指令，所以截断比丢弃安全。
                m = dict(m)
                m["content"] = (m.get("content") or "")[:_cap].rstrip() + "…"
                s = len(m["content"])
            keep.append(m)
            total += s
        return list(reversed(keep))

    def _replay_history(self):
        """把 history 原文回显到聊天框（启动恢复/历史载入共用）。"""
        self.chat.clear()
        for m in self.history:
            if m.get("role") == "user":
                self._append("你", html.escape(m["content"]))
            else:
                self._append(self.cfg["name"], md_to_html(m["content"]))

    def _pref_dialog(self):
        """数据面板：录入关于用户的偏好/日程/事实，写进记忆并参与对话。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("数据面板 · 录入关于你的事")
        dlg.resize(430, 380)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("我会记住这些，并在合适时自然用上："))
        self.pref_list = QListWidget()
        for p in self.prefs:
            it = QListWidgetItem(f"[{p.get('cat','')}] {p.get('tag','')}  （双击删除）")
            it.setData(0x0100, p)
            self.pref_list.addItem(it)
        self.pref_list.itemDoubleClicked.connect(self._pref_del)
        lay.addWidget(self.pref_list)
        h = QHBoxLayout()
        self.pref_cat = QComboBox()
        self.pref_cat.addItems(["偏好", "日程", "事实", "任务"])
        h.addWidget(self.pref_cat)
        self.pref_edit = QLineEdit()
        self.pref_edit.setPlaceholderText("例：我每周三晚上不加班 / 我过敏花生")
        h.addWidget(self.pref_edit, 1)
        add = QPushButton("录入")
        add.clicked.connect(self._pref_add)
        h.addWidget(add)
        lay.addLayout(h)
        dlg.exec()

    def _pref_add(self):
        txt = self.pref_edit.text().strip()
        if not txt:
            return
        self.prefs.append({"t": time.strftime("%m-%d %H:%M"),
                           "cat": self.pref_cat.currentText(), "tag": txt})
        self.prefs = self.prefs[-200:]
        _save_json(PREFS, self.prefs)
        self.pref_list.addItem(QListWidgetItem(f"[{self.prefs[-1]['cat']}] {txt}  （双击删除）"))
        self.pref_edit.clear()
        self.chat.append("<span style='color:#059669'>✅ 已记下。</span>")

    def _pref_del(self, item):
        p = item.data(0x0100)
        if p in self.prefs:
            self.prefs.remove(p)
            _save_json(PREFS, self.prefs)
            self.pref_list.takeItem(self.pref_list.row(item))

    def _session_dialog(self):
        dlg = QDialog(self)
        dlg.setWindowTitle("历史会话")
        dlg.resize(430, 360)
        lay = QVBoxLayout(dlg)
        self.sess_list = QListWidget()
        self._refresh_sessions()
        lay.addWidget(self.sess_list)
        btns = QHBoxLayout()
        for t, cb in [("载入", self._sess_load), ("删除", self._sess_del),
                      ("关闭", dlg.accept)]:
            b = QPushButton(t)
            b.clicked.connect(cb)
            btns.addWidget(b)
        lay.addLayout(btns)
        dlg.exec()

    def _refresh_sessions(self):
        """历史会话弹窗列表（与左侧会话栏同一数据源）。"""
        self.sess_list.clear()
        for fn, ts, title, _up, chip in self._conv_items():
            icon = self._chip_icon_of(chip)
            it = QListWidgetItem(f"[{ts}] {icon + ' ' if icon else ''}{title}")
            it.setData(0x0100, fn)
            self.sess_list.addItem(it)

    def _current_session_file(self):
        it = self.sess_list.currentItem()
        return it.data(0x0100) if it else None

    def _sess_load(self):
        fn = self._current_session_file()
        if fn:
            self._open_conv(fn)

    def _sess_del(self):
        fn = self._current_session_file()
        if fn:
            self._del_conv(fn)
            self._refresh_sessions()

    def _page_skill(self):
        """技能页（左侧顶部「🎓 技能」）：浏览 / 添加 / 导入 / 删除 / 查看规程。"""
        page = QWidget()
        lay = QVBoxLayout(page)
        lay.setContentsMargins(14, 12, 14, 10)
        lay.addWidget(self._page_title("🎓 技能库"))
        note = QLabel("<b>🎯 专家</b> = 岗位型（一整套方法论）　·　<b>🎓 技能</b> = 单项规程（怎么做一件事）　·　"
                      "<b>🔌 连接器</b> = 外部能力（联网/文件/办公文档接口）。<br>"
                      "全部存为标准 SKILL.md 规程文件，聊天干活时自动检索匹配；"
                      "点列表项查看全文，也可在数据目录手动放 .md 文件。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;font-size:12px")
        lay.addWidget(note)
        # v0.25 三层类型筛选：🎯专家 / 🎓技能 / 🔌连接器（参考专家-技能-连接器体系）
        trow = QHBoxLayout()
        trow.addWidget(QLabel("类型："))
        self.sk_type = QComboBox()
        for lab, key in (("全部", "all"), ("🎓 技能", "skill"),
                         ("🎯 专家", "expert"), ("🔌 连接器", "connector")):
            self.sk_type.addItem(lab, key)
        self.sk_type.currentIndexChanged.connect(lambda _i: self._skill_refresh())
        trow.addWidget(self.sk_type)
        trow.addStretch(1)
        lay.addLayout(trow)
        self.sk_list = QListWidget()
        self.sk_list.setStyleSheet(self.NAV_CSS)
        self.sk_list.itemDoubleClicked.connect(self._skill_view)
        self._skill_refresh()
        lay.addWidget(self.sk_list, 1)
        btns = QHBoxLayout()
        for t, cb in [("➕ 添加技能", self._skill_add),
                      ("📥 导入 .md", self._skill_import),
                      ("📖 查看规程", self._skill_view),
                      ("🗑 删除所选", self._skill_del)]:
            b = QPushButton(t)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)
        return page

    def _skill_view(self, *_):
        it = self.sk_list.currentItem()
        if not it:
            self._append("系统", "先在列表里点选一个技能再查看。")
            return
        name = it.data(0x0100)
        s = SKL.load(name)
        if not s:
            return
        from qt_compat import QtWidgets
        raw = SKL.export_text(name) or ""
        meta = SKL.meta_line(name)
        body = s.get("body") or "（无正文）"
        dlg = QDialog(self)
        dlg.setWindowTitle(f"技能《{name}》 · SKILL.md")
        dlg.resize(560, 460)
        lay = QVBoxLayout(dlg)
        head = QLabel(f"<b>{name}</b>　<span style='color:#64748b;font-size:12px'>{meta}</span>")
        lay.addWidget(head)
        desc = QLabel(s.get("description", ""))
        desc.setWordWrap(True)
        desc.setStyleSheet("color:#334155;font-size:13px")
        lay.addWidget(desc)
        tb = QTextBrowser()
        # 保留 frontmatter 一并显示 → 用户能看到标准 SKILL.md 结构，便于照着写自己的
        shown = raw if raw.strip() else (f"# {name}\n\n{body}")
        tb.setPlainText(shown)
        lay.addWidget(tb, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        b_close = QPushButton("关闭")
        b_close.clicked.connect(dlg.accept)
        b_copy = QPushButton("📋 复制原文件到剪贴板")
        b_copy.clicked.connect(lambda: (QtWidgets.QApplication.clipboard().setText(shown),
                                        self._append("系统", f"已复制《{name}》的 SKILL.md 全文。")))
        row.addWidget(b_copy)
        row.addWidget(b_close)
        lay.addLayout(row)
        dlg.exec()

    def _skill_import(self, *_):
        """从外部导入一份标准 SKILL.md 到用户技能库（复制，不改原文件）。"""
        from qt_compat import QtWidgets
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "选择要导入的 SKILL.md", "",
            "Markdown / SKILL.md (*.md *.markdown);;所有文件 (*)")
        if not path:
            return
        ok, msg = SKL.import_skill(path)
        if ok:
            self._skill_refresh()
            self._append("系统", "✅ " + msg)
        else:
            self._append("系统", "导入失败：" + msg)

    def _skill_dialog(self):
        """技能管理器：查看 / 添加 / 删除（用户技能），内置技能只读。"""
        dlg = QDialog(self)
        dlg.setWindowTitle("🎓 技能库 · 它会自动检索并调用")
        dlg.resize(620, 430)
        lay = QVBoxLayout(dlg)
        note = QLabel("技能 = \"怎么做某件事\"的规程。聊天/干活时会**自动检索匹配的技能**来用；"
                      "它会自己上网学，把新本事沉淀成技能。新增技能存进你的数据目录，重启后仍在。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#64748b;font-size:12px")
        lay.addWidget(note)
        self.sk_list = QListWidget()
        self._skill_refresh()
        lay.addWidget(self.sk_list, 1)
        btns = QHBoxLayout()
        for t, cb in [("➕ 添加技能", self._skill_add),
                      ("🗑 删除所选(仅用户技能)", self._skill_del),
                      ("关闭", dlg.accept)]:
            b = QPushButton(t)
            b.clicked.connect(cb)
            btns.addWidget(b)
        lay.addLayout(btns)
        dlg.exec()

    def _skill_refresh(self):
        if not hasattr(self, "sk_list"):
            return
        cur = getattr(self, "sk_type", None)
        want = cur.currentData() if cur else "all"
        self.sk_list.clear()
        for s in SKL.list_skills():
            if want in ("all", None) or s.get("type", "skill") == want:
                src = "📦内置" if s["source"] == "builtin" else "🖊用户"
                stype = {"expert": "🎯专家", "connector": "🔌连接器"}.get(
                    s.get("type", "skill"), "🎓技能")
                desc = (s.get("description") or "").strip()
                item = QListWidgetItem(f"{stype}  {src}  {s['name']}  "
                                       f"[{s.get('category', '通用')}]  —  {desc[:30]}")
                item.setData(0x0100, s["name"])
                item.setToolTip(f"类型：{stype}\n{desc}\n关键词：{'、'.join(s.get('keywords', [])) or '无'}")
                self.sk_list.addItem(item)

    def _skill_add(self):
        """分步录入标准 SKILL.md：名称 → 说明 → 触发词 → 类型 → 归类 → 规程正文。"""
        from qt_compat import QtWidgets
        QInputDialog = QtWidgets.QInputDialog
        name, ok1 = QInputDialog.getText(
            self, "添加技能 · 1/6",
            "技能名称（简短，如 video-script / 周报生成）：\n"
            "存盘文件名取自这里（自动转拼音安全字符）。")
        if not ok1 or not name.strip():
            return
        desc, ok2 = QInputDialog.getText(
            self, "添加技能 · 2/6",
            "一句话说明它能做什么（会参与自动检索，尽量写清「何时用」）：\n"
            "例：生成周报：输入本周完成的事项，输出结构化周报 Markdown",
            text=name.strip())
        if not ok2:
            return
        kws, ok3 = QInputDialog.getText(
            self, "添加技能 · 3/6",
            "触发关键词（逗号分隔，命中后自动调用本技能）：\n"
            "例：周报,周总结,weekly report,周报生成",
            text=name.strip())
        if not ok3:
            return
        stype, ok4 = QInputDialog.getItem(
            self, "添加技能 · 4/6 · 选择类型",
            "三层类型（与 WorkBuddy 的 专家-技能-连接器 一致）：\n\n"
            "🎯 专家 = 岗位型（一整套方法论，常调度下面的技能与连接器完成大任务）\n"
            "🎓 技能 = 单项规程（把「怎么做一件事」写清楚，被检索到就用）\n"
            "🔌 连接器 = 外部能力（联网/本地文件/办公文档等真实接口的接入说明）\n\n"
            "选择：",
            ["🎓 技能（skill）", "🎯 专家（expert）", "🔌 连接器（connector）"],
            0, False)
        if not ok4:
            return
        stype = "skill"
        if stype.startswith("🎯"):
            stype = "expert"
        elif stype.startswith("🔌"):
            stype = "connector"
        cat, ok5 = QInputDialog.getText(
            self, "添加技能 · 5/6",
            "归类（创作 / 办公 / 开发 / 学习 / 效率…）：",
            text="通用")
        if not ok5:
            return
        body, ok6 = QInputDialog.getMultiLineText(
            self, "添加技能 · 6/6 · 规程正文",
            "告诉它具体怎么做（目标 / 何时使用 / 执行步骤 / 输出结构 / 质量红线）。\n"
            "正文里写「调用技能:某技能」可复用另一个技能。\n\n"
            "参考模板：\n" + SKL.SKILL_TEMPLATE[:900],
            f"## 目标\n\n## 何时使用\n\n## 执行步骤\n1. \n2. \n3. \n\n"
            f"## 输出结构\n\n## 质量红线\n- \n")
        if not ok6:
            return
        try:
            path = SKL.add_skill(name.strip(), desc.strip() or name.strip(),
                                 kws.strip(), body.strip(),
                                 category=cat.strip() or "通用",
                                 skill_type=stype)
        except Exception as ex:
            self._append("系统", f"添加技能失败：{ex}")
            return
        self._skill_refresh()
        self._append("系统", f"✅ 已添加{('🎯专家' if stype == 'expert' else '🔌连接器' if stype == 'connector' else '🎓技能')}"
                             f"《{name.strip()}》（{path}）。之后说相关需求，我会自动检索并调用它。")

    def _skill_del(self):
        it = self.sk_list.currentItem()
        if not it:
            return
        name = it.data(0x0100)
        s = SKL.load(name)
        if s and s["source"] != "user":
            self._append("系统", "内置技能不能删除；可以复制它的做法，另存为用户技能后再编辑。")
            return
        if SKL.remove_skill(name):
            self._append("系统", f"已删除技能《{name}》。")
            self._skill_refresh()

    def _mem_manager(self):
        MemDialog(self.notes, lambda ns: (_save_json(NOTES, ns),
                                          self._render_mind(self.agent.snapshot())), self).exec()

    # ============ 认知皮层 & 分层记忆 & 任务复盘（v0.15） ============
    _FB_POS = re.compile(
        r"谢谢|多谢|感谢|不错|厉害|真棒|太棒|很好|好用|牛|满意|说得对|有帮助|喜欢你|就是这样")
    _FB_NEG = re.compile(
        r"不对|错了|胡说|瞎说|重做|不行|太差|听不懂|乱来|失望|没用|答非所问|别乱")

    def _user_feedback_signal(self, text: str):
        """v0.19 学习机制：用户反馈 → 性格在线调参 + SFT 样本沉淀。"""
        sig = 0.0
        if self._FB_NEG.search(text):
            sig = -0.8
        elif self._FB_POS.search(text):
            sig = +0.8
        if not sig:
            return
        pf = getattr(self.agent, "personality_feedback", None)
        if callable(pf):
            try:
                pf(sig)
            except Exception:
                pass
        if sig > 0 and SFT is not None:
            last_a = next((m.get("content", "") for m in reversed(self.history)
                           if m.get("role") == "assistant"), "")
            last_u = next((m.get("content", "") for m in reversed(self.history)
                           if m.get("role") == "user"), "")
            if last_a and last_u:
                SFT.collect(last_u, last_a, quality=1.0, kind="chat_feedback")
                SFT.trim()

    def _note_proc(self, user_text: str, kind: str, reply: str):
        """程序记忆：干完一件事记一笔（目标→手段→成没成→教训）。"""
        ok = bool(reply) and not reply.startswith(
            ("（", "!", "没能", "我试着", "这次没", "我还没", "生成文件失败",
             "没找到", "我找不到"))
        lesson = ""
        if kind in ("script", "project", "runlast") and not ok:
            lesson = "没能一次跑通，应主动带报错信息自动修复重试"
        ML.proc_push(user_text, kind, ok, lesson)
        # v0.19：成功的任务问答沉淀为 SFT 样本（微调语料，质量 0.8）
        if ok and SFT is not None and reply:
            try:
                SFT.collect(user_text, reply, quality=0.8, kind=str(kind)[:16])
                SFT.trim()
            except Exception:
                pass

    def _auto_episode(self, dec: dict | None = None):
        """把最近这段对话归档成一条情景记忆（规则摘要，零 LLM 成本）。"""
        if not self.history:
            return
        try:
            cat = {"task_do": "任务", "followup": "任务", "question": "问答",
                   "vent": "倾诉", "remember": "记住", "chitchat": "闲聊",
                   "wrapup": "闲聊"}.get((dec or {}).get("intent", ""), "日常")
            us = [m["content"] for m in self.history if m.get("role") == "user"]
            if not us:
                return
            title = us[-1][:26]
            tail = " / ".join(u.replace("\n", " ")[:34] for u in us[-3:])
            tags = list(self.cog.topics)[:4] if self.cog.topics else \
                COG.extract_topics(us[-1], 3)
            tags = [t for t in tags if t][:4] or [us[-1][:8]]
            brief = f"用户说：{tail[:120]}"
            if self.cog.open_goal:
                brief += f"；正在推进：{self.cog.open_goal[:60]}"
            # v0.29.0（P0.3）：把重要度真正传下去——此前桌面两处调用一字未传，
            # 结果"玩家救了我"和"今天吃了面包"同权，容量触顶时里程碑先被挤掉。
            #   记住=5（用户明确要求记住，终身难忘）/ 任务·倾诉=3 / 问答=2 / 闲聊=1
            sal = {"记住": 5, "任务": 3, "倾诉": 3, "问答": 2}.get(cat, 1)
            if getattr(self.cog, "open_goal", ""):
                sal = max(sal, 3)          # 正在推进的目标相关，别被日常挤掉
            ML.episode_push(title, brief, tags, cat, salience=sal)
        except Exception:
            pass

    def _maybe_episode(self, dec: dict | None = None):
        """每满 8 轮自动归档一次，避免长会话"聊完就忘"。"""
        try:
            self._episode_turn += 1
            if self._episode_turn % 8 == 0:
                self._auto_episode(dec)
        except Exception:
            pass

    def _cog_wrapup(self, text: str, score: float, dec: dict | None):
        """每条回复完成后回写认知状态：话题漂移 / 情绪连续轮 / 意图。"""
        try:
            if dec:
                self.cog.last_intent = dec.get("intent", "chitchat")
            self.cog.after(text, score)
        except Exception:
            pass

    def closeEvent(self, ev):
        """退出前：当前会话落盘 + 归档情景记忆 + 巩固 + 持久化工作记忆（跨重启延续话题）。"""
        try:
            self._persist_conv(refresh=False)
            self._auto_episode()
            ML.consolidate()                     # v0.19：睡前巩固一轮记忆
            st = self.cog.to_dict()
            ML.working_save(topic=st.get("topic", ""),
                            open_goal=st.get("open_goal", ""),
                            open_kind=st.get("open_kind", ""))
            # v0.29.0：召回侧的强度更新是去抖的（P0.5），退出前强制落盘，
            # 否则"这次想起了什么"这层信息会丢。
            ML.flush(force=True)
        except Exception:
            pass
        # v0.22.4：勾选了"随 PASM 管理 ComfyUI"→ 退出时关掉由本应用拉起的 ComfyUI
        if VE is not None and self.cfg.get("comfy_manage"):
            try:
                VE.stop_comfy(self.cfg)
            except Exception:
                pass
        # v0.31.0：**必须在退出前显式销毁效果区的 WebEngine 视图** ——
        # 让它等到解释器退出才销毁，进程会以 0xC0000005（访问冲突）结束
        # （验证套件里表现为"断言全过但 rc=3221225477"，看着像全过、其实没跑完）。
        try:
            if getattr(self, "p_eff", None) is not None:
                _kind, _w = self.p_eff
                if hasattr(_w, "dispose"):
                    _w.dispose()
        except Exception:
            pass
        super().closeEvent(ev)

    def _maybe_comfy_auto(self):
        """v0.22.4：设置勾了「随 PASM 启动 ComfyUI」且选了 ComfyUI 引擎、
        服务没在跑、本机找得到 ComfyUI → 后台拉起（不阻塞界面，失败静默）。"""
        try:
            if VE is None or not self.cfg.get("comfy_manage"):
                return
            if VE.provider_of(self.cfg) != "comfyui":
                return
            if VE.comfy_running(self.cfg):
                return

            def job():
                try:
                    VE.start_comfy(self.cfg, wait=40)
                except Exception:
                    pass
            threading.Thread(target=job, daemon=True).start()
        except Exception:
            pass

    # ================= 工具智能（文件/分析/编程/联网学） =================
    def _rebuke(self, text: str) -> str:
        """用户对它刚才的言行提出质疑/纠错 → 先认错解释 + 撤销指引。

        修复真机问题：用户质问"你怎么写记住啦？"被当成"问它能干什么"，
        结果甩出能力宣传单、完全无视质疑。命中返回回应文案，否则返回空串。
        """
        if not re.search(r"(?:你怎么|你为何|你为啥|你干嘛|你干吗|凭什么|谁让你|"
                         r"你居然|你怎么能|为什么你|你为什么|为何你|凭啥)", text):
            return ""
        # 质问必须跟"它刚才的言行"有关（记住/写成/这么说…），
        # 避免误伤"你怎么看这部电影/这事怎么办"这类正常提问
        if not re.search(r"(?:记住啦|记住|记成|写成|说成|当成|搞成|听成|理解错|"
                         r"乱记|乱说|乱做|擅自动手|自作主张|弄错|搞错|记错|这么写|"
                         r"这么说|这样回|刚才|那条|这事|回答的|回的)", text):
            return ""
        return ("哎呀，是我理解错了🙏 你刚才那是在**道谢/闲聊**，我却当成让我记事了，"
                "不该自作主张写\"记住啦\"。\n\n"
                "如果你确实不想留这条，说「**撤销刚才那条**」我立刻删掉；"
                "以后没听到你说\"帮我记 / 提醒我 …\"，我不会擅自动手。")

    def _cap_question(self, text: str) -> str:
        """判断是否"问能力"而非"下令干活"。命中返回能力类别，否则返回空。

        v0.23.0 初版 → **P1 起本判定已收敛到核心 `ir.capability_kind()`（唯一真相源）**，
        桌面侧只做委托：词表与判定逻辑都只有一处实现，不再各写一份。

        真机血泪（2026-09-14）：旧实现里"能否/能不能/会不会"只要出现在**任意位置**
        就算在问能力，末尾又是无条件兜底 `return "all"` —— 于是「能否先吃点小吃？」
        直接背出一串能力清单，模型整轮没参与。实测负样本误触发 9/13 → 修复后 0/14。
        """
        try:
            return IR.capability_kind(text)
        except Exception:
            return ""                       # 核心不可用 ≠ 抢答；交回模型正常回答

    def _askhelp_reply(self, kind: str) -> str:
        """问能力 → 一句话说清能做什么 + 引导开工（v0.26.3：去掉一长串快捷锚点，保持轻）。"""
        n = self.cfg.get("name", "小U")
        if kind == "project":
            body = (f"可以呀，{n}能帮你**开发软件 / 网站 / 小程序**：前端页面 + 后端服务 + 数据库，"
                    f"生成完还会尝试运行给你看。\n\n"
                    f"不过要开工得先说清楚想做什么（一句话即可），例如：\n"
                    f"　• 帮我开发一个**记账本待办应用**\n"
                    f"　• 帮我开发一个**产品官网**（前端+后端）\n\n报出你的需求即可开工：")
        elif kind == "code":
            body = (f"会呀，{n}能**直接写代码并运行**：Python / JavaScript / HTML / Go / Java 都行，"
                    f"也能帮你做完整项目。说具体点就开工，例如：\n"
                    f"　• 帮我写个 **python 脚本** 批量重命名文件\n"
                    f"　• 帮我**开发一个登录系统**")
        elif kind == "filegen":
            body = (f"能！{n}可以生成 **Word / PPT / Excel** 到你的电脑（默认桌面），"
                    f"还能按你指定的路径保存。说个主题就能做，例如：\n"
                    f"　• 帮我做一份**季度工作总结 PPT**\n"
                    f"　• 帮我写一份**产品方案 Word**\n"
                    f"　• 帮我做个**销售数据 Excel**")
        elif kind == "read":
            body = (f"能！{n}可以读你电脑上的 **txt / md / docx / xlsx / pdf / 代码** 等文件，"
                    f"并帮你总结、分析、找关键信息。直接给路径（如 `D:\\报告\\方案.md`）或或直接把路径发给我即可：\n\n"
                    f"还能**读整个文件夹**（如 `分析一下 D:\\报告 里的文件`），"
                    f"以及**打开电脑上的应用/文件夹**（如 `打开微信`、`打开 D:\\下载`）。")
        elif kind == "web":
            body = (f"能！{n}可以上网自学任何主题（一个主题会从多个信息源搜，"
                    f"不只是单一词条），学完存进知识库，以后聊到会引用。"
                    f"例如：上网学一下 **基金定投**")
        elif kind == "skill":
            body = (f"能！{n}有一个**技能库**，里面放着\"怎么做某事\"的规程，"
                    f"而且会随相处与自学不断新增。现在已经会：{SKL.headline()}\n\n"
                    f"例如：\n"
                    f"　• 帮我写一个**咖啡探店 vlog 的短视频脚本**\n"
                    f"　• 帮我写一段**新品上市的小红书种草文案**\n"
                    f"　• 帮我策划一套**漫剧一条龙**（梗概 → 分镜 → 绘图提示词）\n\n"
                    f"想加新技能点左侧「🎓 技能」页就能添加或导入；它也会上网自学把"
                    f"新本事沉淀成技能。报出你的需求即可开工：")
        else:
            body = (f"{n}能帮你：**读文件、找文件、写文案、做 PPT/Word/Excel、"
                    f"开发软件项目、上网自学、记待办**。")
        return (f"{body}\n\n直接说要做什么就能开工（例如：帮我做一份 xx 的 PPT / "
                f"开发一个记账小工具 / 读一下 D:\\xx 文件）。")

    def _detect_agent(self, text, allow_cap=True):
        import re as _re
        # ★疑问句拦截：问"能不能/会吗/可以吗" = 询问能力，不等于下令干活。
        #   （干活模式下 allow_cap=False：直接干活，不再弹"我能帮你…"清单）
        #   v0.31.2：**但如果这句话本身就是一条完整的开发需求**（"能不能帮我开发一个
        #   记账本" 有动词+产物），那就不是在泛问能力，而是换了种口气下单 —— 报菜单
        #   是一个死胡同（用户还得再重说一遍），必须放行到下面的开工守卫。
        cap = self._cap_question(text) if allow_cap else ""
        if cap and not self._looks_like_build_order(text):
            return ("askhelp", cap)
        # ★0) v0.31.2 软件开工早守卫 —— **必须排在 tableana / remote_push 之前**。
        #   真机事故（小志 2026-09-23）：
        #     · 「帮我开发一个数据分析平台」→ 被 tableana（"分析"+"数据"）抢走
        #     · 「帮我开发一个微信小程序」→ 被 remote_push 抢走
        #       （它的 `(推|发|送)...(手机|微信|…)` 命中的是 "开**发**一个**微信**"）
        #     · 「帮我开发一个能自动抓取价格…的比价系统」→ 旧窗口 22 字撑不住，
        #       **0 命中** → agent=None → 静默掉进普通聊天
        #   这两条关键词规则原位置都早于 project（先命中者胜），开发需求永远排不上队。
        if self._looks_like_build_order(text):
            return ("project", text)
        # ★0b) v0.28.x 实时数据 / 浏览器自动化（对标 QClaw/OpenClaw 的"行动型"能力）
        if not self._askish(text):
            if re.search(r"天气|气温|温度|气候|下雨|降温|气象|空气质量|空气质量指数", text):
                return ("weather", text)
            if re.search(r"打开网页|访问网页|用浏览器|网页截图|截图(网页|网站)|"
                       r"浏览器(里|中|打开|搜索|填表|操作|点击|登录)|"
                       r"在浏览器(里|中)?(打开|搜索|查|填|点击|登录|滚动|操作)|"
                       r"网页上(操作|填|点|输入)|自动(填表|登录|下单)|"
                       r"(网页|网站).{0,8}(点击|填表|登录|搜索)", text):
                return ("browser", text)
        # ★0c) v0.28.x 定时 / 邮件 / 日历 / 跨端推送 / 自进化 / 协作层
        #    （对齐 WorkBuddy 的任务板与 Hermes 的自进化；全部本地、零新依赖）
        if not self._askish(text):
            # —— v0.30.14 第三方接入（飞书 / Discord / 微信 / Webhook）放最前 ——
            #    必须早于"跨端推送"：两者都提到飞书/微信，但「接入飞书」是配置诉求，
            #    「把结果推到飞书」才是推送诉求；顺序错了会把配置问句当推送执行。
            _plat = r"(飞书|feishu|lark|discord|微信|wechat|公众号|企业微信|webhook)"
            if (re.search(r"(接入|连接|对接|绑定|配置)[^。，]{0,6}" + _plat, text, re.I)
                    or re.search(_plat + r"[^。，]{0,4}(接入|连接|对接|绑定|通道|连接器)",
                                 text, re.I)
                    # 「通了吗 / 好了吗 / 状态」这几种口语问法都要认（"第三方接通了吗"
                    # 少个「入」字也是人话，别让用户换个说法就查不到）
                    or re.search(r"第三方[^。，]{0,4}(接通|接好|连上)", text)
                    or re.search(r"接入(都|是否)?[^。，]{0,3}(通|好)(了)?(吗|没)", text)
                    or re.search(r"接入状态", text)):
                if re.search(r"测试|检测|试一下|通不通|能不能连|连得上", text):
                    return ("connector_test", text)
                return ("connector_status", text)
            # —— 跨端推送放最前：含"手机/微信/钉钉/飞书"的推送诉求更具体 ——
            if re.search(r"(推|发|送)(送)?(到|给|往)?[^，。]{0,6}(手机|微信|钉钉|飞书|telegram)|"
                         r"(手机|微信|钉钉|飞书)[^，。]{0,4}(推送|发我|提醒我)", text, re.I):
                return ("remote_push", text)
            if re.search(r"(跨端|远程|手机)[^。]{0,6}(配置|状态|怎么设|设置|怎么推)|"
                         r"怎么(把|用)[^。]{0,8}(推|发)[^。]{0,4}(手机|微信)", text):
                return ("remote_status", text)
            # —— 定时提醒 ——
            if re.search(r"提醒|闹钟|定时", text):
                if re.search(r"(我)?(定|设)了?哪些|有哪些|查看|列出|我的(定时|提醒|任务|闹钟)|"
                             r"还有(什么|哪些)|(定时|提醒|任务|闹钟)[^。]{0,4}(列表|清单)", text):
                    return ("remind_list", text)
                if re.search(r"(取消|删掉|删除|停止|不用|别)[^。]{0,8}(提醒|闹钟|定时)", text):
                    return ("remind_del", text)
                _rm_kw = re.search(r"提醒我|提醒一?下我|设(个|一个)?(提醒|闹钟)|"
                                   r"定(个|一个)?(提醒|闹钟|时)|闹钟|定时(提醒|任务)", text)
                _rm_time = re.search(r"每天|每日|每小时|每\s*\d+\s*分钟|每两?个?小时|"
                                     r"周[一二三四五六日天]|明[天早晚]|后天|今[天晚]|"
                                     r"\d{1,2}\s*[月日号]|\d{1,2}\s*[点时]", text)
                if _rm_kw or (_rm_time and re.search(r"提醒|闹钟", text)):
                    return ("remind_add", text)
            # —— 日历日程 ——
            #   ⚠️ 两个真踩到的坑：
            #     ①「会议纪要 / 会议记录」是**文档**，不是日程，不能判成"建日程"；
            #     ②「9月20日10点交材料」没有"日程/安排"字样，但**日期+钟点**本身就是
            #        强排期信号，应能建日程（否则用户会以为我没听懂）。
            _cal_kw = re.search(r"日程|日历|约会", text)
            _cal_meet = re.search(r"会议|开会", text)
            _cal_time = re.search(r"明天|后天|今天|今晚|下周|本周|周[一二三四五六日天]|"
                                  r"\d{1,2}\s*[月日号]|\d{1,2}\s*[点时]", text)
            _cal_date = re.search(r"\d{1,2}\s*[月日号]", text)
            _cal_doc = re.search(r"会议纪要|会议记录|会议总结|会议材料|会议内容|纪要", text)
            if not _cal_doc and (_cal_kw or _cal_meet
                                 or (_cal_time and (_cal_date
                                                    or re.search(r"安排", text)))):
                if re.search(r"(有(什么|哪些)|查看|看一?下|列出|最近|未来)[^。]{0,8}"
                             r"(日程|安排|会议|行程)|我(最近|未来|这周|本周)[^。]{0,4}"
                             r"(安排|日程)|^(日程|安排)(呢|怎么样|如何)?[?？]?$", text):
                    return ("cal_list", text)
                if re.search(r"(取消|删掉|删除)[^。]{0,8}(日程|会议|安排|约会)", text):
                    return ("cal_del", text)
                if _cal_time or _cal_kw:
                    return ("cal_add", text)
            # —— 邮件 ——
            #   ⚠️「查一下我的邮件」= 看邮箱，「搜一下含报价的邮件」= 搜邮件 ——
            #   所以"搜/找"这类动词后面必须**带关键词**才算搜索，不能见"查…邮件"就判搜。
            if re.search(r"邮件|邮箱|收件箱|发信", text):
                if re.search(r"(发|写|回|寄)[^。]{0,6}(邮件|信)|发给|发一封", text):
                    return ("mail_send", text)
                if re.search(r"含[^。]{1,12}的(?:邮件|信)", text) or \
                        re.search(r"(?:搜|搜索|查找)(?:一?下)?[^。]{1,12}的?(?:邮件|信)", text):
                    return ("mail_search", text)
                return ("mail_check", text)
            # —— 自进化 ——（"有什么值得沉淀的"要先于"沉淀成技能"判断，否则被当成下令保存）
            if re.search(r"(有(什么|哪些))[^。]{0,6}(值得|可以|能)(沉淀|存成技能)|"
                         r"(沉淀|自进化)[^。]{0,4}(建议|推荐)|你(学到|学会)了什么", text):
                return ("evolve_suggest", text)
            if re.search(r"(存|沉淀|记录|总结|转|变)(成|为)?(一个)?技能|"
                         r"把(刚才|这个|这次|今天)[^。]{0,10}(存|沉淀|记|变)(成|为)?技能|"
                         r"记住(这个|刚才的)?(方法|流程|做法)", text):
                return ("evolve_save", text)
            # —— 协作层：任务板 / 自动组队 ——
            if re.search(r"任务板|谁在做|团队进度|协作进度|员工在做什么", text):
                return ("team_board", text)
            if re.search(r"自动组队|让团队自动|团队自动|自动安排团队|多智能体协作|"
                         r"组个队|拉个团队|自动分工", text):
                return ("team_auto", text)
        # ★0a2) v0.27.1 真实系统操作：删除/清垃圾/查台账 —— 只认真实工具结果，
        #        绝不让模型编"已完成"。删除=回收站+授权弹窗；清理=先扫描报告。
        #   v0.27.2 收紧：只有"查我干活的账"才进台账路由（"怎么验证这个软件真伪"
        #   这类闲聊关键词不再劫持）。
        if _re.search(r"操作台账|执行记录|操作记录|台账|做过哪些", text) or \
                (_re.search(r"(?:你|小[Uu伴]|它).{0,10}?(?:真的)?"
                            r"(?:删|清|执行|操作).{0,4}(?:了|过|掉|完)", text) and
                 _re.search(r"(?:吗|么|没有|没|怎么验证|真的假的|是不是真的)", text)) or \
                _re.search(r"怎么验证(?:你|它|小[Uu伴])?.{0,6}(?:删|清|执行|做)", text):
            return ("sysops_report", text)
        # ★0a-1b) v0.27.3 语言天赋：查/清 学到的说话习惯
        if re.search(r"别学我说话|不要学我|别学我口音|忘掉我的(口音|说话方式)|"
                     r"重置(我的)?(口音|说话方式)", text):
            return ("accent_clear", text)
        if re.search(r"(我|咱)?(说话|聊天)?(是|有)?什么(口音|味儿|味道)|"
                     r"你(学|听)出我(的)?(口音|说话方式|味)了吗|"
                     r"我的(口音|说话方式)|你怎么学我说话|语言天赋", text) and \
                not self._askish(text):
            return ("accent_status", text)
        # ★0a-1) v0.27.3 语音自检：一次查清识别器/中文包/麦克风，给可执行结论
        if re.search(r"语音自检|麦克风自检|检查(一?下)?(麦克风|语音|录音)|"
                     r"测试(一?下)?(麦克风|语音)|麦克风(为什么)?没反应|"
                     r"语音(输入)?(为什么)?没反应|没听清.{0,6}为什么", text):
            return ("micdiag", text)
        # ★0a0) v0.27.3 候选接选：上一轮列了表格/文件清单，用户回"1""第一个"
        #       这种短选择时接住它，别掉进普通聊天（真机痛点：没有下一步）
        _pick = _re.fullmatch(r"\s*(?:第)?\s*([0-9０-９一二三四五六七八九十]+)"
                              r"\s*(?:[.、个号]?)\s*", text or "")
        if _pick:
            _cands = (getattr(self, "_tab_candidates", None) or []) + \
                     (getattr(self, "_pick_candidates", None) or [])
            if _cands:
                _raw = _pick.group(1)
                _CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5,
                       "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
                _idx = _CN.get(_raw)
                if _idx is None:
                    _idx = int(_raw.translate(
                        str.maketrans("０１２３４５６７８９", "0123456789")))
                if 1 <= _idx <= len(_cands):
                    _sel = _cands[_idx - 1]
                    if _sel.lower().endswith((".xlsx", ".xls", ".csv")):
                        return ("tableana", "分析 " + _sel)
                    return ("readfile", _sel, "分析")
        # v0.27.5：把"清系统垃圾"与"删图片"分开——文本明确指向 图片/照片/截图
        #   时不走系统清垃圾（交给下面的图片删除分支）。否则「清理桌面垃圾图片」
        #   会跑去清 TEMP 目录，驴唇不对马嘴。
        if self._imperative(text) and not self._askish(text) and \
                not _re.search(r"图片|照片|截图|相片", text) and \
                _re.search(r"(继续|再)?(清理|清除|清掉|删掉|删了|删除)\s*(一下)?\s*"
                           r"(电脑|系统|缓存|垃圾|临时文件|无用的)?", text) and \
                _re.search(r"(电脑|系统|缓存|垃圾|临时文件|无用的|^继续清?[理除掉])", text):
            return ("sysops_clean", text)
        # v0.27.2 图片删除：仅当"图片/照片/截图/相片"是删除目标本身时才走图片分支
        #   （"截图文件夹"不是图片；"搜索怎么删除图片"是搜索不是删除）
        _del_img_intent = (not _re.search(r"搜索|怎么|如何|教程|方法|为什么", text)) and \
            bool(_re.search(r"(?:图片|照片|截图|相片)"
                            r"(?![^，。！？\n]{0,4}(?:文件夹|目录|库))", text))
        if _del_img_intent and self._imperative(text) and \
                not self._askish(text) and \
                _re.search(r"(删除|删掉|删了|移除|清掉|清除|清理)", text):
            # 目标解析：显式路径 > 桌面上的X文件夹 > 最近引用 > 放弃并如实说明
            tgt = AT.resolve_path(text)
            why = (text or "").strip()[:80]
            # v0.27.2：图片/照片/截图 → 在真实桌面（含 OneDrive 重定向候选）上找图。
            #   点名删某张 → 只删那张；泛指"不用的图片" → 把桌面图片列出来批量确认。
            #   （修复真机痛点：旧版只认"文件夹/文件"，"删图片"掉进普通聊天被模型编造）
            import sysops as _SYSI
            imgs = _SYSI.list_desktop_images()
            # ===== v0.27.5 点名识别重做（真机 bug：点名一张 → 整桌面被删）=====
            # 旧正则靠 "XXX图片"（"图片"**必须作后缀**）认名字，于是：
            #   「删除桌面图片_2026-08-03_134353_483.png」（"图片"在最前）
            #   「删除桌面上的微信图片_2026-...png」（"图片"在中间）
            # 都认不出具体文件 → named=None → 静默落到"有图就全删" →
            # 用户点名一张，结果删了一桌面（严重误删）。现在改为：
            #   ① 文本里出现"带图片扩展名的文件名" → 最强点名信号（先精确匹配）
            #   ② 引号/书名号里的名字
            #   ③ 才轮到 "XXX图片" 模式
            #   ④ 安全底线：**点名没命中一律如实说找不到，绝不降级全删**；
            #      只有明确泛指（所有/全部/不用的…）才列全部图片请确认
            _IMG_EXT = r"(?:png|jpe?g|gif|webp|bmp|ico)"
            _GENERIC = ("不用", "没用", "无用", "所有", "全部", "多余", "这些",
                        "那些", "过期", "要删", "旧", "一些", "几个", "杂",
                        "乱", "不要", "没要", "废弃", "垃圾", "桌面上", "桌面")

            def _hits_of(keys):
                out, seen = [], set()
                for k in keys:
                    k = (k or "").strip().strip("的").strip()
                    if len(k) < 2:
                        continue
                    for p in imgs:
                        bn = os.path.basename(p)
                        if (k.lower() in bn.lower()) and p not in seen:
                            seen.add(p)
                            out.append(p)
                return out

            # ① 直接给了"带扩展名的文件名" → 最强点名信号
            #   （提取时会把"删除桌面"等指令前缀一起吞进来，故原样 + 剥词后
            #     两个候选都拿去匹配，命中即用）
            _k1 = [m.group(1) for m in _re.finditer(
                r"([^\s，。！？、；：\"'「」《》/\\|]{1,80}?\.%s)" % _IMG_EXT,
                text, _re.I)]
            if _k1:
                _c1 = []
                for k in _k1:
                    _c1.append(k)
                    _c1.append(_re.sub(
                        r"^(?:帮我|给我|替我|请|麻烦|把|去|删除|删掉|删了|移除|"
                        r"清掉|清除|桌面[上里]?的?|上的|里(?:面)?的?|一下)+",
                        "", k).strip())
                _h = _hits_of(_c1)
                if _h:
                    return ("sysops_del_imgs", _h, why)
                return ("sysops_del", "", why)   # 点名了具体文件但桌面没有 → 如实说
            # ② 引号 / 书名号里的名字
            _k2 = [m.group(1) for m in _re.finditer(
                r"[「'\"《]([^」'\"》]{2,60})[」'\"》]", text)]
            if _k2:
                _h = _hits_of(_k2)
                if _h:
                    return ("sysops_del_imgs", _h, why)
            # ③ "XXX图片/照片/截图/相片"（名字不含"图片"二字的常见说法）
            _nm = _re.sub(
                r"删除|删掉|删了|移除|清掉|清除|帮我|给我|替我|请|麻烦|"
                r"桌面[上里]?的?|上的|上面的|里面|一下|[「'\"」]", " ", text)
            m3 = _re.search(
                r"([\w\u4e00-\u9fff][\w\u4e00-\u9fff \-\.]{0,40}?)"
                r"的?(?:这?张|这?个)?(?:图片|照片|截图|相片)", _nm)
            if m3:
                key = m3.group(1).strip().rstrip("的上是在把 ")
                if key and len(key) > 1 and not any(g in key for g in _GENERIC):
                    _h = _hits_of([key])
                    if _h:
                        return ("sysops_del_imgs", _h, why)
                    return ("sysops_del", "", why)   # 点名没中 → 如实说，不转全删
            # ④ 走到这里 = **没有点名到具体文件**（点名命中已在上面 ①/②/③ 直接返回）。
            #    只要桌面上有图，就列出来请用户确认（有确认窗兜底）——保留旧版
            #    「删掉桌面上的图片」这种"半泛指"的能力。
            #    真正防误删的关键在上面：① 给了带扩展名的文件名却没匹配到、
            #    ③ 点了名（非泛词）却没匹配到 → 都直接如实说，**绝不走到这里全删**。
            if imgs:
                return ("sysops_del_imgs", imgs, why)
            if not tgt:
                return ("sysops_del", "", why)   # 桌面没图 → 如实说
        if not _del_img_intent and self._imperative(text) and \
                not self._askish(text) and \
                not _re.search(r"搜索|怎么|如何|教程|方法|为什么", text) and \
                _re.search(r"(删除|删掉|删了|移除|清掉|清除)", text) and \
                _re.search(r"(文件夹|目录|文件|快捷方式|文档|压缩包|视频|音频|"
                           r"\.[A-Za-z0-9]{1,6})", text, _re.I):
            # 目标解析：显式路径 > 桌面上的X文件夹 > 最近引用 > 放弃并如实说明
            tgt = AT.resolve_path(text)
            why = (text or "").strip()[:80]
            if not tgt:
                import sysops as _SYSF
                # v0.27.3：泛指"空文件夹"——没有点名，旧版解析不出目标就回
                #   "找不到"（真机反馈：还是删不掉）。现在真的去桌面扫一遍，
                #   把空文件夹列成清单交给你确认后删。
                if _re.search(r"空(的)?(文件夹|目录)|没用(的|的)?(文件夹|目录)|"
                              r"所有?(的)?空", text):
                    roots = _SYSF.desktop_candidates() or \
                        [os.path.join(os.path.expanduser("~"), "Desktop")]
                    empties = []
                    for root in roots:
                        if os.path.isdir(root):
                            empties += _SYSF.find_empty_dirs(root)
                    if empties:
                        return ("sysops_del_paths", empties, why)
                    return ("sysops_del", "", why)   # 桌面确实没有空文件夹 → 如实说
                m = _re.search(
                    r"(?:桌面|Desktop)[上里]?的?[^，。！？\n]{0,20}?[「'\"]?"
                    r"([\w\u4e00-\u9fff][\w\u4e00-\u9fff \-\.]{0,40}?)[」'\"]?"
                    r"的?(?:这?个)?空?(?:的)?(?:文件夹|目录)", text)
                if m:
                    name = m.group(1).strip()
                    cand = None
                    # v0.27.2：真实桌面路径（SHGetKnownFolderPath + OneDrive 候选）
                    for root in (_SYSF.desktop_candidates() or
                                 [os.path.join(os.path.expanduser("~"), "Desktop")]):
                        if not os.path.isdir(root):
                            continue
                        for e in sorted(os.listdir(root)):
                            if name in e and os.path.isdir(os.path.join(root, e)):
                                cand = os.path.join(root, e)
                                break
                        if cand:
                            break
                    tgt = cand or ""
            if not tgt and self.path_refs:
                m2 = _re.search(r"(?:那个|这个|刚才|之前)", text)
                if m2 and _re.search(r"(?:文件夹|目录)", text):
                    tgt = self.path_refs[-1]
            return ("sysops_del", tgt, why)
        # ★0a1) v0.27.2 真实动作：上网搜索（真的打开系统浏览器搜，不假手模型编结果）
        if self._imperative(text) and \
                _re.search(r"浏览器|网页|网上|互联网|百度|必应|谷歌|bing|google",
                           text, _re.I) and \
                _re.search(r"搜|检索|查一?下|帮我查|找一?找", text):
            return ("websearch", self._extract_search_query(text) or text.strip())
        # ★0a2) v0.27.2 真实体检：电脑安全状况（只读，Defender 真实状态，不编结论）。
        #   命令直问（"帮我查病毒"）或针对"我的电脑"（"我电脑是不是中毒了"）才触发；
        #   泛泛聊"现在的病毒真多"这类绝不接。
        if (self._imperative(text) or
                _re.search(r"(?:我|咱|我们|这台|本)(?:的)?(?:电脑|计算机|机器|系统)"
                           r"|电脑(?:上|里|中)?(?:有|是不是|有没有)", text)) and \
                not self._askish(text) and \
                _re.search(r"病毒|杀毒|查杀|木马|中毒|安全体检|电脑体检|"
                           r"安全(检查|扫描|状况|状态)", text):
            return ("secchk", text)
        # ★0a3) v0.30.7 整机现状（只读）：CPU 占用/核数、内存、磁盘剩余、开机时长。
        #   触发与 secchk 同款保守：必须**指向这台机器**（我电脑/这台机器/本机），
        #   或者用明确口令（电脑配置 / 系统信息 / 硬件信息）。泛泛聊"现在电脑真慢"
        #   这类不指向说话人机器的句子**绝不接**（会答非所问）。
        if not self._askish(text) and _re.search(
                r"配置|卡不卡|卡吗|卡顿|内存|CPU|处理器|硬盘|磁盘|开机|"
                r"运行多久|用了多久|性能|系统信息|硬件信息|跑得动",
                text, _re.I) and (
                _re.search(r"(?:我|咱|我们|这台|本)(?:的)?(?:电脑|计算机|机器|系统)"
                           r"|本机|我这|咱这|这台机", text) or
                _re.search(r"电脑(配置|信息|体检)|系统(信息|配置)|硬件(信息|配置)|"
                           r"整机|机器配置|电脑怎么样|电脑啥样", text)):
            return ("sysinfo", text)
        # ★0a4) v0.30.7 进程占用（只读）：谁在占内存 / 占 CPU。
        #   要求同时出现「进程/占用」词与「询问词」，且不是"是什么"这一类定义型提问。
        #   守卫用**专用**的一条：只挡"推荐/软件/工具/方法"这类知识型提问
        #   （"帮我推荐个清理内存的软件"）。不能用通用的 _askish —— 它含 `哪个`，
        #   会把「哪个程序占内存」这种明明在让我看机器的话一起挡掉（实测踩到）。
        if not _re.search(r"推荐|介绍|哪(?:款|些)|软件|工具|方法|教程|该用|用什么",
                          text) and \
                _re.search(r"进程|后台程序|占(用)?(内存|CPU)|吃(内存|CPU)|内存占用",
                           text, _re.I) and \
                _re.search(r"哪|谁|看看|看下|列|查|多少|占|吃|最大|最高|排行",
                           text):
            return ("procs", text)
        # ★0a5) v0.30.7 结束进程（**不可逆** → 走授权确认）。
        #   只认"动词 + ASCII 进程名"，不做模糊匹配：中文名（"关掉微信"）留给"打开"那套，
        #   `kill_process` 再挡一层系统关键进程 —— 两道都在，宁可打不准，不可杀错。
        _km = _re.search(
            r"(?:结束|关掉|关闭|退出|杀掉|杀死|kill|terminate)\s*"
            r"(?:进程|程序|应用)?\s*[《\"'\[]?([A-Za-z][A-Za-z0-9_.\-]{1,40})",
            text, _re.I)
        if _km:
            return ("killproc", _km.group(1))
        # ★0a) v0.27 数学脑：分析/统计表格数据 → mathlab 真算（数值是真算出来的，
        #       不是让模型编数）；命中后再由 _agent_run 走 _table_analysis_reply
        if MLAB is not None and not _re.search(
                r"写一[段篇个份]|写个|生成|做一[份个张]|做份|出一份|报告文字|文章|文案|方案",
                text) and _re.search(
                r"(分析|解读|统计|看看|看一下|总结一下|找.{0,2}规律|趋势|相关性|离群)"
                r"[^，。]{0,12}(表格|数据|表|excel|xlsx)|(表格|数据|excel|xlsx)"
                r"[^，。]{0,8}(分析|解读|统计|规律|趋势|怎么样)", text, _re.I):
            return ("tableana", text)
        # ★0-) 创作栏目会话粘性（v0.20.0）：漫剧分步进行中 / 图片会话已有图时，
        #      分步指令与微调话术直通执行，不再掉进普通聊天
        #      （真机痛点：企划后说「出图」、图片会话里说「背景换成黄昏」没人接）
        if self.mode == "work" and not _re.search(
                r"怎么|为什么|为何|如何|什么是|代码|脚本|程序", text):
            if self._chip == "manga":
                story = getattr(self, "_ses_story", None) or self._story_load()
                if story and _re.search(
                        r"出图|开画|画吧|继续画|都画|重画|重绘|配音|旁白|合成|成片|"
                        r"导出|出视频|一条龙|直接成片|重新?企划|换个?题材|"
                        r"第\s*[0-9一二三四五六七八九十]+\s*镜", text):
                    return ("manga", text)
            elif self._chip == "image":
                has_img = bool(getattr(self, "_ses_img_dir", None) and
                               os.path.isdir(self._ses_img_dir))
                if has_img and self._wants_image_edit(text):
                    return ("image", text)
        # ★0) 引用"刚才/之前提到"的路径（没说新路径，但指代上次的路径/文件/文件夹）
        if not AT.resolve_path(text) and self.path_refs and \
           _re.search(r"(?:那个|刚才|之前|上次|上面|前面|这个).{0,8}(?:路径|文件夹|目录|文件|资料)", text) and \
           any(v in text for v in ("打开", "运行", "启动", "读", "分析", "总结", "看看", "查看", "检查")):
            p = self.path_refs[-1]
            # 说"那个文件夹/目录"但上次读的是文件 → 自动切到其所在目录
            if not os.path.isdir(p) and _re.search(r"(?:文件夹|目录)", text):
                p = os.path.dirname(p)
            if _re.search(r"打开|运行|启动", text) and \
               not _re.search(r"读|看看|查看|分析|总结|检查|内容", text):
                return ("openpath", p)
            if os.path.isdir(p):
                return ("readfolder", p,
                        "分析" if _re.search(r"分析|总结|检查|审查|问题|异常", text) else "")
            return ("readfile", p, "分析" if _re.search(r"分析|总结|检查|审查", text) else "")
        # ★0a) 聊天文本里存在**真实路径** → 按动词分流：打开=系统打开；读/分析=读内容
        #      （"怎么打开/为什么读不了"这类疑问句不抢答，交给模型解释）
        if not _re.search(r"(?:怎么|为什么|为何|如何|为啥|行不行|可不可以|能不能).{0,12}"
                          r"(?:打开|运行|启动|读|分析)", text):
            _p = AT.resolve_path(text)
            if _p:
                if _re.search(r"打开|运行|启动", text) and \
                   not _re.search(r"读|分析|总结|看看|查看|浏览|检查|提取|内容", text):
                    return ("openpath", _p)
                if os.path.isdir(_p):
                    dir_ask = _re.search(r"读|分析|总结|检查|浏览|扫描|看看|查看|列|清点", text) or \
                        _re.search(r"(?:里面|下面|下|里|中).{0,6}(?:什么|啥|哪些|有没有|文件|内容|目录)", text)
                    if dir_ask:
                        return ("readfolder", _p,
                                "分析" if _re.search(r"分析|总结|检查|审查|问题|异常", text) else "")
                elif _re.search(r"读|看|查看|分析|总结|检查|扫|看看|提取|内容", text):
                    return ("readfile", _p,
                            "分析" if _re.search(r"分析|总结|检查", text) else "")
        # ★0c) 含糊文件指代：打开/读 "E盘里的txt" / "D盘那个word" 之类（无具体文件名）
        #      → 按盘符+类型做**有界搜索**，命中即开/读（修复"打不开E盘txt"：原来
        #      resolve_path 拿不到具体路径就掉进普通聊天，模型只能编造"打不开"）。
        #      疑问句（怎么/为什么/哪/什么）不抢答；只在确实有打开/读取意图且无具体路径时尝试。
        if not _re.search(r"(?:怎么|为什么|为何|如何|为啥|行不行|可不可以|能不能|哪[个些]|什么).{0,12}"
                          r"(?:打开|运行|启动|读|看|查|分析)", text) and \
           _re.search(r"(?:打开|运行|启动|读|查看|看看|看下|分析|浏览)", text):
            _vf = AT.resolve_vague_file(text)
            if _vf:
                if _re.search(r"打开|运行|启动", text) and \
                   not _re.search(r"读|分析|总结|看看|查看|浏览|检查|提取|内容", text):
                    return ("openpath", _vf)
                return ("readfile", _vf,
                        "分析" if _re.search(r"分析|总结|检查", text) else "")
        # ★0b) 打开电脑应用：打开/启动/运行 + 应用名（整句；不是路径、不是脚本/程序）
        #      v0.26.1：app 名允许更长；剥掉"听歌/放歌/用用"等尾巴词（打开网易云音乐听歌）
        _o = _re.match(r"^(?:帮我|请|麻烦|给我|帮我打开|请打开|麻烦打开)?"
                       r"(?:打开|启动|运行|开一下|打开一下|打开下)\s*[:：]?\s*"
                       r"(?P<app>[A-Za-z0-9\u4e00-\u9fa5().\-\s]{1,24})"
                       r"(?:吧|呀|呗|嘛|哦|呢|好吗|好嘛|谢谢|多谢|。|．|\.)?$", text.strip())
        if _o:
            app = _o.group("app").strip()
            app = re.sub(r"(?:好吗|好嘛|谢谢|多谢)$", "", app).rstrip("吧呀呗嘛哦呢么")
            # 剥尾巴动作（放首歌/听音乐/用用/看看…），最长优先
            for tail in ("听首歌", "放首歌", "听会歌", "听歌", "放歌", "放音乐",
                         "听音乐", "播放音乐", "用用", "玩玩", "看看", "开开"):
                if app.endswith(tail):
                    app = app[: -len(tail)].strip()
                    break
            app = app.strip().rstrip("的")
            if app and not any(w in app for w in ("脚本", "代码", "程序", "网页", "网站", "项目")):
                return ("openapp", app)
        # 0) 读文件：读一下/读取/看看 + 文件名（相对路径或带扩展名；文件不存在时给友好提示）
        m = _re.search(r"(?:读(?:取|一下|一读|取一下)?|看看|帮我读|查看)\s*[:：]?\s*"
                       r"(?P<p>[^\s，。]{1,80}?\.(?:txt|md|docx|json|py|csv|js|ts|html|xml|yaml|yml|log|ini|xlsx|doc|xls|pdf))", text, flags=_re.I)
        if m:
            return ("readfile", m.group("p").strip())
        # 0c) 泛指"读我电脑/桌面/我的文件"但没给具体文件 → 列目录引导
        if ("能" in text and "读" in text and ("文件" in text or "电脑" in text)) or \
           ("看看" in text and "桌面" in text) or \
           ("读" in text and ("桌面" in text or "我的电脑" in text) and "文件" not in text and "." not in text):
            return ("listhelp", "")
        # 1) 找文件 / 文档（可选"在 X 里"指定目录）
        if "找" in text and any(w in text for w in ("文件", "文档", "在")):
            seg = text
            seg = _re.sub(r"^(帮我|请|麻烦)?(找一下|找|查找|搜一下|搜索|查一下|查)\s*", "", seg)
            seg = _re.sub(r"\s*(文件|文档).*$", "", seg)
            folder = ""
            if "在" in seg:
                part = seg.split("在", 1)[1]
                seg = seg.split("在", 1)[0]
                folder = _re.sub(r"(里|中|目录|文件夹|找|查找).*$", "", part).strip()
            kw = _re.sub(r"(的|里|中|目录|文件夹)", "", seg).strip(" ，。：:")
            if kw:
                return ("files", kw, folder.strip())
        # 2) 分析文档（路径带扩展名）
        m = _re.search(r"分析(?:一下|分析)?\s*(?P<p>[^，。]{2,80}?\.(?:txt|md|docx|json|py|csv|xlsx|pdf|doc|xls))", text, flags=_re.I)
        if m:
            return ("readfile", m.group("p").strip(), "分析")
        # 2b) v0.27.3 目录体检（真扫描）：分析/盘点 桌面|文件夹|目录|磁盘
        #   ——放在 analyzectx 之前。真机痛点：说"分析一下桌面上的文件"，
        #     旧版没读过文件就直接回"我还没读过文件呢"，等于没有分析。
        _dir_intent = re.search(r"(分析|统计|盘点|整理|看一下|看看|检查一下|"
                                r"梳理|扫一?下|体检)", text) and \
            re.search(r"(桌面|文件夹|目录|文件|磁盘|盘里|电脑里|这台电脑)", text)
        _has_ext = re.search(r"\.(?:txt|md|docx|json|py|csv|xlsx|pdf|doc|xls)\b",
                             text, re.I)
        if _dir_intent and not _has_ext and self._imperative(text) \
                and not self._askish(text):
            return ("dirscan", text)
        # 2b-2) 分析 泛指文件 → 尝试工作上下文里最近的文件
        if re.search(r"分析(?:一下)?(?:这个|这份|刚才|我发你的)?(?:文件|方案|文档)?$", text) or \
           ("分析" in text and "文件" in text and "." not in text):
            # v0.27.3：没有上下文也别干瞪眼——先看是不是在分析某个目录
            if _dir_intent:
                return ("dirscan", text)
            return ("analyzectx", "")
        # 2a) 出图/出片/漫剧成片指令（创作引擎直连；区别于“写脚本/文案”类需求）
        if not re.search(r"脚本|剧本|文案|分镜|代码|python|编程|网页|网站|前端|后端|系统|"
                         r"应用|接口|程序", text, _re.I):
            if re.search(r"漫剧|漫画剧|动态漫画|有声漫画", text) and \
                    re.search(r"生成|制作|做|做一集|出|产|画", text):
                return ("manga", text)
            if re.search(r"(?:生成|制作|做|合成|产|出)[^，。]{0,8}"
                         r"(?:视频|短片|动画|vlog|纪录片|宣传片|微电影)", text, _re.I) and \
                    not re.search(r"漫剧|漫画", text):
                return ("video", text)
            if re.search(r"(?:画|生成|做|制作|出|合成)[^，。]{0,10}"
                         r"(?:图片|插画|海报|配图|示意图|封面图|头像|壁纸|一张图|一张图片|张图)", text):
                return ("image", text)
        # 2b) 生成文件：PPT / Word / Excel / 报告文档（真的产出文件到桌面）
        if re.search(r"(?:生成|做|制作|写|输出|帮我做|帮我写|帮我做一份|做一份|给我做一份|"
                     r"出一份|产出)[^，。]{0,16}"
                     r"(?:ppt|PPT|幻灯片|演示文稿|word|Word|docx|文档|报告|方案|总结|"
                     r"excel|Excel|表格|xlsx)", text):
            if re.search(r"ppt|幻灯片|演示文稿", text, re.I):
                return ("genppt", text)
            if re.search(r"excel|表格|xlsx", text, re.I):
                return ("genxls", text)
            return ("gendoc", text)
        # 2b+) v0.26.2 产物续改路由：本会话刚做过 表格/文档/PPT，说"加xx/改xx/再xx/换xx"
        #      → 回到对应生成器，在上一版文件上改（_genfile_run 会喂旧全文并覆盖写回）
        if hasattr(self, "_ses_gen") and self._ses_gen:
            _nm = {"genxls": r"表格|xls|excel|表",
                   "gendoc": r"文档|报告|方案|总结|纪要|说明|word",
                   "genppt": r"ppt|幻灯片|演示文稿"}
            _chg = re.compile(
                r"(?:改|修改|加|加一?个|增|增加|删|删除|减|去掉|换|调整|更新|改改|续写|"
                r"接着|再改|补|补齐|更新一下|重新整理|优化|美化|简化|详细点|再写|再做|"
                r"把|将|后面|末尾|开头|第一页|第二页|中间|再加|再加两行|多来点)")
            gen_act = re.compile(r"(?:这?个)?(表格|xlsx|excel|ppt|word|文档|报告|方案|总结|"
                                 r"演示文稿|幻灯片|表|内容|它|这个|那个|一份)")
            if _chg.search(text) and gen_act.search(text):
                _hit = next((k for k, pat in _nm.items()
                             if pat and re.search(pat, text, re.I)
                             and k in self._ses_gen), None)
                if _hit and not re.search(
                        r"(?:打开|读一下|分析|查看|怎么|为什么|能不能)", text):
                    return (_hit, text)
        doc_ctx = re.search(
            r"(?:根据|基于|结合|参考|按|顺着|沿用|照|把|将)\s*"
            r"(?:这个|这份|刚才|上面|前面|那个|那份|已分析|分析结果|它的|里面的)?"
            r"(?:文档|文件|内容|材料|资料|分析|报告|方案|笔记|记录|摘要|回复|结论|数据|结果)"
            r"(?:里|中|上面|来|整理|继续|再)?", text)
        doc_out = re.search(
            r"(?:做|写|生成|制作|输出|整理|总结|汇总|出|编|续写|接着写|补)"
            r"(?:一|二|两)?(?:成|成一份|一份|个|篇|份|一下)?\s*(?:新)?\s*(?:文档|报告|方案|"
            r"总结|说明|材料|纪要|清单|文稿|笔记|分析报告|Word|word|PPT|ppt|Excel|excel|表格)"
            r"|(?:做|写|生成|整理|汇总|出|编).{0,6}(?:成|成一份|一份|一个|一篇)?\s*(?:文档|"
            r"报告|方案|总结|说明|材料|纪要|清单|文稿|笔记|表格)", text)
        if doc_ctx and doc_out and not re.search(
                r"(?:网站|网页|官网|小程序|前后端|全栈|后台|数据库|系统|"
                r"客户端|软件|平台|管理系统)", text):
            return ("gendoc", text)
        # 2c) 全栈开发项目：强指令 + 具体产物才算"开工"；泛指"项目"不触发
        #   v0.31.2：补一道**叙述句守卫**。改动前这条规则是裸 `re.search`，于是
        #   「我昨天开发了一个网站」「怎么开发一个网站」「让电脑帮我开发网站」
        #   「帮我搜一下有没有开发网站的工具」**全都**被判成开发项目 —— 在干活栏目里
        #   会真的建出一个项目来（实测 5 条全部命中）。真正的开工需求现在由前面那道
        #   早守卫（`_looks_like_build_order`）先认领，这里只管兜住漏网的。
        if _re.search(
                r"(?:开发|做一个|做个|帮我做|帮我开发|搭一?个|写一?个|建一?个|弄一?个|"
                r"设计一个|帮我建|帮我搭)\s*"
                r"(?:一个|一下|个)?\s*[^，。！？!?\n]{0,22}?"
                r"(?:网站|官网|网页应用|网页|系统|平台|小程序|前后端|全栈|后台|数据库|"
                r"管理工具|工具软件|应用软件|博客|商城|留言板|记账|待办应用|管理系统|"
                r"登录系统|计算器|画板|小游戏|机器人|客户端|页面)", text,
                flags=_re.I) and len(text) > 4 \
                and not self._order_narrative(text):
            return ("project", text)
        # 2d) 领域创作：视频/分镜/漫剧/口播 等语境 + 脚本类字样 → 技能（创作），
        #     绝不误进"写代码"（用户痛点：说写视频脚本却跑去生成 python）
        if re.search(r"(?:视频|短视频|分镜|漫剧|漫画|动漫|动画|口播|带货|种草|"
                     r"vlog|Vlog|直播|剧情|解说|纪录片|宣传片|微电影|影视|预告片|"
                     r"分集).{0,14}?(?:脚本|剧本|分镜|分镜表|剧情|故事|大纲)", text) and \
           not re.search(r"(?:python|代码|编程|后端|函数|import |def |网页|html|接口|"
                         r"服务器|数据库)", text, re.I):
            skill = "manga-pipeline" if re.search(r"漫剧|漫画|动漫|动画", text) \
                else "video-script"
            return ("skill", skill, text)
        # 2d2) 文案/策划类（种草/带货/朋友圈/小红书/营销…+文案）→ 文案技能
        if (re.search(r"(?:帮我|写|做|创作|生成|策划|来一段|给我).{0,14}"
                      r"(?:种草|带货|朋友圈|小红书|营销|宣传|广告|推广)?文案", text) or
            re.search(r"(?:种草|带货|营销|宣传|广告|推广).{0,8}(?:文案|口播稿)", text)) and \
           not re.search(r"(?:python|代码|编程|网页|html)", text, re.I):
            return ("skill", "copywriting", text)
        # 2e) 技能库自动检索：明确开工指令 + 关键词命中高置信技能 → 交给技能执行
        if not re.search(r"(?:python|代码|编程|后端|函数|import |def |运行脚本|服务器|"
                         r"数据库|\.py\b|html)", text, re.I) and \
           re.search(r"(?:写|做|生成|制作|创作|策划|编|出一份|帮我|我要|来一段|设计)",
                     text):
            low = text.lower()
            for s in SKL.search(text, 2):
                kw_hit = any((kw.lower() in low) for kw in s.get("keywords", [])) or \
                         s["name"].lower() in low
                if kw_hit:
                    return ("skill", s["name"], text)
        # 3) 写/生成 脚本或代码（学了编程 = 真干活入口；网页单页也走这里）
        if ("脚本" in text or "代码" in text or "程序" in text or
                _re.search(r"(?:写|做|生成).{0,6}(?:网页|页面|html)", text, _re.I)) and \
           any(w in text for w in ("写", "生成", "做", "编")):
            key = next((k for k in ("脚本", "代码", "程序", "网页", "页面") if k in text), "")
            head, _, tail = text.partition(key) if key else (text, "", "")
            head = _re.sub(r"^(帮我|请|麻烦)?(写|生成|做个|做|编程|写段|帮我写)", "", head)
            req = (tail.strip(" ，。：:") or head.strip(" ，。：:"))
            req = _re.sub(r"^(帮我|请|麻烦)?(写个|写一个|写一段|生成一个|生成)?", "", req).strip()
            # 需求里带"根据/结合/参考 我读的文件"等 → 附加工作上下文
            if req:
                return ("script", req, "不运行" not in text)
        # 3b) "能不能编程/会编程吗/帮我把 X 做成程序" → 说明会 + 引导（含学习动机）
        if re.search(r"(会|能|可以).{0,4}(编程|写代码|写程序)", text) or \
           re.search(r"(学了|学习).{0,8}(编程|代码).{0,10}(能|可以|怎么|干嘛|用)", text):
            return ("codeask", "")
        # 4) 联网学
        m = _re.search(r"(?:上网|联网|帮我上网)学(?:习)?(?:一下)?\s*(?P<topic>[^，。]+)", text)
        if m:
            return ("weblearn", m.group("topic").strip())
        # 5) 运行刚才的脚本（"运行"/"跑一下/执行" + 脚本/它）
        if _re.match(r"^(?:运行|跑一下|执行)[^，。]{0,8}(?:脚本|程序|代码)?[^，。]{0,4}$",
                     text.strip()) or \
           ("运行" in text and ("脚本" in text or "刚才" in text) and len(text) < 24):
            return ("runlast", "")
        # 5b) v0.27.1 能力判定 broker（非预设机制）：真实工具没接住的执行类请求
        #     → 先判定 能做/学过能做/学了再做/真不能做，绝不含糊装能
        if _CD and _CD.is_scriptable(text) and _re.search(
                r"帮我|给我|替我|把|将|去|执行|操作|运行|弄|搞|处理|批量|自动化", text):
            return ("cando", text)
        return None

    # ============ v0.23 PASM×LLM 真协同（不是装饰）：起草→评审→修正→采纳 ============
    def _pitfall_lessons(self, req: str, k: int = 3) -> str:
        """从程序记忆里捞"以前踩过的坑"（失败教训），按相关性排前几条给评审参考。"""
        try:
            q = (req or "")[:60]
            lst = [p for p in ML.proc_list() if not p.get("ok") and p.get("lesson")]
            if not lst:
                return ""

            def _sc(p) -> int:
                hay = str(p.get("context", ""))[:60]
                return sum(1 for w in (q[i:i + 2] for i in range(len(q) - 1))
                           if w and w in hay) // 2
            lst.sort(key=_sc, reverse=True)
            lines = [f"· 上次干这类事栽过：{p.get('context','')[:34]}→教训：{p.get('lesson','')[:70]}"
                     for p in lst[:k] if _sc(p) > 0]
            return "\n".join(lines) if lines else ""
        except Exception:
            return ""

    def _coop_worth(self, text: str, task: str) -> bool:
        """v0.25：判断这条消息值不值得跑双脑协同（重活才值得多花模型调用）。
        重活 = 写作/创作/代码/成篇方案；闲聊问答一律单次直出。"""
        if not bool(self.cfg.get("coop", True)):
            return False
        t = (text or "").strip()
        if task in ("code", "generation"):
            return True
        if task in ("summarize", "extract"):
            return len(t) >= 120                       # 长文摘要才值得复核
        if task == "reasoning":
            return len(t) >= 80
        return len(t) >= 150                           # 未分类的超长输入兜底

    def _brain_coop(self, req: str, system: str = "", max_tokens: int = 1500,
                    rounds: int = 1) -> tuple:
        """PASM×LLM 真互补交叉作业（重活专用）：

        1. **LLM 起草** 第一版成品；
        2. **PASM 评审**：规则校验(validator) + 回放程序记忆里同类的失败教训，
           再让 LLM 扮演挑剔评审找 2~4 个具体问题（评审要求给出可改意见，不是空话）；
        3. **LLM 复审修正**：把评审意见带回原需求重出一版成品；
        4. **PASM 采纳**：修正版明显不劣于草稿则采纳；并把"这类活要避开什么"写回
           程序记忆——下次同类活开局就能看到（闭环是真的：互相改进了对方的下一次）。
        返回 (最终成品, 事件简述列表) —— 事件列表会展示给用户，证明不是摆设。
        """
        events = []
        try:
            draft = (self._brain(req, system=system, max_tokens=max_tokens) or "").strip()
        except Exception:
            return "", []
        events.append("起草")
        ans = draft
        lessons = ""
        try:
            lessons = self._pitfall_lessons(req)
        except Exception:
            pass
        for rnd in range(rounds):
            ok, probs = VAL.check("generation", ans, min_len=20)
            # 评审：规则问题 + 历史教训 + 请模型当挑剔评审
            judge = ("你是严格的成品评审。下面是给用户的一版成品草稿。\n"
                     "请指出 2~4 个**具体、可执行**的问题（漏了什么目标点/哪里空泛/"
                     "有没有明显错误/如何更好），不要客套。"
                     "如果已足够好，只回 PASS。")
            extra = ""
            if probs:
                extra = "\n另外机器校验发现：\n- " + "\n- ".join(probs)
            if lessons:
                extra += "\n历史教训（同类活要注意）：\n" + lessons
            verdict = ""
            try:
                verdict = (self._brain(
                    "用户需求：\n" + (req or "")[:900] +
                    "\n\n草稿：\n" + ans[:4000] +
                    "\n\n" + judge + extra,
                    system="评审：只回 PASS 或「具体问题清单」，不发新成品。",
                    max_tokens=700, task="study",
                    prefer="local") or "").strip()   # v0.27 交叉验证：评审换本地脑子（没有就落回云端）
            except Exception:
                break
            if not verdict or re.match(r"^\s*(PASS|通过|没问题|很好)\s*[:：]?", verdict, re.I):
                events.append("评审通过")
                break
            events.append(f"评审{rnd + 1}·修正")
            try:
                revised = (self._brain(
                    "用户需求：\n" + (req or "")[:900] +
                    "\n\n你上一版草稿：\n" + ans[:4000] +
                    "\n\n【评审意见】\n" + verdict[:1800] +
                    "\n\n请针对意见修订后，重新输出**完整成品**（只输出成品本身）。",
                    system=system, max_tokens=max_tokens) or "").strip()
                if len(revised) >= max(20, int(len(ans) * 0.3)):
                    ans = revised
            except Exception:
                pass
        # 采纳并回写程序记忆（"这类活要防什么"），下次同类开局自动可见
        try:
            if ans and len(ans) > 30:
                ML.proc_push((req or "")[:70], "coop", True,
                             lesson=("先草稿→自评→修正好再交；评审轮数" +
                                     str(len([e for e in events if "修正" in e]))))
        except Exception:
            pass
        events.append("采纳")
        return ans, events

    def _coop_footer(self, events: list) -> str:
        if not events:
            return ""
        tag = "起草→" + "→".join(events[1:-1]) if len(events) > 2 else "起草→采纳"
        return ("\n\n---\n<sup style='color:#94a3b8'>PASM×LLM 真协同："
                + html.escape(tag) + " ✓（教训已回写记忆）</sup>")

    # —— v0.27 模型路由器（多模型编排）：任务分型 → 快慢搭配 ——
    _HEAVY_TASKS = {"filegen", "project", "dev", "copy", "skill", "code"}
    _HEAVY_RE = re.compile(
        r"代码|程序|开发|实现|脚本|函数|网页|网站|文件|表格|文档|PPT|幻灯片|"
        r"方案|报告|文案|排版|长文|详细|完整|步骤|教程|编译|部署", re.I)

    def _route_endpoint(self, task: str = "", prompt: str = "", prefer: str = ""):
        """按任务轻重路由模型端点（编排，而不只是"有多个模型"）：

        - 重活（filegen/project/dev/copy/skill/code 或 prompt 命中干活特征）→ 云端优先
        - 轻活（短闲聊/情绪陪伴/study 提炼）→ 本地小模型优先（快·私密·零成本）
        - prefer="local"/"cloud" 强制指定一侧（交叉验证：评审换一个脑子）
        - 一侧不可用自动落到另一侧；都没有 → None（走离线微脑）
        用户在设置里固定了 cloud / local:<名> 时尊重手选，不参与自动路由。
        """
        cfg = self.cfg
        if cfg.get("model_choice", "auto") not in ("", "auto") and not prefer:
            return self._endpoint()
        key = cfg.get("api_key") or ""
        cloud = (cfg["base_url"], cfg["model"], key) if key else None
        loc = self.local or detect_local_llm()
        local_ep = None
        if loc:
            self.local = loc
            local_ep = (loc["base_url"], self._valid_local_model(), "")
        if prefer == "local":
            return local_ep or cloud
        if prefer == "cloud":
            return cloud or local_ep
        heavy = task in self._HEAVY_TASKS or (
            task in ("brain", "chat") and
            bool(self._HEAVY_RE.search((prompt or "")[:400])))
        if heavy:
            return cloud or local_ep
        return local_ep or cloud          # 轻活本地优先

    def _brain(self, prompt, system="你是一位严谨可靠的 AI 助手，用中文回答。",
               max_tokens=1500, task="brain", prefer=None, on_think=None):
        """用当前可用模型回答（v0.27 起按任务轻重自动路由；云端→本地→离线，
        均尊重顶部"大脑切换"的固定选择）。task 走网关任务路由
        （brain=分析/写码等重活；study=提炼轻活；filegen=生成文件）。

        v0.31.2：新增 `on_think` 透传 —— 重活（开发/写码/生成文件）以前**没有**
        思考流，模型推理期间界面全黑（本地模型下可能就是几分钟）。现在调用方
        可以接上，把真实的思考增量播报到过程流里（不是伪造的"假思考"）。
        """
        if prefer:
            ep = self._route_endpoint(task=task, prompt=prompt, prefer=prefer)
        elif self.cfg.get("model_choice", "auto") in ("", "auto"):
            ep = self._route_endpoint(task=task, prompt=prompt)
        else:
            ep = self._endpoint()
        if ep:
            return self._llm_call(system, prompt, ep[0], ep[1], ep[2],
                                  max_tokens=max_tokens, task=task,
                                  on_think=on_think)
        raise RuntimeError("没有可用模型（请配置云端 Key 或启动本地 Ollama）")

    def _skill_run(self, name: str, req: str) -> str:
        """执行技能：把技能正文（含依赖技能）作为规程交给模型，产出完整交付物。"""
        s = SKL.load(name)
        if not s:
            return (f"技能库里暂时没有《{name}》。可在「🎓 技能」里添加，"
                    f"或让我上网自学相关内容沉淀成技能后再做。")
        if not (req or "").strip():
            req = f"请按《{name}》的用途，帮我把这件事做完整。"
        spec = SKL.inspect(name)
        note_txt = ""
        try:
            note_txt = knowledge.inject_relevant(req, 3)
        except Exception:
            pass
        extra = f"\n\n【可参考的已学知识】\n{note_txt}" if note_txt else ""
        p = self.cfg.get("persona", "温和沉稳")
        try:
            ans = self._brain(
                f"用户需求：{req}\n\n请严格按上面的技能规程执行：需要先澄清的关键参数"
                f"先列出来（一两句、带默认值即可）；信息足够就直接给出**完整成品**，"
                f"不要只讲思路。",
                system=(f"你是性格偏「{p}」的 PASM，正在为用户执行技能《{name}》。\n\n"
                        f"{spec}{extra}\n\n"
                        f"硬性要求：内容完整、可直接使用；凡技能注明"
                        f"\"图片/视频渲染\"等能力尚未接入的，如实说明并提供提示词，"
                        f"绝不假装已经生成。"),
                max_tokens=3000)
            return (ans or "").strip() or f"（《{name}》没有产出内容，请把需求再说具体些。）"
        except Exception as ex:
            return (f"技能《{name}》执行时没能连上模型（{ex}）。"
                    f"技能已就位：配好云端 Key 或启动本地 Ollama 后即可使用。")

    def _touch_ref(self, p: str):
        """记住本会话出现过的真实路径（最近 8 个），供"刚才那个路径/文件夹"引用。"""
        if p in self.path_refs:
            self.path_refs.remove(p)
        self.path_refs.append(p)
        self.path_refs = self.path_refs[-8:]

    # ---------- 符号推理层·回写闭环（v0.28.5）----------
    _sym_sink_ready = False

    def _register_symbolic_sink(self):
        """注册一次「已验证解 → 记忆/向量循环」的落盘回调（闭环第三步）。"""
        if CompanionWindow._sym_sink_ready:
            return
        try:
            import symbolic as SY
            _sol_path = os.path.join(getattr(ML, "DATA_DIR", ""),
                                    "symbolic_solutions.jsonl")

            def _sink(rec):
                try:
                    subj = rec.get("subject") or rec.get("kind") or "推理"
                    # ① 写入分层记忆（情景），后续 recall_layers 能召回
                    #    salience=4：确定性求解的结论比日常闲聊值钱得多，
                    #    容量触顶时绝不该被闲聊挤掉（v0.29.0 P0.3）。
                    ML.episode_push(
                        "符号推理已验证：" + subj,
                        rec["answer"] + " ｜ " + "；".join(rec.get("constraints", [])),
                        ["符号推理", rec["kind"]], "推理", salience=4)
                    # ② 写入结构化解库（solution_id 便于溯源 / 图库扩展）
                    if _sol_path:
                        os.makedirs(os.path.dirname(_sol_path), exist_ok=True)
                        with open(_sol_path, "a", encoding="utf-8") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                except Exception:
                    pass

            SY.set_memory_sink(_sink)
        except Exception:
            pass
        CompanionWindow._sym_sink_ready = True

    # ---------- v0.27.1 真实系统操作：授权确认 + sysops 执行 ----------
    def _symbolic_guard(self, text: str, reply: str) -> str:
        """v0.28.0 符号推理层·矛盾检测：回复里的数值若与确定性解冲突，就地补上更正值。

        这是「向量循环 ↔ 符号推理层」闭环的最后一道关：
          · 正常情况下，符号结论已经在系统提示里（memrouter 复杂型通路），模型会直接用；
          · 这里只兜住"模型仍然答错"的少数情况 —— 代价是 0.1ms，不是 30 秒的思考链。
        """
        if not reply:
            return reply
        try:
            import symbolic as SY
            if not CompanionWindow._sym_sink_ready:
                self._register_symbolic_sink()
            fix = SY.friendly_correction(text, reply)
            SY.writeback(text, reply)   # 闭环第三步：已验证事实沉淀进记忆/向量循环
        except Exception as ex:
            logging.debug("symbolic guard 跳过: %s", ex)
            return reply
        if not fix:
            return reply
        logging.info("symbolic guard：检出矛盾并更正（%s）", text[:40])
        return reply.rstrip() + "\n\n" + fix

    #: 宣称"已经执行了动作"的动词
    #: 动词前允许很短的间隔（"已经**把文档**保存到…"），但：
    #:   · 间隔限 ≤6 字，避免"我已经把方案写清楚然后…"这类长句被误伤；
    #:   · 动词后 `(?!过)` —— "打开过/生成过"说的是**过去经历**，不是宣称
    #:     刚执行完，必须排除（否则会误拦"我之前打开过这个文件"）。
    _RE_ACT_WORD = re.compile(
        r"已(?:经)?(?:帮你|为你|给我|把|将)?"
        r"[\u4e00-\u9fa5A-Za-z0-9_./:：]{0,6}?"
        r"(?:生成|创建|写好|写入|保存|存放|运行|执行|打开|启动|做成|做好|搞定|落盘)"
        r"(?!过)")
    #: 交付物 / 媒介词 —— 必须与动作词**同句**出现，
    #: 否则"已生成好的思路"这类正常表达会被误伤
    _RE_ACT_MEDIA = re.compile(
        r"文件|文档|图片|图像|视频|音频|PPT|pptx|Word|docx|Excel|xlsx|表格|"
        r"网页|页面|浏览器|代码|脚本|程序|文件夹|桌面|路径|安装包|压缩包|目录",
        re.I)
    #: 出现任意一条都算"这一轮真的执行过东西"
    _ACT_KINDS = ("genfile", "open_browser", "open_path", "open_app",
                  "run_script", "run_project", "delete", "clean_junk",
                  "web_search")

    def _claims_execution(self, reply: str) -> str:
        """回复里是否宣称"已经真实执行了动作"（返回命中那句，否则空串）。

        只认「动作词 + 媒介词**同句**出现」，避免误伤"已生成好的方案"这类
        普通表达。按铁律：拿不准就放行 —— **误拦比漏拦糟得多**。
        """
        for seg in re.split(r"[。！？!?\n；;]", reply or ""):
            if self._RE_ACT_WORD.search(seg) and self._RE_ACT_MEDIA.search(seg):
                return seg.strip()[:60]
        return ""

    def _guard_say(self, text: str, reply: str, kind: str, what: str) -> str:
        """记拦截台账 + 给更正话术（**不断言"我骗你"，只说查不到记录**）。"""
        import sysops as SYS
        SYS.ledger("honesty_guard", (text or "")[:80],
                   "回复宣称%s但台账无真实记录" % kind,
                   {"ok": False, "reason": "拦截编造，强制改口"})
        return ("⚠️ 更正：我刚才差点说错话——**我并没有真的%s任何东西**"
                "（执行台账里查不到真实操作记录，我不装做完了）。\n"
                "要真动手的话，说一声「现在就做」，我会真正执行并把结果给你看。"
                "你也可以说「看看操作台账」核验我。" % what)

    def _honesty_guard(self, text: str, reply: str) -> str:
        """防幻觉守卫：回复宣称"已执行/已完成"，但执行台账里没有这段时间的
        真实成功记录 → 强制改口，绝不放过"嘴上完成"。

        v0.27.2 只覆盖"已删除/已清理"；v0.30.2 扩展到"已生成/已打开/已运行/
        已保存"—— 真机实锤：普通聊天里模型说"✅ 已生成并打开浏览器"，
        而 chat 栏目的工具只有 recall/verify/learn/mood，**根本没有执行手段**，
        电脑上也没有任何产物。提示词早就写了纪律，模型照样编 → 必须程序护栏。
        """
        import sysops as SYS
        if not reply:
            return reply
        # —— 删除 / 清理类（原有逻辑）——
        if re.search(r"已(经)?删除|删掉[了过]|删除成功|已清除|清除(完毕|成功)|"
                     r"清理(完成|完毕|成功)|已清理", reply):
            if SYS.ledger_recent(("delete", "clean_junk"), seconds=600):
                return reply                       # 有真账，放行
            return self._guard_say(text, reply, "已删除/清理", "删除或清理")
        # —— 执行类（v0.30.2 新增）——
        if self._claims_execution(reply):
            if SYS.ledger_recent(self._ACT_KINDS, seconds=600):
                return reply                       # 有真账（真生成/真打开/真运行）
            return self._guard_say(text, reply, "已生成/打开/运行", "生成或打开")
        return reply

    # ---------- v0.29：权限三档 ----------
    def _perm_level(self) -> str:
        """当前权限档位。配置脏值一律归一到「安全」（fail-safe）。"""
        try:
            return PERM.normalize_level(self.cfg.get("perm_level"))
        except Exception:
            return "safe"

    def _perm_audit(self, title: str, how: str, risk: str):
        """自动放行的操作要留痕 —— 「完全访问」不等于「无记录」。

        刻意只落日志、不弹窗：这是事后追查用的，不该打断当前操作。
        """
        try:
            import logging
            logging.info("[perm] %s | risk=%s | %s", how, risk, title)
        except Exception:
            pass

    def _ui_confirm(self, title: str, body: str, timeout: int = 90,
                    risk: str = "high") -> bool:
        """worker 线程安全地弹授权确认框；UI 线程直接弹。
        v0.27.2 修复：旧版用模态 QMessageBox.question（queued signal 驱动），
        真机上主线程未及时处理时 Event 干等 180 秒超时→被当成"用户点了取消"，
        台账已实锤（两次 delete_rejected 与请求间隔恰好 180s×2）。
        新版：①非模态+置顶+自动激活的确认窗，主线程绝不阻塞；
              ②超时如实区分"没等到确认"与"用户点了否"，并记台账；
              ③user 未确认一律不动手（fail-safe 不变）。

        v0.29 权限三档闸门：档位足够高时**直接放行**、不再打扰用户。
        ⚠ risk 默认 "high"（必须确认）—— 所以没标注风险的老调用点行为不变。
        三档行为见 desktop/permission.py 的矩阵（14 项自检钉死）。"""
        try:
            if PERM.decide(self._perm_level(), risk) == "allow":
                self._perm_audit(title, "auto-allow", risk)
                return True
        except Exception as _ge:
            # 闸门自身出错 → 继续走原确认流程（fail-safe，方向永远是"不动手"）。
            # 但**必须留痕**：否则"完全访问档还在弹窗"这种症状只能靠猜
            # （实测踩过：提取测试时 PERM 不在命名空间，NameError 被静默吞掉）。
            self._perm_audit(title, "gate-error: %s: %s" % (type(_ge).__name__, _ge), risk)
        import threading as _th
        from qt_compat import QtWidgets          # v0.27.4：项目惯例——用到 QtWidgets 必须局部导入
        if _th.current_thread() is _th.main_thread():
            ret = QtWidgets.QMessageBox.question(
                self, title, body,
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
                QtWidgets.QMessageBox.No)
            return ret == QtWidgets.QMessageBox.Yes
        self._confirm_ok = False
        self._confirm_shown = False
        self._confirm_timeout = False
        self._confirm_ev = _th.Event()
        self.ask_confirm.emit(title, body)
        got = self._confirm_ev.wait(timeout=timeout)
        if not got:
            self._confirm_timeout = True
            # 弹窗可能还开着：关掉它，避免用户之后点击产生"幽灵确认"
            try:
                self._ui(self._close_stale_confirm)
            except Exception:
                pass
            return False
        self._confirm_timeout = False
        return self._confirm_ok

    def _close_stale_confirm(self):
        box = getattr(self, "_confirm_box", None)
        if box is not None:
            try:
                box.finished.disconnect()
            except Exception:
                pass
            box.close()
            self._confirm_box = None

    def _on_ask_confirm(self, title: str, body: str):
        from qt_compat import QtWidgets          # v0.27.4：修确认窗不显示（NameError 崩掉）
        self._confirm_shown = True
        box = QtWidgets.QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(body)
        box.setIcon(QtWidgets.QMessageBox.Question)
        box.setWindowFlags(box.windowFlags() | Qt.WindowStaysOnTopHint)
        box.setModal(False)                    # 非模态：绝不卡主线程
        yes = box.addButton("确认执行", QtWidgets.QMessageBox.YesRole)
        box.addButton("取消", QtWidgets.QMessageBox.NoRole)
        box.finished.connect(lambda: self._confirm_done(
            box.clickedButton() is yes))
        self._confirm_box = box
        box.show()
        box.adjustSize()
        # v0.27.4：确保确认窗真的落在屏幕可见处——
        # 旧版可能因父窗口被最小化/移出屏幕而不显示（用户以为"没弹窗"）
        try:
            scr = QtWidgets.QApplication.primaryScreen()
            if self.isVisible() and not self.isMinimized():
                c = self.frameGeometry().center()
            elif scr is not None:
                c = scr.availableGeometry().center()
            else:
                c = box.frameGeometry().center()
            box.move(max(0, c.x() - box.width() // 2),
                     max(0, c.y() - box.height() // 2))
        except Exception:
            pass
        box.raise_()
        box.activateWindow()

    def _confirm_done(self, ok: bool):
        self._confirm_ok = ok
        self.confirm_closed.emit()

    def _on_confirm_closed(self):
        if self._confirm_ev is not None:
            self._confirm_ev.set()

    def _confirm_timeout_flag(self) -> bool:
        return bool(getattr(self, "_confirm_timeout", False))

    def _confirm_refuse_reply(self, verb: str) -> str:
        """v0.27.2 诚实话术：确认框没成，如实区分"超时没等到"与"你点了取消"。
        （修复真机痛点：旧版把 180 秒超时也说成"你点了取消"，冤枉用户）"""
        if self._confirm_timeout_flag():
            return ("我弹出了确认窗，但在等待时间内没等到你的确认——按安全规则"
                    f"我什么都没{verb}，对象原样保留。需要的话再说一次"
                    f"（例如「删除桌面上的 XX」），确认窗会重新弹出，"
                    f"留意一下屏幕（可能在任务栏闪烁）。")
        return f"好，你点了取消——我什么都没{verb}，原样保留。"

    def _confirm_refuse_reason(self) -> str:
        return ("确认窗超时未应答（用户可能没看到弹窗）"
                if self._confirm_timeout_flag() else "用户在确认框点了否")

    def _sysops_del(self, path: str, why: str = "用户请求删除") -> str:
        """删除一条路径：护栏→授权→真删→如实回话。回复全部基于真实结果。"""
        import sysops as SYS
        if not (path or "").strip():
            # v0.27.5：没认出目标时，把桌面图片**列出来给用户挑**（只读展示、绝不动手）
            _hint = ""
            try:
                _imgs = SYS.list_desktop_images()
                if _imgs:
                    _names = "\n".join("  · " + os.path.basename(p) for p in _imgs[:15])
                    _hint = ("\n\n📋 桌面上现在有 %d 张图片，你要删哪张？"
                             "把**完整文件名**发我即可（例如「删除 %s」）：\n%s"
                             % (len(_imgs), os.path.basename(_imgs[0]), _names))
                    if len(_imgs) > 15:
                        _hint += "\n  …还有 %d 张" % (len(_imgs) - 15)
            except Exception:
                pass
            return ("我想真动手帮你删，但没从你的话里认出**确切是哪一张 / 哪一个**——"
                    "我不猜，更不会装删过了。也可以把文件用 @ 引用拖进输入框，"
                    "或说「删除桌面上 XX 文件夹」。" + _hint)
        d = SYS.describe(path)
        if not d["exists"]:
            return (f"我找不到 `{path}`——它可能已经被删了，或者名字有出入。"
                    f"你可以把文件夹拖进输入框（@ 引用），我按确切路径再删一次。")
        if SYS.is_forbidden(path) or SYS.inside_forbidden(path):
            probe = SYS.delete_path(path, allow_nonempty=False, why=why)
            return ("🛡 这个我不能删：" + probe["reason"])
        if d["kind"] == "dir" and not d["empty"]:
            body = (f"确认删除「{os.path.basename(path) or path}」吗？\n\n"
                    f"位置：{path}\n内容：{d['n_files']} 个文件、{d['n_dirs']} 个子文件夹"
                    f"（共 {SYS._human(d['size'])}）\n\n"
                    f"删除后移入回收站，可以恢复。")
            if not self._ui_confirm("删除确认（非空）", body):
                SYS.ledger("delete_rejected", path, why, {"ok": False,
                           "reason": self._confirm_refuse_reason()})
                return self._confirm_refuse_reply("删")
            r = SYS.delete_path(path, allow_nonempty=True, why=why)
        else:
            body = (f"删除「{os.path.basename(path) or path}」"
                    f"（{'空文件夹' if d['kind'] == 'dir' else '文件'}）？"
                    f"\n位置：{path}\n删除后移入回收站，可以恢复。")
            # 走回收站、可恢复 → 中风险：标准档以上不再打扰
            if not self._ui_confirm("删除确认", body, risk="med"):
                SYS.ledger("delete_rejected", path, why, {"ok": False,
                           "reason": self._confirm_refuse_reason()})
                return self._confirm_refuse_reply("删")
            r = SYS.delete_path(path, allow_nonempty=True, why=why)
        if r["ok"]:
            size = f"，释放 {SYS._human(r['size'])}" if r["size"] else ""
            return (f"✅ 真删掉了：移入回收站{size}。\n"
                    f"`{r['path']}`\n（桌面现在应该看不到了，可去回收站恢复）")
        return (f"❌ 没删成，我不说假话——真实原因：\n{r['reason']}")

    def _sysops_del_imgs(self, paths: list, why: str = "用户请求删除图片") -> str:
        """v0.27.2 删除桌面图片：先把要删的图列出来给你确认，确认后逐张真删
        （回收站），逐张记账，结果如实汇报。绝不先斩后奏。"""
        import sysops as SYS
        imgs = [p for p in (paths or []) if os.path.isfile(p)]
        if not imgs:
            return ("我在桌面（含 OneDrive 重定向的桌面）上没找到图片文件，"
                    "不装删过了。可以把具体图片路径发我，或说「删除桌面上的"
                    " XX 图片」点名要删的那张。")
        shown = "\n".join(f"- {os.path.basename(p)}  `{p}`" for p in imgs[:12])
        more = (f"\n- …等共 {len(imgs)} 张" if len(imgs) > 12 else "")
        body = (f"确认删除桌面上的 {len(imgs)} 张图片吗？\n\n{shown}{more}\n\n"
                f"删除后移入回收站，可以恢复。")
        if not self._ui_confirm("删除图片确认", body, risk="med"):
            SYS.ledger("delete_rejected", f"{len(imgs)} 张桌面图片", why,
                       {"ok": False, "reason": self._confirm_refuse_reason()})
            return self._confirm_refuse_reply("删")
        ok, fail = [], []
        for p in imgs:
            r = SYS.delete_path(p, allow_nonempty=True, why=why)
            (ok if r["ok"] else fail).append((p, r))
        lines = []
        if ok:
            lines.append(f"✅ 真删掉了 {len(ok)} 张（已移入回收站，可恢复）：")
            lines += [f"- {os.path.basename(p)}" for p, _ in ok[:10]]
            if len(ok) > 10:
                lines.append(f"- …等共 {len(ok)} 张")
        if fail:
            lines.append(f"❌ 有 {len(fail)} 张没删成，我不瞒你：")
            lines += [f"- {os.path.basename(p)}：{r['reason']}" for p, r in fail[:6]]
        return "\n".join(lines)

    def _sysops_del_paths(self, paths: list, why: str = "用户请求删除") -> str:
        """v0.27.3 多目标删除（空文件夹清单等）：先列清单 → 确认 → 逐个真删
        （回收站，可恢复）→ 逐个记账。绝不先斩后奏，绝不假报成功。"""
        import sysops as SYS
        items = [p for p in (paths or []) if p and os.path.exists(p)]
        if not items:
            return ("我没找到符合条件的文件夹/文件，不装删过了。"
                    "可以把它所在的路径发我（例：删除 D:\\临时\\xx 这个文件夹）。")
        shown = "\n".join(f"- {os.path.basename(p) or p}  `{p}`" for p in items[:15])
        more = (f"\n- …等共 {len(items)} 项" if len(items) > 15 else "")
        body = (f"确认删除这 {len(items)} 项吗？\n\n{shown}{more}\n\n"
                f"删除后移入回收站，可以恢复。")
        if not self._ui_confirm("删除确认", body):
            SYS.ledger("delete_rejected", f"{len(items)} 项", why,
                       {"ok": False, "reason": self._confirm_refuse_reason()})
            return self._confirm_refuse_reply("删")
        ok, fail = [], []
        for p in items:
            r = SYS.delete_path(p, allow_nonempty=True, why=why)
            (ok if r["ok"] else fail).append((p, r))
        lines = []
        if ok:
            lines.append(f"✅ 真删掉了 {len(ok)} 项（回收站，可恢复）：")
            lines += [f"- {os.path.basename(p) or p}" for p, _ in ok[:12]]
            if len(ok) > 12:
                lines.append(f"- …等共 {len(ok)} 项")
        if fail:
            lines.append(f"❌ 有 {len(fail)} 项没删成，我不瞒你：")
            lines += [f"- {os.path.basename(p) or p}：{r['reason']}"
                      for p, r in fail[:6]]
        return "\n".join(lines)

    def _dirscan_reply(self, target: str = "") -> str:
        """v0.27.3 目录体检：真扫描（不是模型编），给出结构化报告 + 下一步。

        真机痛点：说"分析一下桌面上的文件"，旧版要先读过文件才有上下文，
        否则只回一句"我还没读过文件呢"——等于没分析。现在：
        真扫 → 报数 → 给可执行下一步（删空文件夹/找大文件/找重复/分析表格）。
        """
        import sysops as SYS
        t = (target or "").strip()
        root = ""
        m = re.search(r"([A-Za-z]:[\\/][^\s，。；;）]+)", t)
        if m and os.path.isdir(m.group(1)):
            root = m.group(1)
        if not root and self.path_refs:
            for p in reversed(self.path_refs):
                if os.path.isdir(p):
                    root = p
                    break
        if not root:
            for c in (SYS.desktop_candidates() or
                      [os.path.join(os.path.expanduser("~"), "Desktop")]):
                if os.path.isdir(c):
                    root = c
                    break
        if not root:
            return "我没定位到要分析的目录，把路径发我（例：分析 D:\\项目 这个文件夹）。"
        rep = SYS.scan_dir_report(root)
        if not rep.get("ok"):
            return f"没扫成：{rep.get('error') or '未知原因'}"
        mb = rep["bytes"] / 1048576
        lines = [f"📁 目录体检（真扫描，不是估的）：`{root}`", "",
                 f"- 文件 **{rep['n_file']}** 个 / 文件夹 {rep['n_dir']} 个，"
                 f"合计 **{mb:.1f} MB**",
                 f"- 空文件夹 **{rep['empty_dirs']}** 个"]
        if rep["types"]:
            top = "、".join(f"{x['ext']}×{x['n']}({x['mb']}MB)"
                            for x in rep["types"][:6])
            lines.append(f"- 类型分布：{top}")
        if rep["biggest"]:
            lines.append("- 最大的文件：")
            lines += [f"    · {os.path.basename(b['path'])} — {b['mb']} MB"
                      for b in rep["biggest"][:3]]
        if rep["newest"]:
            lines.append("- 最近改动：")
            lines += [f"    · {os.path.basename(n['path'])} — {n['when']}"
                      for n in rep["newest"][:3]]
        if rep["dups"]:
            lines.append(f"- 疑似重复（同尺寸）{len(rep['dups'])} 组，例如：")
            for d in rep["dups"][:2]:
                lines.append(f"    · {d['size_mb']} MB {d['ext']}："
                             + "、".join(os.path.basename(f) for f in d["files"][:3]))
        if rep.get("error"):
            lines.append(f"- ⚠ {rep['error']}")
        lines += ["", "下一步我可以真干（说一句就行）：",
                  "1) **删掉这里的空文件夹**（我扫出来让你确认再删）",
                  "2) **按类型整理**（图片/文档/压缩包各归各的文件夹）",
                  "3) **找出大文件**看哪些该清",
                  "4) **分析这里的表格**（我找 .xlsx/.csv 真算给你看）"]
        return "\n".join(lines)

    def _websearch_reply(self, q: str) -> str:
        """v0.27.2 上网搜索：真的用系统默认浏览器打开搜索页（必应），
        开了就是开了，失败给真实原因。"""
        import sysops as SYS
        q = (q or "").strip()
        if not q:
            return ("想帮你上网搜，但没认出要搜什么。告诉我关键词，"
                    "比如「用浏览器搜索 PASM 猜想」。")
        r = SYS.open_web_search(q)
        if r["ok"]:
            return (f"✅ 已经用系统默认浏览器打开搜索页，搜索词：**{q}**。\n"
                    f"{r['url']}\n（如果浏览器没自动弹出来，说一声，"
                    f"我把链接复制给你手动打开）")
        return f"❌ 没能打开浏览器搜索，我不装搜过了——真实原因：{r['reason']}"

    def _sysinfo_reply(self) -> str:
        """整机现状（只读）：**真读机器**，取不到就如实说 —— 绝不编数。

        为什么单开这条：用户最常问的就是"我电脑卡不卡"。以前没这条意图，
        模型只能凭记忆答"应该还行"——那是编造，违反诚实守则第 5 条。
        """
        import sysops as SYS
        r = SYS.sysinfo()
        if not r["ok"]:
            return ("我如实说：这次没能读到系统信息——" + r["err"] +
                    "\n（这是只读查询，我没改你任何设置；也可以直接打开「任务管理器」看。）")
        lines = ["🖥 这台电脑现在是这样（只读查询，没改任何设置）："]
        if r["cpu_pct"] is not None:
            tail = "　← 偏高，后台有东西在忙" if r["cpu_pct"] >= 80 else ""
            lines.append("- CPU 占用：{0}%{1}".format(r["cpu_pct"], tail))
        if r["cores"]:
            lines.append("- CPU 核心：{0} 核".format(r["cores"]))
        if r["mem_total_gb"]:
            used = r["mem_total_gb"] - (r["mem_free_gb"] or 0)
            tail = "　← 内存吃紧，建议关掉不用的程序" \
                if (r["mem_pct"] or 0) >= 85 else ""
            lines.append("- 内存：{0:.1f} GB 里已用 {1:.1f} GB（{2}%）{3}".format(
                r["mem_total_gb"], used, r["mem_pct"], tail))
        for d in (r["disks"] or [])[:4]:
            lines.append("- {0} 盘：剩余 {1:.1f} GB / 共 {2:.1f} GB".format(
                d["drive"], d["free_gb"], d["total_gb"]))
        if r["uptime_h"] is not None:
            lines.append("- 已开机：{0:.1f} 小时".format(r["uptime_h"]))
        if r["os"]:
            lines.append("- 系统：{0}".format(r["os"]))
        lines.append("\n想看看是谁在占内存？说一句「看看进程」我就列给你。")
        return "\n".join(lines)

    def _procs_reply(self) -> str:
        """进程占用（只读）：按内存降序列出前几名。取不到就如实说。"""
        import sysops as SYS
        r = SYS.top_processes(8)
        if not r["ok"]:
            return ("我如实说：这次没读到进程列表——" + r["err"] +
                    "\n（只读查询；也可以直接打开「任务管理器」。）")
        lines = ["🧭 现在占用最高的几个程序（按内存排序，只读查询）："]
        for i, it in enumerate(r["items"], 1):
            cpu = "，累计 CPU {0:.0f}s".format(it["cpu_s"]) if it["cpu_s"] >= 0 else ""
            lines.append("{0}. {1} —— 内存 {2:.0f} MB{3}".format(
                i, it["name"], it["mem_mb"], cpu))
        lines.append("\n要关掉哪个可以说「关掉 xxx」：我会先弹确认，"
                     "而且系统关键进程（lsass/svchost/explorer 这类）我怎么都不会碰。")
        return "\n".join(lines)

    def _killproc_reply(self, name: str) -> str:
        """结束进程：**只读预检 → 弹授权确认 → 才动手**（不可逆操作的标准三步）。

        顺序刻意如此：预检不合格（名字非法/系统关键/根本没在跑）就**不弹窗**——
        不该为一个注定不会执行的动作去打断用户。
        """
        import sysops as SYS
        nm = (name or "").strip()
        if not nm:
            return "要结束哪个进程？把名字发我（例如 chrome、notepad）。"
        low = nm.lower()
        if low.endswith(".exe"):
            low = low[:-4]
        if low in SYS.PROTECTED_PROCS:
            return ("「{0}」是系统关键进程，结束它可能掉桌面 / 断网 / 蓝屏 —— "
                    "这条我怎么都不会做。要停后台服务请用「任务管理器 → 服务」。".format(nm))
        run = SYS.process_running(low)
        if run is None:
            return ("进程列表没取到（脚本被禁用或超时），没法安全判断，"
                    "这次我不动手 —— 拿不到状态时宁可不动。")
        if not run:
            return ("现在没有叫「{0}」的进程在运行（可能名字不对，或它已经关了）。"
                    "可以说「看看进程」我把正在跑的列给你。".format(nm))
        ok = self._ui_confirm(
            "结束进程",
            "要结束「{0}」吗？\n\n未保存的内容可能丢失，这一步不可撤销。\n"
            "（系统关键进程我不会碰；结束了也可以重新打开这个程序。）".format(nm),
            risk="high")
        if not ok:
            try:
                SYS.ledger("killproc", low, "用户未确认",
                           {"ok": False, "reason": "cancelled"})
            except Exception:
                pass
            return "好，这次没动它。"
        r = SYS.kill_process(nm)
        return ("✅ " if r["ok"] else "❌ ") + r["msg"]

    def _secchk_reply(self) -> str:
        """v0.27.2 电脑安全体检：只读检查 Windows Defender 真实状态，
        取不到就如实说，绝不编"没有病毒"。"""
        import sysops as SYS
        r = SYS.security_check()
        if not r["ok"]:
            return ("我如实说：这次没能完成安全体检——" + r["error"] +
                    "\n（这次检查是只读的，我没有改动你系统的任何设置；"
                    "想深度查杀可以在 Windows 安全中心跑一次完整扫描。）")
        lines = ["🖥 电脑安全体检完成（只读检查，我没改任何设置）："]
        lines.append("- 实时保护：" +
                     ("✅ 开启" if r["realtime"] else "❌ 关闭！建议到 Windows 安全中心立即开启"))
        lines.append("- 病毒库更新日期：" + (r["sig_date"] or "未知"))
        lines.append("- 防火墙：" + r["firewall_txt"])
        if r["threats"]:
            lines.append("- 最近威胁检出记录：")
            lines += [f"  - {t}" for t in r["threats"][:5]]
        else:
            lines.append("- 最近威胁检出记录：无（Defender 没有检出记录）")
        lines.append("\n说明：以上是 Windows 安全中心的真实状态快照；"
                     "「无检出记录」不等于绝对无病毒，需要深度查杀请在"
                     " Windows 安全中心跑完整扫描（我做不了全盘扫描，如实告知）。")
        return "\n".join(lines)

    def _sysops_clean(self) -> str:
        """清理垃圾：先扫描（只读）→ 报告 → 授权 → 真清 → 如实报告释放量。"""
        import sysops as SYS
        scan = SYS.scan_junk()
        if scan["total"] < 5 * 1024 * 1024:
            detail = "；".join(f"{l['label']} {l['human']}" for l in scan["locations"]) \
                if scan["locations"] else "没找到可清理的临时目录"
            return (f"我扫了一遍（只读，还没动手删任何东西）：可清理的临时文件总共只有 "
                    f"{scan['total_human']}——{detail}。\n"
                    f"不到 5MB，清了意义不大，我就不动手了。"
                    f"想要更彻底的话，可以用系统「存储感知」或磁盘清理。")
        detail = "\n".join(
            f"- {l['label']}：{l['human']}（{l['n_files']} 个文件）\n  `{l['path']}`"
            for l in scan["locations"])
        body = (f"扫描结果（我还没删任何东西）：\n\n{detail}\n\n"
                f"共 {scan['total_human']}。将只清理其中超过 2 天的旧文件"
                f"（正在用的不动，删不掉的会如实报告）。确认清理吗？")
        if not self._ui_confirm("清理确认", body, risk="med"):
            SYS.ledger("clean_rejected", "temp-whitelist", "清理确认未成",
                       {"ok": False, "reason": self._confirm_refuse_reason()})
            return self._confirm_refuse_reply("清")
        r = SYS.clean_junk(min_age_days=2.0)
        msg = (f"✅ 真清完了：删除 {r['deleted_files']} 个旧文件，"
               f"释放 {r['freed_human']}。\n"
               f"跳过正在使用的 {r['skipped_locked']} 个（删了会出事），"
               f"保留 2 天内新文件的 {r['skipped_new']} 个。")
        if r.get("remaining"):
            msg += ("\n⏳ 这轮时间预算用完还有剩余——临时文件夹很大，"
                    "说「继续清理」我就接着清一轮。")
        if r["errors"]:
            msg += "\n个别失败：" + "；".join(r["errors"])
        return msg

    def _cando_reply(self, text: str) -> str:
        """能力判定 broker 执行器（非预设机制）：
        判定 → 学过的用知识真做 / 不会的先上网学再真做 / 做不了说真话。
        难度自适应：易任务主体直做；难任务拆子步骤逐个执行+台账+主体复核。"""
        import sysops as SYS
        if _CD is None:
            return "（能力判定模块未加载）"
        verdict = _CD.assess(text, learned_search=knowledge.search_snippets)
        v = verdict["verdict"]
        if v == "NO":
            SYS.ledger("cando_no", text[:80], "能力边界", verdict)
            return ("这件事我确实做不了，不装能做——真实原因：\n"
                    + verdict["reason"] +
                    "\n（我的边界都记录在案，你也可以问「你有什么不能做的」）")
        if v == "CHAT":
            return None  # 理论到不了（路由已过滤），交给普通聊天
        diff = SYS.assess_difficulty(text)
        plan_note = ""
        # —— 难度自适应：复杂任务先拆子步骤（子智能体分工，主体逐个复核兜底）——
        sub_steps = []
        if diff["mode"] == "delegate":
            try:
                steps = PLN.make_plan(
                    text, llm_fn=lambda up, sp: self._brain(
                        up, system=sp, max_tokens=300, task="study"))
                if len(steps) >= 2:
                    sub_steps = [s.get("name") or s.get("desc") or str(s)
                                 for s in steps]
                    plan_note = ("这个任务有点复杂，我拆成了 " + str(len(sub_steps)) +
                                 " 个子步骤逐个干（每步做完我都会检查，出问题立刻停下说实话）：\n"
                                 + "\n".join(f"  {i}. {s}" for i, s in enumerate(sub_steps, 1))
                                 + "\n\n")
            except Exception:
                sub_steps = []
        # —— 破坏性操作：主体亲自执行前必须用户确认 ——
        if diff["destructive"] and diff["mode"] == "main_confirm":
            if not self._ui_confirm(
                    "危险操作确认",
                    f"这个请求包含删除/覆盖类动作：\n「{text[:120]}」\n\n"
                    f"我会亲自（不假手子智能体）写脚本并真跑，出错不搞无法恢复的默认行为。"
                    f"确认执行吗？"):
                SYS.ledger("cando_rejected", text[:80], "危险操作确认未成",
                           {"ok": False, "reason": self._confirm_refuse_reason()})
                return self._confirm_refuse_reply("执行")
        # —— 学过：用学到的真材实料写脚本 ——
        source_note = ""
        extra_ctx = ""
        if v == "LEARNED":
            extra_ctx = _CD.learned_context(verdict.get("learned"))
            source_note = "我将用自己学过的知识（" + \
                (verdict.get("learned") or [{}])[0].get("title", "知识库") + "）来真做：\n"
        elif v == "LEARN":
            # 不会 → 先上网自学（真抓真存），学完再试；学不会如实交代
            topic = re.sub(r"^(帮我|请|麻烦|去|给我)?(把|将)?", "",
                           (text or "").strip())[:20].strip(" ，。") or text[:20]
            learn_reply = self._agent_run(("weblearn", topic))
            if "没能从网上抓到" in learn_reply:
                SYS.ledger("cando_learn_fail", text[:80], "自学未果", {"ok": False})
                return ("我本来打算先上网自学这件事再真做，但这回没抓到可用资料。"
                        "我不装会——你可以：发我一个相关网页链接让我读，"
                        "或者换个更常见的说法，我再学一次。")
            extra_ctx = "我刚自学了《" + topic + "》，写脚本时用上学到的做法。"
            source_note = ("我刚上网自学了《" + topic + "》并已存入知识库，"
                           "现在用学到的真做：\n")
        # —— 真执行：写脚本 → 运行 → 验证 → 失败自动修复重试（_script_plan 闭环）——
        req = text + ("\n\n【自学知识上下文，写脚本时用上】\n" + extra_ctx) \
            if extra_ctx else text
        rep = self._script_plan(req, run=True)
        ok_run = rep and ("✅" in rep or "成功" in rep or "完成" in rep)
        SYS.ledger("cando_script", text[:80],
                   f"{verdict['verdict']} 模式={diff['mode']}",
                   {"ok": bool(ok_run), "report": rep[:200]})
        head = (plan_note or source_note or "")
        tail = "" if ok_run else \
            ("\n⚠️ 如实说：这轮没完全跑成。上面是我真实做到的部分和卡住的原因，"
             "你可以让我修一次，或者告诉我更多细节。")
        return head + (rep or "（没有产出）") + tail

    def _weather_reply(self, text: str) -> str:
        """实时天气：open-meteo 免 key，直接给出答案，不打开浏览器。"""
        try:
            import live_data as LD
            return LD.get_weather(text)
        except Exception as ex:
            return ("查天气失败了：" + str(ex) +
                    "\n（需要联网；若网络受限，我可以改用网页搜索帮你找天气。）")

    def _browser_reply(self, text: str) -> str:
        """浏览器自动化：Playwright 控制真实浏览器完成打开/搜索/填表/截图。"""
        try:
            import browser_agent as BA
            # 默认有头，让用户看到浏览器被操作；可在设置里设 browser_headless=true 转无头
            headless = bool(self.cfg.get("browser_headless", False))
            return BA.run_browser_task(text, headless=headless)
        except Exception as ex:
            return "浏览器操作失败了：" + str(ex)

    # ---------- v0.28.x：定时 / 邮件 / 日历 / 跨端 / 自进化 / 协作 的落地实现 ----------
    def _notify_chat(self, msg: str):
        """把一条**主动**消息（定时提醒 / 手机来消息）显示到聊天并存进历史。

        一律回到 UI 线程再动控件（后台线程只负责算，不碰界面）。
        """
        def _do():
            try:
                self._reveal(self.cfg["name"], msg)
            except Exception:
                logging.exception("notify reveal failed")
            try:
                self.history.append({"role": "assistant", "content": msg})
                self.history = self.history[-500:]
                self._persist_conv()
            except Exception:
                pass
        self._ui(_do)

    def _remind_reply(self, text: str, action: str = "add") -> str:
        """定时提醒：add 新建 / list 查清单 / del 取消。"""
        try:
            import scheduler as SCH
        except Exception as ex:
            return "定时模块没加载上（%s）。" % ex
        if action == "list":
            return SCH.list_text()
        if action == "del":
            kw = re.sub(r"(取消|删掉|删除|停止|不用|别|那个|这个|提醒|闹钟|定时|任务)", " ",
                        text or "").strip()
            job = SCH.find_job(kw)
            if not job:
                return ("没找到要取消的任务。你可以先说「我定了哪些提醒」，"
                        "我列出来给你挑；或者直接说「取消吃药提醒」。")
            SCH.remove_job(job["id"])
            return "✅ 已取消：%s" % SCH.describe(job)
        when, rest = SCH.parse_when(text or "")
        if not when:
            return ("我没听出这是什么时候。说得再具体点就行，例如：\n"
                    "· 「每天早上 8 点半提醒我吃药」\n"
                    "· 「每小时提醒我起来动一动」\n"
                    "· 「明天下午 3 点提醒我开会」\n"
                    "· 「每周一 9 点提醒我整理计划」")
        title = (rest or "").strip() or "提醒"
        # 话里还提到手机/微信 → 顺带把这个提醒推到手机（对齐跨端能力）
        _to_phone = re.search(r"手机|微信|钉钉|飞书|推送", text or "")
        if _to_phone:
            # 别把"推到我手机"这种话尾巴留进提醒标题里
            title = re.sub(r"[,，。;；]?\s*(把)?(结果|内容)?(推|发|送)(送)?(到|给|往)?"
                           r"[^,，。;；]{0,4}(手机|微信|钉钉|飞书|telegram)", " ", title or "")
            title = re.sub(r"[,，。;；]\s*$", "", title).strip() or "提醒"
        act = "notify" if _to_phone else "remind"
        job = SCH.add_job(title, when, action=act)
        tip = "，到点我会推到你手机" if act == "notify" else "，到点我会在聊天里提醒你"
        return ("⏰ 记下了：%s%s\n· 下次：%s\n\n想看看都定了什么，说「我定了哪些提醒」。"
                % (SCH.describe(job), tip, job.get("next_run") or "—"))

    def _mail_reply(self, text: str, action: str = "check") -> str:
        """邮件：check 看未读 / search 搜 / send 发。"""
        try:
            import connector_mail as MAIL
        except Exception as ex:
            return "邮件模块没加载上（%s）。" % ex
        if action == "check":
            n = 5
            m = re.search(r"(\d{1,2})\s*封", text or "")
            if m:
                n = max(1, min(20, int(m.group(1))))
            return MAIL.digest(n, unread_only=True, cfg=self.cfg)
        if action == "search":
            kw = re.sub(r"(帮我|请|给我|搜|找|查|一下|邮件|邮箱|里的|含|的)",
                        " ", text or "").strip()
            if not kw:
                return "你想搜什么关键词？说「搜一下含报价的邮件」这样就行。"
            return MAIL.search_text(kw, 5, cfg=self.cfg)
        addr = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text or "")
        if not addr:
            return ("要发邮件得先给我收件人地址。这样说就行：\n"
                    "「给 a@b.com 发邮件，主题是周报，内容是本周完成了三件事」")
        to = addr.group(0)
        subj = ""
        m = re.search(r"主题(?:是|为|：|:)\s*([^，。；\n]{1,40})", text or "")
        if m:
            subj = m.group(1).strip()
        body = ""
        m2 = re.search(r"内容(?:是|为|：|:)\s*(.+)$", text or "", re.S)
        if m2:
            body = m2.group(1).strip()
        if not body:
            return ("邮件正文还没给我 —— 补一句「内容是……」我就发。\n"
                    "（收件人：%s；主题：%s）" % (to, subj or "（待定）"))
        try:
            return MAIL.send(to, subj or ("来自 %s 的邮件" % self.cfg.get("name", "小U")),
                             body, cfg=self.cfg)
        except Exception as ex:
            return "📧 " + str(ex)

    def _cal_reply(self, text: str, action: str = "add") -> str:
        """日历：add 建日程 / list 看安排 / del 删。"""
        try:
            import connector_calendar as CAL
        except Exception as ex:
            return "日历模块没加载上（%s）。" % ex
        if action == "list":
            days = 7
            m = re.search(r"(\d{1,2})\s*天", text or "")
            if m:
                days = max(1, min(60, int(m.group(1))))
            return CAL.agenda_text(days)
        if action == "del":
            kw = re.sub(r"(取消|删掉|删除|日程|日历|安排|会议|约会|那个|这个)", " ",
                        text or "").strip()
            ok, msg = CAL.remove_event(kw)
            return msg
        ok, msg = CAL.add_from_text(text or "")
        return msg

    def _connector_test_reply(self, text: str) -> str:
        """第三方接入的连通性自测（v0.30.14）。

        为什么这步必须由**你**来点：我们没法替你联调（需要真实 App 凭据、
        后台订阅配置、公网/长连接开通）。这里用你填的凭据**真去连一次**，
        成不成如实报 —— 比"文档说支持"有用得多。
        """
        t = (text or "").lower()
        names = {"feishu": "飞书", "discord": "Discord",
                 "wechat": "微信", "webhook": "Webhook"}
        picks = []
        if any(k in t for k in ("飞书", "feishu", "lark")):
            picks.append("feishu")
        if "discord" in t:
            picks.append("discord")
        if any(k in t for k in ("微信", "wechat", "公众号", "企业微信")):
            picks.append("wechat")
        if "webhook" in t:
            picks.append("webhook")
        if not picks:
            picks = list(names)
        lines = ["🔎 **接入连通性测试**（用你配置里的凭据真连一次，不猜）"]
        for p in picks:
            try:
                mod = __import__("connector_" + p)
                if not mod.configured(self.cfg):
                    lines.append("\n【%s】未配置 —— 先去 ⚙ 设置 → 🔗 接入 填凭据"
                                 % names.get(p, p))
                    continue
                lines.append("\n【%s】\n%s" % (names.get(p, p),
                                             mod.test_connection(self.cfg)))
            except Exception as ex:                          # noqa: BLE001
                lines.append("\n【%s】测试失败：%s" % (names.get(p, p), ex))
        lines.append("\n（凭据只存在你自己机器上；保存后通道会热重启，不用重开应用）")
        return "\n".join(lines)

    def _remote_reply(self, text: str, action: str = "push") -> str:
        """跨端：push 把结果推手机 / status 看配置。"""
        try:
            import remote_bridge as RB
        except Exception as ex:
            return "跨端模块没加载上（%s）。" % ex
        if action == "status":
            return RB.status_text(self.cfg)
        body = str(getattr(self, "_last_reply_text", "") or "").strip()
        if not body:
            body = re.sub(r"(帮我|请|把|刚才|的|结果|内容|推|发|送|到|给|往|"
                          r"手机|微信|钉钉|飞书|一下)", " ", text or "").strip()
        if not body:
            body = (text or "").strip()
        try:
            return RB.notify(body, title="来自 %s" % self.cfg.get("name", "小U"),
                             cfg=self.cfg)
        except Exception as ex:
            return "📡 " + str(ex)

    def _evolve_reply(self, text: str, action: str = "save") -> str:
        """自进化：save 把刚才的活沉淀成技能 / suggest 给建议。"""
        try:
            import self_evolve as SE
        except Exception as ex:
            return "自进化模块没加载上（%s）。" % ex
        if action == "suggest":
            return SE.suggest_text()
        brain = None
        try:
            brain = self._team_llm()          # 有模型就用它润色，没有就退回模板
        except Exception:
            brain = None
        ok, msg = SE.from_task(brain=brain)
        return msg

    def _team_reply(self, text: str, action: str = "board") -> str:
        """协作层：board 看任务板 / auto 自动组队并执行。"""
        if TEAM is None:
            return "团队模块没加载上。"
        if action == "board":
            try:
                return TEAM.board_text()
            except Exception as ex:
                return "读任务板失败：%s" % ex
        goal = re.sub(r"(自动组队|让团队自动|团队自动|自动安排团队|多智能体协作|"
                      r"组个队|拉个团队|自动分工|帮我|请|一下)", " ", text or "").strip()
        goal = goal or (text or "").strip()
        if not goal:
            return "要让团队做什么？说一句目标就行，例如「做个记账小工具」。"
        logs: list = []
        try:
            res = TEAM.run_collaboration(goal, self._team_llm(),
                                         progress=lambda m: logs.append(str(m)))
        except Exception as ex:
            return "自动组队执行失败：%s" % ex
        out = [TEAM.collab_text(res)]
        for i, s in enumerate(res.get("steps") or [], 1):
            out.append("\n**%d. %s**\n%s"
                       % (i, s.get("role") or "", str(s.get("text") or "")[:1200]))
        try:
            out.append("\n\n📋 任务板：\n" + TEAM.board_text(10))
        except Exception:
            pass
        return "".join(out)

    def _start_background_jobs(self):
        """v0.28.x：拉起**纯后台**的定时轮询与跨端消息接收。

        守护线程 + 休眠式轮询（30 秒醒一次），不跑任务时 CPU 占用为 0，
        关窗口自动结束；刻意不在启动关键路径上做事，不拖慢开机。
        """
        try:
            import scheduler as SCH
            if not SCH.running():
                SCH.start(self._on_remind_fire)
        except Exception:
            logging.exception("scheduler start failed")
        try:
            import remote_bridge as RB
            if str(self.cfg.get("bridge_tg_token") or "").strip():
                RB.reply_loop(self._bridge_handle, poll=6)
        except Exception:
            logging.exception("bridge start failed")
        # v0.30.14：第三方接入（飞书 / Discord / 微信 / Webhook）—— 四个通道都是双向的：
        # 入站消息统一交给 `_bridge_handle`（与手机端走同一条管线），结果回给同一个会话。
        # 没配的通道**安静跳过**（不抛、不弹窗、不刷红日志）。
        try:
            import connector_hub as HUB
            if getattr(self, "_hub", None) is None:
                self._hub = HUB.ConnectorHub(
                    cfg=self.cfg, handler=self._bridge_handle,
                    on_log=lambda m: logging.info("[connector] %s", m))
            got = self._hub.start_all()
            _live = [k for k, v in (got or {}).items() if v]
            if _live:
                logging.info("第三方接入已启动：%s", "、".join(_live))
                self._notify_chat("🔗 第三方接入已就绪：%s"
                                  % "、".join(_live))
        except Exception:
            logging.exception("connector hub start failed")
        self._start_memory_replay()
        self._init_memory_backends()
        # v0.30.11：把老位置散落的产物收进统一工作根（只做一次；失败静默、原件保留）
        self._ws_auto_migrate()

    def _init_memory_backends(self):
        """v0.29.0（P1.1）：按配置启用**语义嵌入后端**（默认不启用）。

        默认刻意关着——内置哈希向量零依赖、必定可用；语义后端要真连上 Ollama，
        模型没装/端口不通时必须能安静退回。**增强项失败绝不拖死核心功能**，
        这是本项目一贯的"双档设计"（full ↔ teaching）。启用方式：配置里加
        embed_model（如 nomic-embed-text / bge-m3），可选 ollama_url。

        注：多模态（图/音/视频）编码器是 P3 的开放挂点，接口为
        memory_layers.set_modality_embedder(fn)，fn(path, kind) -> (vec, vk)。
        未注册时多模态条目退化为"按文本/文件名可召回"，不会崩溃。
        """
        try:
            import memvec as MV
            m = str(self.cfg.get("embed_model") or "").strip()
            if m:
                MV.set_backend(m, str(self.cfg.get("ollama_url") or ""))
                logging.info("语义嵌入后端已配置：%s", m)
        except Exception:
            logging.debug("语义嵌入后端配置失败", exc_info=True)

    def _start_memory_replay(self, every: int = 900):
        """v0.29.0（P2.5）记忆主动重放：低峰复盘，**纯后台**不占资源。

        对标 Letta 的 sleep-time compute / 海马体重放：把"今天发生的"沉淀成
        "长期知道的"。三件事——巩固长期印象、抽高置信事实入事实层、计划类事实
        到期自更新（"要去北京"→"去过北京"）。

        刻意默认**不调 LLM**（纯本地规则）：这样它既不会与前台对话抢模型，
        也不会让电脑变卡——符合"自检/自测/自修复链路纯后台运行、只暴露查看状态"
        的产品要求。线程 15 分钟醒一次，空闲才干活，关窗自动结束。
        """
        if getattr(self, "_replay_started", False):
            return
        self._replay_started = True

        def _loop():
            while True:
                time.sleep(max(60, int(every)))
                try:
                    if getattr(self, "busy", False) or getattr(self, "_turns", None):
                        continue                       # 正在用，让路
                    r = ML.replay(limit=12)
                    if r.get("consolidated") or r.get("facts_in") or r.get("expired"):
                        logging.info("memory replay: %s", r)
                    ML.flush()
                except Exception:
                    logging.debug("memory replay 失败", exc_info=True)

        threading.Thread(target=_loop, name="pasm-mem-replay", daemon=True).start()

    def _on_remind_fire(self, job):
        """定时任务到点（在后台线程被调用）：本地提醒，或按配置推到手机。"""
        def _work():
            title = str(job.get("title") or "定时任务")
            act = str(job.get("action") or "remind")
            if act == "notify":
                try:
                    import remote_bridge as RB
                    RB.notify("⏰ " + title, title="PASM 提醒", cfg=self.cfg)
                except Exception as ex:
                    self._notify_chat("⏰ 提醒到点了，但推到手机失败：%s\n（本地提醒：%s）"
                                      % (ex, title))
                    return
            self._notify_chat("⏰ **定时提醒**：%s" % title)
        threading.Thread(target=_work, name="pasm-remind", daemon=True).start()

    def _bridge_handle(self, text: str) -> str:
        """手机发来的消息（后台线程）→ 投到 UI 线程走正常管线，回复由 _finish 推回手机。

        这里**不直接在后台线程跑模型/碰控件** —— 只把话塞进输入框点发送，
        保证手机端与桌面端行为完全一致。
        """
        def _do():
            try:
                self.input.setPlainText(text)
            except Exception:
                pass
            self._bridge_pending = True
            self.send()
        try:
            self._ui(_do)
            return "（已交给桌面端处理，稍候把结果推给你）"
        except Exception as ex:
            return "处理失败：%s" % ex

    def _agent_run(self, agent):
        kind = agent[0]
        if kind == "sysops_del":          # v0.27.1 真实删除（回收站+授权+台账）
            return self._sysops_del(agent[1], agent[2] if len(agent) > 2 else "用户请求删除")
        if kind == "sysops_del_imgs":     # v0.27.2 真实删图片（列出+批量授权+台账）
            return self._sysops_del_imgs(
                agent[1], agent[2] if len(agent) > 2 else "用户请求删除图片")
        if kind == "sysops_del_paths":    # v0.27.3 多目标删除（空文件夹清单等）
            return self._sysops_del_paths(
                agent[1], agent[2] if len(agent) > 2 else "用户请求删除")
        if kind == "dirscan":             # v0.27.3 目录体检（真扫描，给下一步）
            return self._dirscan_reply(agent[1] if len(agent) > 1 else "")
        if kind == "accent_status":       # v0.27.3 我学到了你什么说话习惯
            try:
                import accent as _AC
                return _AC.status_text()
            except Exception as ex:
                return f"语言画像读取失败：{ex}"
        if kind == "accent_clear":        # v0.27.3 别学我说话
            try:
                import accent as _AC
                _AC.clear()
                return "好，我忘掉跟你学的口音了，回到本来怎么说话就怎么说话。"
            except Exception as ex:
                return f"没能清掉：{ex}"
        if kind == "micdiag":             # v0.27.4 语音自检（输入 + 朗读 全链路）
            out = []
            try:
                import asr as _asr_m
                out.append("🎤 **语音输入自检**\n" + _asr_m.diagnose())
            except Exception as ex:
                out.append(f"语音输入自检跑失败了：{ex}")
            try:
                import tts as _tts_m
                out.append(_tts_m.tts_diag())
            except Exception as ex:
                out.append(f"朗读自检跑失败了：{ex}")
            return "\n\n".join(out)
        if kind == "websearch":           # v0.27.2 真实动作：打开浏览器搜索
            return self._websearch_reply(agent[1] if len(agent) > 1 else "")
        if kind == "weather":             # v0.28.x 实时天气（open-meteo，免 key，直接给答案不打开浏览器）
            return self._weather_reply(agent[1] if len(agent) > 1 else "")
        if kind == "browser":             # v0.28.x 浏览器自动化（Playwright）
            return self._browser_reply(agent[1] if len(agent) > 1 else "")
        # v0.28.x 新增能力：定时 / 邮件 / 日历 / 跨端推送 / 自进化 / 协作层
        if kind == "remind_add":
            return self._remind_reply(agent[1] if len(agent) > 1 else "", "add")
        if kind == "remind_list":
            return self._remind_reply("", "list")
        if kind == "remind_del":
            return self._remind_reply(agent[1] if len(agent) > 1 else "", "del")
        if kind == "mail_check":
            return self._mail_reply(agent[1] if len(agent) > 1 else "", "check")
        if kind == "mail_search":
            return self._mail_reply(agent[1] if len(agent) > 1 else "", "search")
        if kind == "mail_send":
            return self._mail_reply(agent[1] if len(agent) > 1 else "", "send")
        if kind == "cal_add":
            return self._cal_reply(agent[1] if len(agent) > 1 else "", "add")
        if kind == "cal_list":
            return self._cal_reply(agent[1] if len(agent) > 1 else "", "list")
        if kind == "cal_del":
            return self._cal_reply(agent[1] if len(agent) > 1 else "", "del")
        if kind == "remote_push":
            return self._remote_reply(agent[1] if len(agent) > 1 else "", "push")
        if kind == "remote_status":
            return self._remote_reply("", "status")
        if kind == "connector_status":     # v0.30.14 第三方接入状态（四个通道一起看）
            try:
                import connector_hub as HUB
                hub = getattr(self, "_hub", None) or HUB.ConnectorHub(cfg=self.cfg)
                return hub.status_text()
            except Exception as ex:                          # noqa: BLE001
                return "接入状态读不出来：%s" % ex
        if kind == "connector_test":       # v0.30.14 真连一次（用你自己的凭据）
            return self._connector_test_reply(agent[1] if len(agent) > 1 else "")
        if kind == "evolve_save":
            return self._evolve_reply(agent[1] if len(agent) > 1 else "", "save")
        if kind == "evolve_suggest":
            return self._evolve_reply("", "suggest")
        if kind == "team_board":
            return self._team_reply("", "board")
        if kind == "team_auto":
            return self._team_reply(agent[1] if len(agent) > 1 else "", "auto")
        if kind == "secchk":              # v0.27.2 真实体检：安全状况（只读）
            return self._secchk_reply()
        if kind == "sysinfo":             # v0.30.7 整机现状（只读，真读机器）
            return self._sysinfo_reply()
        if kind == "procs":               # v0.30.7 进程占用（只读）
            return self._procs_reply()
        if kind == "killproc":            # v0.30.7 结束进程（先预检 → 弹授权 → 才动手）
            return self._killproc_reply(agent[1] if len(agent) > 1 else "")
        if kind == "sysops_clean":        # v0.27.1 真实清垃圾（扫描→确认→清理）
            return self._sysops_clean()
        if kind == "sysops_report":       # 查最近系统操作台账（可验证我说没说谎）
            import sysops as SYS
            rows = SYS.ledger_tail(8)
            if not rows:
                return "我还没有执行过任何系统操作，台账是空的。"
            lines = [f"- {r['t']} [{r['action']}] {r['target']} → "
                     f"{'✅ 成功' if r.get('ok') else '❌ ' + (r.get('reason') or '未执行')}"
                     for r in reversed(rows)]
            return ("最近 8 条真实系统操作记录（存在本地台账，随时可查）：\n" +
                    "\n".join(lines))
        if kind == "cando":               # v0.27.1 能力判定：能做/学过做/学着做/说不能
            return self._cando_reply(agent[1])
        if kind == "askhelp":
            return self._askhelp_reply(agent[1])
        if kind == "tableana":
            return self._table_analysis_reply(agent[1] if len(agent) > 1 else "")
        if kind == "skill":
            name = agent[1] if len(agent) > 1 else ""
            req = agent[2] if len(agent) > 2 else ""
            return self._skill_run(name, req)
        if kind == "ad":                  # v0.30.13：广告 = 方案 + 主视觉（≠ 图像栏目）
            return self._create_ad(agent[1] if len(agent) > 1 else "")
        if kind == "image":
            return self._create_image(agent[1] if len(agent) > 1 else "")
        if kind == "video":
            return self._create_story("video", agent[1] if len(agent) > 1 else "")
        if kind == "manga":
            # v0.18.0：漫剧走分步专业流程（企划→改镜→出图→配音→合成；"一条龙"仍可一键）
            return self._manga_dispatch(agent[1] if len(agent) > 1 else "")
        if kind == "image_pack":
            # 引擎未接入且用户点了“取消”时的降级：给提示词包（不假装已出图）
            prompt = agent[1] if len(agent) > 1 else ""
            ans = self._brain(
                prompt,
                system="你是文生图提示词专家。把用户想要的画面拆成「主体 / 风格 / "
                       "构图 / 光线 / 镜头 / 氛围」逐条给出中文提示词并附一段英文精简版，"
                       "开头一句话说明：接入出图引擎后可直接在本应用里生成（设置→创作引擎），"
                       "目前先把提示词给你。绝不假装已经生成图片。",
                max_tokens=1600, task="skill")
            return ("🖼 图像模式：还没接入出图引擎，我先把「可直接出图」的提示词包写好"
                    "（随时可在 设置→创作引擎 接入，接入后同样的话会直接出图）：\n\n"
                    + ans)
        if kind == "readfile":
            path = agent[1]
            want = agent[2] if len(agent) > 2 else ""
            real = path
            if not os.path.exists(real):
                # 试试绝对化 / 用户目录展开 / 找文件兜底
                cand = os.path.expanduser(real.replace("~", "~"))
                if not os.path.exists(cand) and not os.path.isabs(real):
                    cand = os.path.join(os.path.expanduser("~"), real)
                if os.path.exists(cand):
                    real = cand
                else:
                    kw = os.path.basename(real).split(".")[0][:20]
                    folder = os.path.dirname(real)
                    hits = AT.find_files(kw, folder if os.path.isdir(folder) else "")
                    if hits:
                        real = hits[0]
            content = AT.peek_file(real)
            if not content:
                return (f"我试着读 `{path}` 但没读到内容。请确认：\n"
                        f"1) 路径写全（例如 D:\\文档\\方案.md）\n"
                        f"2) 或把文件放到容易找的位置后说「帮我找 {os.path.basename(path)}」\n"
                        f"3) 目前支持 txt/md/json/py/csv/docx/xlsx/html 等文本与办公文档。")
            # 进入工作上下文 → 后续可关联
            self.work_ctx[real] = content[:4000]
            self.work_ctx = dict(list(self.work_ctx.items())[-3:])
            self._touch_ref(real)
            if want == "分析":
                note = "\n".join(content.splitlines()[:150])
                ans = self._brain(
                    "请分析下面这份材料/方案，给出：1) 核心内容概括 2) 亮点 3) 风险或问题 "
                    "4) 改进建议。分条列点，语言简明。\n\n材料内容：\n" + note,
                    system="你是擅长文档与方案分析的 AI 助手，分析要具体、可执行。")
                return "分析《" + os.path.basename(real) + "》：\n\n" + ans
            head = "\n".join(content.splitlines()[:40])
            more = f"\n……（已读全文 {len(content)} 字，内容已进入我的工作上下文，你可以继续问我关于它的任何问题）" \
                if len(content) > 2000 else ""
            return (f"✅ 读到了《{os.path.basename(real)}》：\n\n{head}{more}\n\n"
                    f"要我对它做什么？可以说：分析一下 / 总结要点 / 结合它帮我写个脚本…")
        if kind == "openapp":
            msg = AT.open_app(agent[1])
            if msg.startswith("!"):
                return msg[1:]
            return "✅ " + msg
        if kind == "openpath":
            p = agent[1]
            opened = AT.open_path(p)
            if opened.startswith("!"):
                return f"没能打开 `{p}`：{opened[1:]}"
            self._touch_ref(opened)
            what = "文件夹" if os.path.isdir(opened) else os.path.basename(opened)
            return (f"✅ 已打开「{what}」：`{opened}`\n\n"
                    f"需要的话我还能读里面的内容：`分析一下 {opened}` 或 `读一下 {opened}`。")
        if kind == "readfolder":
            p = agent[1]
            want = agent[2] if len(agent) > 2 else ""
            info = AT.read_folder(p)
            if not info:
                return f"我试着读 `{p}` 没成功，请确认这个文件夹存在、且我能访问。"
            self._touch_ref(p)
            self.work_ctx[p] = info[:4000]
            self.work_ctx = dict(list(self.work_ctx.items())[-3:])
            if want:
                ans = self._brain(
                    "下面是用户给的一个文件夹的清单与其中部分文件的内容。"
                    "请围绕用户意图（概括里面有什么 / 找问题 / 提建议）作答，分条列点、具体可执行。\n\n"
                    + info[:6000],
                    system="你是擅长文件与代码审查的 AI 助手，能抓住关键、直指问题。")
                return f"分析文件夹《{os.path.basename(p)}》：\n\n" + ans
            head = "\n".join(info.splitlines()[:40])
            more = (f"\n……共 {len(info)} 字，内容已进入我的工作上下文，可继续问"
                    f"（如：总结里面所有文件 / 这个文件夹有没有问题）。") \
                if len(info) > 2000 else ""
            return f"✅ 这是《{os.path.basename(p)}》里的内容：\n\n{head}{more}"
        if kind == "listhelp":
            home = os.path.expanduser("~")
            desk = os.path.join(home, "Desktop")
            lst = AT.list_dir(desk if os.path.isdir(desk) else home)
            return ("当然能读！给我文件路径或名字就行（支持 txt/md/docx/xlsx/代码/网页等）。\n"
                    "比如：`读一下 D:\\文档\\方案.md`、`帮我找 报告 文件`、`分析一下 E:\\xx.docx`。\n\n"
                    "你桌面上现在有：\n" + lst)
        if kind == "analyzectx":
            if not self.work_ctx:
                # v0.27.3：旧版在这里只回一句"我还没读过文件呢"，用户感觉
                #   "没有任何分析"。现在改为真的去扫一遍目录并给下一步。
                return self._dirscan_reply("")
            pth, txt = list(self.work_ctx.items())[-1]
            ans = self._brain(
                "请分析下面这份材料，给出：1) 核心内容概括 2) 亮点 3) 风险或问题 4) 改进建议。\n\n材料：\n"
                + "\n".join(txt.splitlines()[:150]),
                system="你是擅长文档与方案分析的 AI 助手。")
            return "分析《" + os.path.basename(pth) + "》：\n\n" + ans
        if kind == "codeask":
            return ("能呀！学了编程就是为了动手干活嘛 😊 我现在就能：\n"
                    "1. **多语言写码并运行**：`帮我写个 python 脚本 批量重命名文件`、"
                    "`写个 js 脚本 生成随机密码`、`做个网页 计算器`（网页会用浏览器打开）——"
                    "Python / JavaScript / 网页 / Java / Go / SQL 都行。\n"
                    "2. **全栈开发**：`帮我开发一个 叫记账本 的小系统，前端网页+后端+数据库` "
                    "—— 我会生成完整多文件项目并尝试运行，工作台面板能看到。\n"
                    "3. **结合文件干活**：先 `读一下 <你的项目路径>`，再让我照着写。\n\n"
                    "注：Python/JS/网页 直接能跑；Java/Go 需要本机装了对应环境，"
                    "没装我会把代码写好并告诉你怎么装。")
        if kind == "files":
            kw, folder = agent[1], agent[2]
            hits = AT.find_files(kw, folder)
            if not hits:
                return f"在{folder or '你的用户目录'}里没找到名字带「{kw}」的文件，换个关键词试试？"
            lines = "\n".join(hits[:12])
            return f"我找到了这些（共 {len(hits)} 个）：\n\n" + lines
        if kind == "script":
            req, run = agent[1], agent[2]
            return self._script_plan(req, bool(run))
        if kind == "project":
            return self._project_run(agent[1])
        if kind in ("genppt", "gendoc", "genxls"):
            return self._genfile_run(kind, agent[1])
        if kind == "runlast":
            try:
                cands = [(os.path.getmtime(os.path.join(AT.SCRIPTS_DIR, f)),
                          os.path.join(AT.SCRIPTS_DIR, f))
                         for f in os.listdir(AT.SCRIPTS_DIR)]
            except Exception:
                cands = []
            if not cands:
                return "我还没写过脚本呢。先说「帮我写个脚本 xx」，写完就能运行。"
            path = max(cands)[1]
            out = AT.run_script(path)
            return f"运行 `{path}`：\n\n{out}"
        if kind == "weblearn":
            topic = agent[1]
            text, srcs = AT.fetch_topic_text(topic, max_chars=30000, return_srcs=True)
            if not text:
                return ("这次没能从网上抓到《" + topic + "》的资料（网络不通或被源站拦截）。"
                        "我会过一会儿再试；也可以换个相近的词，或直接发个网页链接给我读。")
            bullets = self._study_extract(topic, text)
            # 学到 = 内容入库。要点只是"学完后的速记索引"，绝不能因为要点没提炼好
            # 就把已经抓到的原文丢掉、假装没学过。
            if not bullets:
                bullets = ["（原文已存入知识库，要点待后续提炼）"]
            # v0.27.8：把真实抓到的来源 URL 一并记进知识库（资料出处可追溯、内容更全）
            _src = ("网页自学：" + "、".join(srcs)) if srcs else "网页自学"
            # v0.30.8：入库前先洗掉网页噪声。抓回来的 3 万字里相当一部分是
            # 导航/广告/"相关搜索"/成段重复的样板 —— 原样存进库，
            # 用户翻到的是目录页而不是资料（"并不全并不完善"的真凶之一）。
            _body = knowledge.strip_web_noise(text) or text
            kb_record(topic, bullets[:6], text=_body, src=_src)
            # 立刻落成 Obsidian 笔记（资料库要能当"库"用，而不只是一个 json 字段）
            try:
                knowledge.export_one(topic)
            except Exception:
                pass
            # 原文同时存成 .md 书，资料库书架可直接打开全文
            try:
                bpath = os.path.join(knowledge.BOOKS_DIR, topic + ".md")
                with open(bpath, "w", encoding="utf-8") as f:
                    f.write("# " + topic + "\n\n" + _body[:30000])
            except Exception:
                pass
            real = [b for b in bullets if not b.startswith("（原文已存入")]
            n = len(text)
            if real:
                return ("我上网学了《" + topic + "》并存入知识库——含 " + str(len(_body)) +
                        " 字原文全文（已滤掉网页导航广告等噪声），"
                        "打开「资料库」双击就能读到整篇，也可以用 Obsidian 打开资料库文件夹看：\n- " +
                        "\n- ".join(real[:6]))
            return ("我把《" + topic + "》的网页内容（约 " + str(n) +
                    " 字）存进知识库了，资料库里双击即可看全文。这份资料有点杂，"
                    "我暂时没提炼出干净的要点——但内容已经学下来、随时能查。"
                    "换个更常见的说法（比如「Python 基础」）我通常能学得更扎实。")
        return "（未知工具指令）"

    def _study_extract(self, topic: str, text: str) -> list:
        """提炼要点（学完后的速记索引）：模型可用就精炼；模型不可用或资料太杂，
        退回句子级兜底。要点提炼结果不决定"学没学到"——原文入库才算学。"""
        try:
            if self.cfg.get("api_key") or detect_local_llm():
                ans = self._brain(
                    f"你正在自学《{topic}》。下面是网上抓到的资料。\n"
                    f"请提炼出其中真正有信息量的干货要点：每条一行、中文、具体、可复述；"
                    f"过滤掉导航、广告、套话等无关内容。若资料单薄或噪声多，"
                    f"就如实写下你从这份资料里实际读到的 2-3 条内容，不要写 NO_CONTENT。\n\n"
                    f"资料：\n" + text[:7000],
                    system="你是严谨自学的 AI：只输出干货要点，不做其他说明。",
                    max_tokens=800, task="study")
                lines = [ln.strip("•-* 0123456789.、 #") for ln in (ans or "").splitlines()
                         if ln.strip()]
                lines = [ln for ln in lines
                         if not ln.upper().startswith("NO_CONTENT") and len(ln) >= 4]
                if len(lines) >= 2 and sum(len(x) for x in lines) > 60:
                    return lines[:6]
        except Exception as ex:
            logging.info("study extract llm skip: %s", ex)
        bl = knowledge.sentence_bullets(text, 6) or knowledge.rule_bullets(text, 5)
        if not bl:
            bl = ["（原文已存入知识库，这份资料暂时提炼不出要点）"]
        return bl

    def _script_plan(self, req: str, run: bool) -> str:
        """阶段④：计划(写码)→执行(运行)→验证(检查输出)→失败自动修复重跑一次。

        让"写脚本"从一次生成变成"干到对为止"的最小闭环，失败经验由调用方
        写入程序记忆层（_note_proc），下次同类请求会被记忆影响。
        """
        lang = AT.detect_lang(req=req)
        lang_label = AT.LANGS.get(lang, ("", "", "Python"))[2]
        # v0.31.2 过程流：起手一句 + 真思考增量（写码期间不再全黑）
        self._step("plan", "写脚本", lang_label, (req or "")[:80])
        _th = {"t": 0.0}

        def _on_think(_d, full):
            now = time.time()
            if now - _th["t"] >= 0.4:
                _th["t"] = now
                self._step("think", "深度思考", detail=(full or "")[-320:], key="think")

        ctx = ""
        if self.work_ctx:
            parts = [f"[工作文件：{os.path.basename(p)} 内容摘要]\n{t[:800]}"
                     for p, t in list(self.work_ctx.items())[-2:]]
            ctx = "\n\n参考用户已提供的文件内容（按需使用其中结构/逻辑/命名）：\n" + "\n".join(parts)

        # —— Act 1：生成代码 ——
        code = self._brain(
            f"根据需求写一段可直接运行的 {lang_label} 代码。只输出代码，不要解释。\n需求：" + req + ctx,
            system=f"你是严谨的 {lang_label} 工程师，代码健壮、有注释、无敏感操作；代码注释用中文，需要向用户解释时一律用中文。"
                   "若需求是'实现/完成/修改'文件里描述的功能，请参考文件内容写出能落地的代码。",
            on_think=_on_think)
        m = re.search(r"```(?:\w+)?\n?(.*?)```", code, flags=re.S)
        code = (m.group(1) if m else code).strip()
        self._step("plan", "代码已生成", "%d 行" % len(code.splitlines()),
                   "正在做语法/配平/占位符校验")
        # v0.22 输出验证器：语法/配平/占位符规则校验，不通过带具体问题重生成一次
        ok, probs = VAL.check_code(code, lang_label)
        if not ok:
            self._step("edit", "校验未过，重生成一次", "；".join(probs)[:120])
            fix = self._brain(
                f"根据需求写一段可直接运行的 {lang_label} 代码。只输出代码，不要解释。\n"
                f"需求：{req}{ctx}\n\n"
                f"你上一次的代码有这些问题，必须修正：\n- " + "\n- ".join(probs),
                system=f"你是严谨的 {lang_label} 工程师，代码健壮、有注释、无敏感操作；代码注释用中文，需要向用户解释时一律用中文。",
                on_think=_on_think)
            m2 = re.search(r"```(?:\w+)?\n?(.*?)```", fix, flags=re.S)
            fix_code = (m2.group(1) if m2 else fix).strip()
            ok2, _ = VAL.check_code(fix_code, lang_label)
            if ok2:
                code = fix_code
                logging.info("validator: code regenerated and fixed (%s)", "; ".join(probs))
        path = AT.write_script(req[:24], code, lang)
        self._step("new", "脚本已落盘", path, "+%d 行" % len(code.splitlines()))
        if not run:
            return (f"写好了（{lang_label}）：`{path}`\n"
                    f"要运行的话对我说“运行 刚才的脚本”，或重发时带上运行。")

        # —— Act 2 + Check：运行并检查 ——
        out = AT.run_script(path)
        self._step_run("运行脚本", os.path.basename(path), out,
                       ok=not _looks_error(out))
        if not _looks_error(out):
            return f"写好了并运行成功（{lang_label}）：`{path}`\n\n运行结果：\n{out}"

        # —— Act 3：带报错自动修复一次（执行-验证环） ——
        try:
            self._step("edit", "运行报错，自动定位并修复一次",
                       (out or "").strip().splitlines()[-1][:120] if (out or "").strip() else "")
            fix = self._brain(
                "你刚写的脚本运行报错了。下面是运行输出（含报错信息）。"
                f"请定位错误并输出**修复后的完整代码**，只输出代码。\n需求：{req}\n运行输出：\n{out[:1200]}",
                system=f"你是严谨的 {lang_label} 工程师，定位报错并给出可运行的完整代码；注释与解释用中文。",
                on_think=_on_think)
            m2 = re.search(r"```(?:\w+)?\n?(.*?)```", fix, flags=re.S)
            code2 = (m2.group(1) if m2 else fix).strip()
            if code2 and code2 != code:
                path = AT.write_script(req[:24], code2, lang)
                add, dele = self._diff_stat(code, code2)
                self._step("edit", "修复版已落盘", path, "+%d / -%d 行" % (add, dele))
                out2 = AT.run_script(path)
                self._step_run("重跑脚本", os.path.basename(path), out2,
                               ok=not _looks_error(out2))
                if not _looks_error(out2):
                    return (f"第一次运行报错了，我自动修了一版并跑通了：`{path}`\n\n"
                            f"修复后运行结果：\n{out2}")
                return (f"写好了（{lang_label}），自动修复后仍报错：`{path}`\n\n"
                        f"最新输出：\n{out2}\n\n把报错贴给我，我再接着修。")
        except Exception:
            pass
        return f"写好了但运行报错（{lang_label}）：`{path}`\n\n输出：\n{out}\n\n把报错发给我，我再修。"

    def _project_run(self, req: str) -> str:
        """全栈开发：生成多文件项目（前端+后端+数据库，按需求取舍）→ 保存 → 尝试运行。

        v0.17.2：同一会话里再提开发需求，默认 = 在上一版项目上修改（带上下文改写，
        旧文件自动归档到「旧版_时间」），不再凭空另建一个互不相干的新页面/新项目；
        想开全新的项目时说"另建一个 / 换个项目 / 新开一个"即可。
        """
        proj = getattr(self, "_ses_proj_dir", None) or ""
        reset = re.search(r"(另建|另做|新开|开个新|新建一?个|再做一个新|换一?个新|"
                          r"不是.{0,6}那个|不要.{0,6}原来)", req)
        # ★ v0.31.3：需求里**写明了目标目录**（且真存在）→ 就在那里干活。
        #   用户显式给的路径 > "在工作根里另建一个"。真机上用户明确说了
        #   `D:\Code副\springcloud-business`，旧版却跑到桌面另建了个 Python 项目。
        _tgt = _extract_target_dir(req)
        if _tgt:
            proj = _tgt
            self._ses_proj_dir = _tgt
            self._append("系统", "按你说的，在 <code>%s</code> 里干活 ✓"
                         % html.escape(_tgt))
        edit_mode = bool(proj and os.path.isdir(proj)) and not reset
        if _tgt and edit_mode:
            # 用户指定的目录**是空的** → 那是"从零建"，不是"改旧版"（别去读不存在的旧代码）
            try:
                edit_mode = bool(os.listdir(proj))
            except Exception:
                edit_mode = False
        # ★ v0.31.3：项目名**必须来自真实需求**。继承场景下 `req` 前面裹着模板句
        #   与标记（「帮我开发一个项目：（沿用上文需求）…」），直接拿去取名会得到
        #   「**开发一个项目请立即开始**」这种怪名字 —— 真机上就是这么发生的
        #   （用户原话：「出来了一个不是我想要的开发」，连项目名都是那句废话）。
        _name_src = re.sub(r"^（沿用上文需求）", "", str(self._inherit_req or "")).strip() \
            or req
        _name_src = re.sub(r"^帮我(?:开发|做|写|建|搭)\s*(?:一|一个|个)?\s*项目[：:，,、 ]*",
                           "", _name_src).strip() or _name_src
        name = _pick_project_name(_name_src)
        # ★ v0.31.3：**明说这次在改哪个项目**。真机上用户遇到"不是我想要的开发"时，
        #   最需要知道的就是"它到底动了哪个项目、想开新的该怎么说" —— 旧版只在内联
        #   步骤里写一句"已有项目原地改"，用户看不到项目名，也不知道怎么另建。
        if edit_mode:
            self._append("系统", "这次在**已有项目**『%s』上改（想另开一个就说「另建一个…」）"
                         % html.escape(str(name)))
        # 记住开发栏目的这一轮需求（供下一句「请立即开始」继承，并落盘备重启）
        try:
            if getattr(self, "_inherit_req", "") or req:
                self._chip_req["project"] = str(self._inherit_req or req)[:600]
                self.dev = getattr(self, "dev", None) or {}
                self.dev["last_req"] = str(self._inherit_req or req)[:600]
                self.dev["last_ts"] = time.time()
                if not self.dev.get("project"):
                    self.dev["project"] = name
                self._save_work_state()
        except Exception:
            logging.exception("remember project req failed")
        base_sys = ("你是全栈工程师。根据需求生成一个完整可运行的多文件项目"
                    "（前端页面+后端服务+数据库，按需求取舍，不要偷懒只给建议）。"
                    "输出格式必须严格遵守：每个文件用下面格式包裹，除此之外不要输出任何解释文字：\n"
                    "===FILE: 相对路径/文件名===\n"
                    "（该文件的完整内容）\n"
                    "===END===\n"
                    "硬性要求：\n"
                    "1) 前端用单文件 HTML（内联 CSS/JS），双击就能看效果；\n"
                    "2) 后端优先 Python（Flask/FastAPI，标准库优先）或 Node.js，附启动说明；\n"
                    "3) 数据用 SQLite/JSON 本地方案，附建表或初始化脚本（.sql 或 init 数据文件）；\n"
                    "4) 有依赖时附 requirements.txt 或 package.json；\n"
                    "5) 文件内不出现本机绝对路径；界面与注释全部中文；\n"
                    "6) 代码要能直接运行，不要留 TODO 占位。")
        if edit_mode:
            # 把旧项目入口文件读进来，让模型"看着旧代码改"而不是凭空另写
            ctx = []
            for f in ("index.html", "app.py", "main.py", "server.py",
                      "app.js", "package.json", "README.md"):
                fp = os.path.join(proj, f)
                if os.path.isfile(fp):
                    try:
                        txt = open(fp, encoding="utf-8", errors="ignore").read()
                        ctx.append("【" + f + "】\n" + txt[:6000])
                        self._step("read", "已读取", os.path.join(proj, f),
                                   "%d 字" % len(txt))
                    except Exception:
                        pass
            base_sys += ("\n\n用户是要在**已有项目上修改/继续开发**。"
                         "已有项目目录：" + proj + "\n以下是其中已有文件的部分内容：\n"
                         + "\n".join(ctx[:3])
                         + "\n\n请基于以上现有代码完成用户要求。只输出你新建/改动的文件"
                           "的**完整新内容**（===FILE: 相对路径/文件名===格式），"
                           "没改动的文件不要重复输出。改动时保留原有逻辑与风格，"
                           "不要整体推倒重写。")
        base_sys += self._work_mem(req)       # v0.26.2：开发也带上相关记忆/偏好
        # v0.31.2：模型推理期间不再全黑 —— 计划先说清楚，真思考接上过程流。
        self._step("plan", "让模型生成项目文件清单",
                   "已有项目原地改" if edit_mode else "新建项目",
                   "max_tokens=3000（本地模型可能要几十秒到几分钟）")
        _th = {"t": 0.0}

        def _on_think(_d, full):
            # 真实思考增量（网关回调），不是伪造的"假思考"；节流 0.4s 只控刷新频率
            now = time.time()
            if now - _th["t"] >= 0.4:
                _th["t"] = now
                self._step("think", "深度思考", detail=(full or "")[-320:], key="think")

        bundle = self._brain("项目需求：" + req, system=base_sys, max_tokens=3000,
                             on_think=_on_think)
        files = AT.parse_bundle(bundle)
        if not files:
            code = re.sub(r"```(?:\w+)?\n?", "", bundle).replace("```", "").strip()
            if code:
                ext = ".html" if "<html" in code[:300].lower() else ".py"
                files = {"main" + ext: code + "\n"}
        if not files:
            self._step("err", "模型没给出可用文件", detail=(bundle or "")[:200])
            return ("这次没生成出项目文件（模型可能没理解需求）。"
                    "可以把需求说得更具体些再试一次，例如：帮我开发一个 叫记账本 的 "
                    "待办应用，前端网页+Python后端+SQLite数据库。")
        self._step("plan", "文件清单已就绪", "%d 个文件" % len(files),
                   "、".join(list(files)[:6]))
        if edit_mode:
            # —— 原地更新：旧文件先进「旧版_时间」归档，再写新内容 ——
            n_updated = n_new = 0
            bk = os.path.join(proj, "旧版_" + time.strftime("%Y%m%d_%H%M%S"))
            for rel, content in files.items():
                rel = rel.lstrip("/ ")
                if ".." in rel:
                    continue
                full = os.path.join(proj, *rel.split("/"))
                os.makedirs(os.path.dirname(full) or proj, exist_ok=True)
                if os.path.exists(full) and os.path.isfile(full):
                    try:
                        os.makedirs(os.path.dirname(os.path.join(bk, rel)) or bk,
                                    exist_ok=True)
                        with open(full, "rb") as s, \
                                open(os.path.join(bk, rel), "wb") as t:
                            t.write(s.read())
                        n_updated += 1
                    except Exception:
                        pass
                else:
                    n_new += 1
                with open(full, "w", encoding="utf-8") as f:
                    f.write(content)
            # 过程流：逐个文件播报"修改 +N -M / 新建 +N"（与归档件做真 diff）
            self._step_files(files, base=proj, backup=bk)
            tree = "📁 " + proj + "\n" + AT._tree(proj)
            AT._register_project(os.path.basename(proj.rstrip("/\\")), proj,
                                 list(files))
            runmsg = AT.run_project(proj)
            self._step_run("运行项目", "python app.py（自动探测入口）", runmsg,
                           ok=not _looks_error(runmsg))
            self._mark_effect_dev(proj)
            return (f"🛠 已在项目「{os.path.basename(proj.rstrip('/\\\\')) or name}」上"
                    f"完成更新：改 {n_updated} 个 / 新增 {n_new} 个文件"
                    f"（改动前的旧文件已归档到 旧版_时间 子目录，可随时对照）。\n\n"
                    f"{tree}\n\n{runmsg}\n\n"
                    f"还要改哪里直接说：加个删除按钮 / 标题改成 xx / 加个统计页……"
                    f"我会继续在这个项目上改。")
        pdir, tree = AT.save_project(name, files)
        self._ses_proj_dir = pdir
        self._stamp_conv_meta(proj_dir=pdir)
        self._step("new", "项目目录已创建", pdir, "%d 个文件落盘" % len(files))
        self._step_files(files, base=pdir)
        runmsg = AT.run_project(pdir)
        self._step_run("运行项目", "python app.py（自动探测入口）", runmsg,
                       ok=not _looks_error(runmsg))
        self._mark_effect_dev(pdir)
        return (f"🏗 项目「{name}」搭好了！共 {len(files)} 个文件：\n\n{tree}\n\n"
                f"{runmsg}\n\n"
                f"想改哪里直接说：加个删除按钮 / 标题改成 xx / 数据换 SQLite / 加个统计页……"
                f"我会在原项目上改（不再另起一个没关联的新项目）。")

    # ---------- v0.30.17：产出效果的登记与呈现（右栏「产出」的唯一出口）----------
    def _mark_effect(self, kind, media="", path="", copy=None, title="",
                     paths=None, note="", page=""):
        """登记本轮产出的"效果"。**只记事实，不做任何 UI。**

        为什么让各工种只"登记"、把 UI 全收在 `_present_effect` 一处：
          · `_create_ad` 内部要调 `_create_image`，两者都会登记；**后登记的覆盖先登记
            的**（广告覆盖图像）→ 天然得到"广告页"，不需要在 `_create_image` 里
            判断"我这次是不是被广告调的"（那种判断迟早漏一种调用方式）。
          · 展示逻辑集中在一个出口，改一处就是改全部工种。
        """
        try:
            self._effect = {
                "kind": str(kind or ""),
                "media": str(media or ""),
                "path": str(path or ""),
                "copy": copy,
                "title": str(title or ""),
                "paths": [str(x) for x in (paths or []) if x],
                "note": str(note or ""),
                # ★ v0.31.0「页归属」：**一个产物一页**。同一次创作（同一目录 /
                #   同一份文件）的多轮产出都落在同一页，逐轮**向下追加**。
                #   各工种显式传自己的归属键（图片/视频/漫剧 = 会话目录；
                #   文档 = 那份文件；开发 = 项目目录）。
                "page": str(page or ""),
            }
        except Exception:
            logging.exception("mark effect failed")

    def _mark_effect_dev(self, pdir: str):
        """登记"开发"类产出。

        主产物尽量取**入口页**（index.html 优先），而不是目录 ——
        小志要的是"在此栏呈交生成的整个站点效果或页面效果"，指向一个目录是看不到
        效果的。没有已知入口就取第一个可读文件，总之要给一个能"点开看看"的东西。
        """
        try:
            if not pdir or not os.path.isdir(pdir):
                return
            entry = ""
            for cand in ("index.html", "index.htm", "main.py", "app.py",
                         "server.py", "README.md"):
                f = os.path.join(pdir, cand)
                if os.path.isfile(f):
                    entry = f
                    break
            if not entry:
                for root, _ds, fs in os.walk(pdir):
                    hit = [f for f in fs
                           if f.lower().endswith((".html", ".py", ".md", ".js"))]
                    if hit:
                        entry = os.path.join(root, sorted(hit)[0])
                        break
            # ⚠️ `media=entry` **必须传**：效果区按 media 决定渲染成什么 ——
            #    入口页是 .html 才会被渲染成内嵌 iframe（站点效果）。
            #    只给 path 不给 media 的话，右栏里根本没有 iframe，
            #    "站点效果"就成了空话（verify_v0310 的站点那两条就是这么抓到的）。
            self._mark_effect(
                "project", media=entry or "", path=entry or pdir,
                paths=[pdir], page=pdir,
                title="🖥 " + os.path.basename(os.path.normpath(pdir))[:12],
                copy="项目目录：`%s`" % pdir,
                note="站点效果要看**真实渲染**：v0.31.0 会把真浏览器嵌进右栏；"
                     "现在先点「↗ 打开」用浏览器看。")
        except Exception:
            logging.exception("mark dev effect failed")

    @staticmethod
    def _strip_effect_paths(txt) -> str:
        """剥掉文案里的路径片段（反引号里的、裸写的、file:// 的）。

        ★ 小志 2026-09-17 的原话：「广告语是纯文字展示而**不需要 md 文件路径**」。
        由来：v0.30.6 为了让"从回复正文正则抽产物路径"这个兜底能用，`_create_ad`
        把方案 .md 路径放在了回复最前面；那种东西一旦进了效果区，用户看到的就是
        "广告语变成了一个 md 路径"。**路径归路径区，效果区只要文字。**

        反例对照（必须是能抓住的）：body 里塞
        "详见 `C:\\x\\方案.md` 与 C:\\x\\图.png 两张" → 输出里两个路径都要没。
        """
        if not txt:
            return ""
        t = str(txt)
        t = re.sub(r"`[^`\n]*(?:[A-Za-z]:[\\/]|file:)[^`\n]*`", "", t)
        # 裸写的盘符路径（`(?<![A-Za-z])` 防住 https:// 这种被误当路径）
        t = re.sub(r"(?<![A-Za-z])[A-Za-z]:[\\/][^\s<>\"'，。；、）]+", "", t)
        t = re.sub(r"file:///\S+", "", t)
        return re.sub(r"\n{3,}", "\n\n", t).strip()

    def _effect_copy_html(self, copy) -> str:
        """把登记的"文案"转成效果区里那段**纯文字**。

        ★ 小志 2026-09-17 的原话：「广告语是纯文字展示而不需要 md 文件路径」——
        所以这里**主动剥掉路径样式的片段**：v0.30.6 为了让"从正文正则抽产物路径"
        兜底能用，`_create_ad` 曾把方案 .md 路径放在回复最前面；那种东西一旦进了
        效果区，就成了"广告语变成了一个 md 路径"。路径归路径区，这里只要文字。
        """
        if not copy:
            return ""
        if isinstance(copy, dict):
            # 广告：标题 / 正文 / 行动号召 —— 纯文字排版（不是文件路径）
            # ⚠️ 每个字段都要过 `_strip_effect_paths`：模型给的正文里**经常**
            #    夹带文件名/路径（"详见 xxx.md"），不过一遍就会漏进效果区。
            #    这是 verify_v0317 的反例对照逼出来的（只 escape 不剥离 = bug）。
            out = []
            _h = self._strip_effect_paths(copy.get("headline"))
            _b = self._strip_effect_paths(copy.get("body"))
            _c = self._strip_effect_paths(copy.get("cta"))
            if _h:
                out.append("<div style='font-size:17px;font-weight:bold;color:#0f172a;"
                           "line-height:1.5'>%s</div>" % html.escape(_h))
            if _b:
                out.append("<div style='font-size:13px;color:#334155;line-height:1.85;"
                           "margin-top:8px'>%s</div>"
                           % html.escape(_b).replace("\n", "<br>"))
            if _c:
                out.append("<div style='margin-top:10px'><span style='display:inline-block;"
                           "background:#0ea5e9;color:#ffffff;border-radius:6px;"
                           "padding:4px 14px;font-size:12px'>%s</span></div>"
                           % html.escape(_c))
            if copy.get("note"):
                out.append("<div style='margin-top:8px;color:#94a3b8;font-size:11px'>"
                           "%s</div>" % html.escape(str(copy["note"])))
            return "".join(out)
        txt = self._strip_effect_paths(copy)
        if not txt:
            return ""
        return md_to_html(txt)

    def _kind_label(self, kind) -> str:
        """块头那个小胶囊显示的中文工种名。"""
        return self._KIND_CN.get(str(kind or ""), "")

    def _append_effect(self) -> bool:
        """把登记的产出效果**追加**到它所属的产出页。

        ★ 与 v0.30.17 的 `_present_effect` 的关键区别：那时是"填满这一页"
          （每次覆盖），现在是**往这一页后面接一块**（小志：「不删除和覆盖
          之前的内容，每次提了新要求新产出就往下输出」）。
          页的归属由 `_mark_effect(page=...)` 决定 —— 一个产物一页。

        ⚠️ 返回 False 的语义必须精确：**确实什么都没展示**。
          调用方（`_finish`）靠它决定要不要走旧的兜底路径。
          只要在"其实没展示"时返回 True，兜底就永远失效（0.30.15 的同款陷阱）。
        """
        e = getattr(self, "_effect", None)
        self._effect = None                    # 用完即清，免得串到下一轮
        if not e or not e.get("kind") or not hasattr(self, "right_tabs"):
            return False
        try:
            kind = e["kind"]
            page = e.get("page") or ""
            title = e["title"] or "📋 产出"
            if not page:
                # 没有落盘目录的产出（少数）：按"种类 + 内容"开一页，
                # 至少不会把不相干的东西挤进同一个页
                page = "%s#%d" % (kind, abs(hash(str(e.get("copy"))[:200])) % (10 ** 10))
            key = "art:%s" % page
            d = self._panel_open(key, title, kind=kind, page=page)
            if not d:
                return False
            blk = self._round_block(
                d,
                inner=self._effect_copy_html(e.get("copy")),
                media=e.get("media") or "",
                path=e.get("path") or "",
                kind_label=self._kind_label(kind),
                note=e.get("note") or "")
            self._panel_append(d, blk, path=e.get("path") or "",
                               media=e.get("media") or "")
            logging.info("产出效果已追加：kind=%s page=%s 第%d轮",
                         kind, page, d.get("rounds") or 0)
            return True
        except Exception:
            logging.exception("append effect failed")
            return False

    def _work_mem(self, req: str, cap: int = 380) -> str:
        """干活时的 PASM 记忆注入：把与本次需求相关的用户偏好/过往/规则带给模型。

        让"它记得你"真正作用于做表格/写方案/开发——模型能沿用你的风格与历史约定，
        而不是每次像第一次见面。
        """
        try:
            t = (req or "").strip()
            if not t:
                return ""
            picks = []
            def hit(note: dict) -> bool:
                tag = str(note.get("tag") or note.get("t") or "")
                return any(ch in tag for ch in re.findall(r"[\u4e00-\u9fa5]{2,6}", t)[:6])
            for n in self.notes[-40:]:
                if hit(n):
                    picks.append("记得你提过：" + str(n.get("tag") or "")[:120])
            for p in self.prefs[-40:]:
                if hit(p):
                    picks.append("偏好/约定：" + str(p.get("tag") or "")[:120])
            if picks:
                blk = "\n".join(picks[:3])[:cap]
                return ("\n\n【关于用户·若与本需求相关就自然沿用】\n" + blk + "\n")
        except Exception:
            pass
        return ""

    def _table_analysis_reply(self, text: str = "") -> str:
        """v0.27 数学脑（Phase A2）：对表格做真实数值分析——回归/相关/离群点。

        数据来源优先级：消息里的显式 xlsx 路径 → 本会话最近生成的表格（产物记忆）。
        全部数值由 numpy 真算，回复里给结论而非模型编数。
        """
        if MLAB is None:
            # v0.30.8：原文案"重启软件再试试"是**误导** —— 真因通常是运行环境/
            # 安装包里没有 numpy（数学脑靠它做 polyfit/lstsq/corrcoef），重启不会变。
            # 说实话 + 给真能做的下一步，才对得起用户的时间。
            return ("数学脑（真实数值计算）这次没加载起来 —— 它需要 numpy，"
                    "当前环境里没有。<br>"
                    "这不是重启能解决的：<br>"
                    "① 装的是安装包：这一版的打包清单里漏了 numpy，等更新版即可；<br>"
                    "② 跑的是源码：在同一个 Python 环境执行 "
                    "<code>pip install numpy</code> 再启动。<br>"
                    "在此之前我不会拿模型「猜」的数字冒充计算结果 —— "
                    "要分析表格可以把数据发我，我按能核实的方式讲。")
        path = ""
        m = re.search(r"([A-Za-z]:[\\/][^\s，。;）]+\.xlsx?)", text or "")
        if m and os.path.isfile(m.group(1)):
            path = m.group(1)
        if not path:
            rec = (getattr(self, "_ses_gen", {}) or {}).get("genxls") or {}
            p = rec.get("path") or ""
            if p and os.path.isfile(p):
                path = p
        if not path:
            # v0.27.3：旧版直接回"手头没表格"就结束了（用户感觉"没有下一步"）。
            #   现在真的去 桌面/下载/文档 找最近改过的表格，列出来让你点一个。
            import sysops as _SYST
            cands = _SYST.find_recent_tabular(8)
            if cands:
                self._tab_candidates = cands
                return ("我在桌面 / 下载 / 文档里找到这些表格，想分析哪个？"
                        "把序号或路径发我就行：\n\n"
                        + "\n".join(f"{i+1}. {os.path.basename(p)}\n   `{p}`"
                                     for i, p in enumerate(cands))
                        + "\n\n也可以直接把 xlsx 路径发我（例：分析 D:\\数据\\销量.xlsx）。")
            return ("我在桌面 / 下载 / 文档里没找到表格文件（.xlsx/.xls/.csv）。"
                    "你可以：\n1) 把表格路径发我（例：分析 D:\\数据\\销量.xlsx）\n"
                    "2) 或者先让我做一份（说「帮我做个 xx 表格」）\n"
                    "3) 或者说「分析一下桌面」，我先帮你把文件盘一遍。")
        try:
            from openpyxl import load_workbook
            wb = load_workbook(path, read_only=True, data_only=True)
            ws = wb.active
            vals = [list(r) for r in ws.iter_rows(values_only=True)
                    if any(c is not None for c in r)]
            wb.close()
        except Exception as ex:
            return f"读取表格失败了：{ex}"
        if len(vals) < 4:
            return "这份表格有效行太少（连表头不足 4 行），没有分析价值——让我先帮你把它做厚一点？"
        header = [str(c) if c is not None else f"列{i+1}"
                  for i, c in enumerate(vals[0])]
        rows = [[("" if c is None else c) for c in r] for r in vals[1:]]
        try:
            res = MLAB.analyze_table(header, rows)
        except Exception as ex:
            return f"分析时出错：{ex}"
        if not res.get("ok"):
            return ("这份表格里没有找到能算的数值列（每列至少 3 行数字）。"
                    "要么把数字补上，要么让我重新做一份带数据的表。")
        extra = ""
        for s in res.get("stats", []):
            if s.get("outliers_iqr"):
                extra += (f"\n- ⚠ 「{s['name']}」发现离群值 {s['outliers_iqr'][:4]}"
                          f"（超过 IQR 边界，建议核对）")
        return (f"📊 数学脑分析（numpy 真算）：`{os.path.basename(path)}`\n\n"
                f"{res['summary']}{extra}\n\n"
                f"想深挖可以继续说，比如：预测广告费 400 时销量多少 / "
                f"哪两列关系最大 / 把离群点标红。")

    def _genfile_run(self, kind: str, text: str) -> str:
        """生成 PPT/Word/Excel：LLM 创作 Markdown → 落成真实文件 → 打开文件夹。

        v0.26.2：同一会话里再次说"改表格/加一节/换配色" = 在**上一版那个文件**上改——
        把上一版完整内容喂给模型（不是让它另起炉灶），覆盖写回同一路径。
        明确说"另存/新做一份/换个文件/重做"才会另开新文件。
        """
        # 用户是否给了保存位置（目录或完整文件名）
        m = re.search(r"(?:保存到|放到|放在|存到|保存至|另存为)\s*"
                      r"([A-Za-z]:[\\/][^\s，。;；]+|桌面)", text)
        gave_dest = bool(m)
        new_dest = AT.desktop_dir() if (m and m.group(1) == "桌面") else \
            (m.group(1).rstrip("/\\") if m else "")
        req = re.sub(r"(?:保存到|放到|放在|存到|保存至|另存为)\s*"
                     r"[A-Za-z]:[\\/][^\s，。;；]+|保存到桌面", "", text).strip() or text
        # 判断"这是新做一份"还是"在上一版上改"
        is_new = bool(re.search(
            r"(?:另存|另做|新做|新建一份|新建一?个|再做一个新|换个?文件|换个?名|"
            r"重新做一份|重新生成|重做一?份|不要.{0,5}(?:原来|之前|旧的)|"
            r"推倒|从头来|清空重做)", req))
        prev = self._ses_gen.get(kind) if hasattr(self, "_ses_gen") else None
        edit_mode = bool(prev and os.path.isfile(str(prev.get("path") or ""))
                         and not is_new and not gave_dest)
        if kind == "genppt":
            system = ("你是资深演示设计师。围绕需求创作一份内容充实的 PPT 大纲（8-12 页）。"
                      "输出格式：第一行是 PPT 总标题；之后每一页用「## 页标题」开头，"
                      "页内要点每行以「- 」开头；不要输出任何解释、不要代码块。内容要有干货。")
        elif kind == "genxls":
            system = ("你是数据分析助手。围绕需求输出一个 Markdown 表格：第一行是表名（普通文本），"
                      "然后是形如 | 列1 | 列2 | 的表格（第二行用 |---|---| 分隔），"
                      "数据要合理充实（至少 8 行）；不要输出任何解释。")
        else:
            system = ("你是专业写手。围绕需求写一篇结构完整的文档（5-8 节）。"
                      "输出格式：第一行是文档标题；每节用「## 节标题」开头，节下是正文段落，"
                      "需要列表时用「- 」开头；不要代码块、不要解释。内容要专业、有细节。")
        if edit_mode:
            # —— v0.26.2 关键：把上一版全文喂回去，在旧稿上精准修改 ——
            old_md = str(prev.get("md") or "")
            old_path = str(prev.get("path") or "")
            kind_cn = {"genppt": "PPT", "genxls": "表格", "gendoc": "文档"}[kind]
            system += (f"\n\n重要：用户是在【上一版{kind_cn}】上提修改要求，不是从零新做。"
                       f"请严格在下面旧稿基础上修改：主题、大部分内容与结构都要保留，"
                       f"只按用户新要求增删改；绝不能把旧内容整篇替换成不相干的新内容，"
                       f"除非用户明确说重做。\n"
                       f"【上一版完整内容】\n{old_md[:9000]}")
        system += self._work_mem(req)          # v0.26.2：带上与你相关的记忆/偏好
        # v0.31.2 过程流：文件生成以前也是全程静默（一次大生成 + 落盘）
        _kcn = {"genppt": "PPT", "genxls": "表格", "gendoc": "文档"}.get(kind, "文件")
        self._step("plan", "创作%s正文" % _kcn, "改上一版" if edit_mode else "新做一份",
                   (req or "")[:80])
        _th = {"t": 0.0}

        def _on_think(_d, full):
            now = time.time()
            if now - _th["t"] >= 0.4:
                _th["t"] = now
                self._step("think", "深度思考", detail=(full or "")[-320:], key="think")

        md = self._brain("需求：" + req, system=system, max_tokens=3000,
                         task="filegen", on_think=_on_think)
        # 审计加固：模型偶尔会在 Markdown 外套代码块或先答“好的/能力清单”，剥掉并做产出校验，
        # 避免把“空壳/废话”也生成成一个无效文件（既占地方又让用户以为成功了）。
        md = re.sub(r"```(?:markdown|md|text|txt)?\s*", "", (md or "").strip())
        md = md.replace("```", "").strip()
        md = re.sub(r"^(好的|好的，|没问题|没问题，|我来|可以|当然可以|收到|明白)[，。!！\s]*", "", md).strip()
        title0 = (md.splitlines() or [""])[0].strip()
        if len(md) < 40 or len(title0) < 2 or title0.lower().startswith(("关于", "根据")):
            return ("这次模型没有产出合格的正文（只回了句客套话/能力说明，没给实际内容）。"
                    "麻烦把需求说得更明确些再试一次；也可以先在设置里确认大脑在线。")
        try:
            # v0.28.x：PPT / Word 都按节配图（需已配置图像引擎；没配则优雅退化为纯文字）
            imgs = self._section_images(md) if kind in ("genppt", "gendoc") else {}
            if edit_mode:
                # 覆盖写回上一版那个文件（不换名、不新建）
                if kind == "genppt":
                    path = AT.make_pptx(md, old_path, imgs)
                elif kind == "genxls":
                    path = AT.make_xlsx(md, old_path)
                else:
                    path = AT.make_docx(md, old_path, imgs)
            else:
                if kind == "genppt":
                    path = AT.make_pptx(md, new_dest or AT.desktop_dir(), imgs)
                elif kind == "genxls":
                    path = AT.make_xlsx(md, new_dest or AT.desktop_dir())
                else:
                    path = AT.make_docx(md, new_dest or AT.desktop_dir(), imgs)
        except Exception as ex:
            self._step("err", "生成文件失败", detail=str(ex)[:200])
            return ("生成文件失败了：" + str(ex) +
                    "。可以换个说法再试，或告诉我把文件存到哪个文件夹。")
        if not (os.path.isfile(path) and os.path.getsize(path) > 0):
            self._step("err", "文件生成出来是空的", detail=str(path))
            return ("文件生成到了路径但没有有效内容，可能是模型给的格式没解析好。"
                    "再发一次（说得更具体些），或换「文案」模式先出文字稿。")
        self._step("edit" if edit_mode else "new",
                   "已覆盖上一版" if edit_mode else "已生成%s" % _kcn,
                   str(path), "%.0f KB" % (os.path.getsize(path) / 1024.0))
        # 真实执行台账：文件确实存在且非空才记 —— 防幻觉守卫靠它核对
        # "模型说已生成"到底有没有真做（chat 栏目没有执行手段，必是幻觉）。
        try:
            import sysops as _SYS
            _SYS.note_action("genfile", path, ok=True, reason=kind)
        except Exception:
            pass
        # 记住这版产物 → 下次说"再改改"就改同一个文件
        self._ses_gen[kind] = {"path": path, "md": md, "title": title0,
                               "_t": int(time.time())}
        try:
            self._gen_remember()
        except Exception:
            pass
        folder = os.path.dirname(path)
        try:
            PLATFORM_OPS.startfile(folder)          # 打开所在文件夹让你直接看到
        except Exception:
            pass
        # v0.30.17：登记"文档与演示"效果 —— 效果区的正文就是**成稿本身**，
        # 再加产物路径。用户要看到的是内容，不是一个文件路径。
        try:
            _k = {"genppt": "ppt", "genxls": "xls", "gendoc": "doc"}.get(kind, "doc")
            _icon = {"genppt": "📽 ", "genxls": "📊 ", "gendoc": "📄 "}.get(kind, "📄 ")
            self._mark_effect(
                _k, path=path, paths=[os.path.dirname(path)], page=path,
                copy=md,
                title=_icon + (title or "文档")[:12],
                note="上面是成稿内容，下面是文件路径；要改哪一节直接说。")
        except Exception:
            logging.exception("mark doc effect failed")
        title, secs = AT._md_sections(md)
        outline = "、".join(h for h, _ in secs[:6])
        if edit_mode:
            return (f"✅ 已按你的要求**更新原文件**（不是另存新文件）：`{path}`\n"
                    f"（改动前的版本已不存在于新文件——若想留底，说「另存一份到 xx」即可）\n\n"
                    f"《{title}》共 {len(secs)} 部分：{outline}…\n"
                    f"还要怎么改直接说（我会继续改这同一个文件）。")
        return (f"✅ 做好啦！文件在这里：`{path}`\n"
                f"（已帮你打开所在文件夹）\n\n"
                f"《{title}》共 {len(secs)} 部分：{outline}…\n"
                f"想改内容直接说，比如：第2页换成市场分析 / 加一节预算 / 表格再加两行"
                f"（都会在同一个文件上改）。")

    # ---------- 创作引擎：真出图 / 图文短片 / 漫剧成片 ----------
    def _ensure_image_engine(self, kind: str):
        """懒接入：用到才弹接入框（图像/视频/漫剧共用图像引擎）；配置自动保存。"""
        conf = CRE.get_engine(self.cfg, "image")
        if CRE.conf_ok(conf):
            return conf
        dlg = EngineDialog(self.cfg, kind, self)
        if dlg.exec():
            _save_json(CONFIG, self.cfg)
            return CRE.get_engine(self.cfg, "image")
        return None

    def _section_images(self, md: str) -> dict:
        """为文档（PPT / Word）每节生成一张配图（需已在设置里配置图像引擎）；失败优雅降级为空字典。

        仅取前 6 节生成配图，避免一次文档出十几张图拖太久；未配置引擎时直接返回 {}，
        由 make_pptx / make_docx 退化为纯文字排版（仍比旧版美观）。"""
        try:
            import creators as CRE
            conf = CRE.get_engine(self.cfg, "image")
            if not CRE.conf_ok(conf):
                return {}
            title, secs = AT._md_sections(md)
            d = os.path.join(CRE.pick_out_root(self.cfg),
                             CRE.safe_filename("ppt_img", title or "slide"))
            os.makedirs(d, exist_ok=True)
            out = {}
            for i, (h, rows) in enumerate(secs[:6], 1):
                if not h:
                    continue
                bullets = [r[2:] if r.startswith("- ") else r for r in rows if r][:3]
                prompt = (f"{title} — {h}. " + "; ".join(bullets) +
                          ". clean professional illustration, modern flat style, no text, no watermark")
                try:
                    p = CRE.generate_image(conf, prompt, os.path.join(d, f"slide{i:02d}.png"),
                                           conf.get("size") or "1024x1024")
                    out[h] = p
                except Exception as ex:
                    logging.warning("PPT 配图失败(%s): %s", h, ex)
            return out
        except Exception as ex:
            logging.warning("PPT 配图整体失败: %s", ex)
            return {}

    def _engine_refuse_fallback(self, kind: str, text: str):
        """取消接入时的降级：不空手而归——图像给提示词包，视频/漫剧走脚本技能。"""
        if kind == "image":
            return ("image_pack", text)
        return ("skill", "manga-pipeline" if kind == "manga" else "video-script", text)

    def _create_ad(self, req: str) -> str:
        """广告设计（v0.30.13）：**方案 + 主视觉**一起交，不再等于"出一张图"。

        与"图像"栏目的区别（这是小志 2026-09-17 真机反馈的核心问题）：
          · 图像 → 一个提示词、一张图；
          · 广告 → **创意说明（策略层）+ 文案（标题/卖点/行动号召）+ 主视觉提示词**
                   + 主视觉图，并且**方案要落盘成 .md**（可交付、可复盘、可改稿）。
        出图仍然复用既有链路（`_create_image`：提示词精修 / 同图微调 / 会话内续画
        全都不丢），**不引入第二种引擎**，用户不用再配一次。
        """
        st = getattr(self, "_ad_pending", None) or {}
        self._ad_pending = None                       # 用完即清，免得串到别的请求
        theme = st.get("theme") or req
        product = theme
        # ① 真出主视觉（走既有出图链路）
        body = self._create_image(st.get("prompt") or AD.build_prompt(theme, product))
        # 实际用的提示词/图：精修后的那版才算数（方案里要把**真正用的**写进去）
        used_prompt = getattr(self, "_ses_img_prompt", "") or st.get("prompt") or ""
        img = ""
        _d = getattr(self, "_ses_img_dir", "") or ""
        # ⚠️ 只在**这一步真出了图**时才认图：目录里可能躺着上一轮的旧图，
        #    拿它当"本次主视觉"就是撒谎（方案里会写一张不是这次的图）。
        if "✅ 图出好了" in (body or "") and _d and os.path.isdir(_d):
            _c = [os.path.join(_d, f) for f in os.listdir(_d)
                  if re.match(r"图\d+\.png$", f)]
            if _c:
                img = max(_c, key=os.path.getmtime)   # 刚出的那张 = 最新的
        # ② 真落盘广告方案 .md（与图同目录；出图失败也要交方案，如实写「待生成」）
        out_dir = os.path.dirname(img) if img else _d
        try:
            r = AD.materialize(theme, product, out_dir=out_dir, record=False,
                               prebuilt={"copy": st.get("copy"),
                                         "brief": st.get("brief"),
                                         "prompt": used_prompt, "image": img})
        except Exception as e:                        # noqa: BLE001
            logging.exception("广告方案落盘失败")
            r = {"files": [], "plan": "", "ok": False, "note": str(e)}
        plan = r.get("plan") or ""
        if plan:
            # 方案路径写在最前面：右侧工作台的产物列表 / 抽产物路径都按出现顺序取
            head = ("📣 **广告方案**已落盘（创意说明 + 文案 + 主视觉提示词 + 主视觉图）：\n"
                    "`%s`" % plan)
        else:
            head = ("⚠️ 广告方案没写进磁盘（%s）—— 上面的创意说明与文案仍然可用，"
                    "但这次**没有**可交付的方案文件。" % (r.get("note") or "未知原因"))
        # v0.30.17：登记**广告**效果 —— 关键是把"广告语"以**结构化字段**交出去，
        # 而不是让右栏去正文里正则捞。小志 2026-09-17 原话：
        # 「广告语是纯文字展示而不需要 md 文件路径」——上面那个 `head` 里把方案
        # .md 路径放最前面，是 v0.30.6 为"从正文抽产物路径"打的补丁；现在有结构化
        # 通道了，效果区就只吃 `copy`，路径归路径区。
        try:
            self._mark_effect(
                "ad", media=img, path=plan or img, copy=(st.get("copy") or {}),
                paths=[img] if img else [], page=out_dir or _d or "",
                title="📣 " + (theme or "广告").strip()[:12],
                note=("⚠️ 这次没出主视觉（只有文案与方案），如实告知不糊弄。"
                      if not img else
                      "广告语在上、主视觉在中间、方案与路径在下；要改直接说。"))
        except Exception:
            logging.exception("mark ad effect failed")
        return head + ("\n\n" + body if body else "")

    def _create_image(self, req: str) -> str:
        """真出图：brain 精修英文提示词 → 调图像引擎 → 落盘 + 聊天内预览。

        v0.17.2：同一会话内第二句起若是对上一张图的修改（柔光/加小动物/换风格…），
        - 本地 SD WebUI → 真实 img2img（基于上一张图编辑）；
        - 云端引擎（无图生图接口）→ 同目录以延续风格续画新版本，并如实说明；
        - 全新主题（"再画一只狐狸"）→ 目录内新编号出图，互不覆盖。
        """
        conf = CRE.get_engine(self.cfg, "image") or {}

        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        n = 1
        m = re.search(r"(\d+)\s*(?:张|张图|张图片|幅)", req)
        if m:
            n = max(1, min(4, int(m.group(1))))
        size = conf.get("size") or "1024x1024"
        root = CRE.pick_out_root(self.cfg)
        # 同一会话内连续出图/优化上一幅图 → 复用本会话的图片目录（不再每次另建项目）；
        # 会话内第一次出图才新建目录并记住，切会话/重启各归各位。
        pkg = self._ses_img_dir
        fresh_dir = not (pkg and os.path.isdir(pkg))
        if fresh_dir:
            pkg = os.path.join(root, CRE.safe_filename("image", req))
            os.makedirs(pkg, exist_ok=True)
            self._ses_img_dir = pkg
            self._stamp_conv_meta(img_dir=pkg)
        # 目录里已有多张图 → 从下一个编号续写（不覆盖旧图，方便对照迭代效果）
        last_no = 0
        try:
            nos = [int(mm.group(1)) for f in os.listdir(pkg)
                   for mm in [re.match(r"图(\d+)\.png$", f)] if mm]
            last_no = max(nos) if nos else 0
        except Exception:
            last_no = 0
        img_start = last_no + 1
        last_img = os.path.join(pkg, f"图{last_no:02d}.png") if last_no else None
        base_prompt = getattr(self, "_ses_img_prompt", "") or ""
        # 这句是"在上一张基础上改"还是"全新主题"？
        is_edit = bool(last_img) and self._wants_image_edit(req)
        # —— 提示词精修（编辑模式会带上上一张的画面描述，保证主体/画风延续）——
        if is_edit:
            base_en = ("（已有画面：" + base_prompt + "）\n") if base_prompt else ""
            ask = ("这是对上一张图的**修改**任务。保持原有主体、构图与画风，"
                   "只实现下面的修改要求；若上一张英文提示词可用则基于它改写。"
                   "输出一句完整可直接出图的英文提示词，只输出提示词：\n\n"
                   + base_en + req)
            sys_p = "你是图像编辑提示词工程师：英文、精准、尊重原图结构只做要求内的改动。"
        else:
            ask = ("把下面这个画面需求润色成一句可直接出图的**英文提示词**"
                   "（主体/风格/构图/光线/氛围），只输出提示词：\n\n" + req)
            sys_p = "你是文生图提示词工程师：英文为主、精准具体、可直接喂给出图模型。"
        # v0.18.0：把资料库自学的相关知识带进提示词精修——自学不只是聊天能聊，
        # 出图时风格/领域知识（如"这种场景常用构图/色调/专业规范"）也要用上
        kb = ""
        try:
            kb = knowledge.inject_relevant(req, 3)
        except Exception:
            pass
        if kb:
            ask = ask + "\n\n【资料库自学知识（与画面相关时融入提示词，更专业）】\n" + kb
        prompt = req
        try:
            r = self._brain(ask, system=sys_p, max_tokens=700, task="filegen")
            r = re.sub(r"```(?:text|txt)?\s*", "", (r or "").strip()).replace("```", "").strip()
            if len(r) >= 8:
                prompt = r
        except Exception as ex:
            say("（提示词自动精修不可用，将直接用原需求出图：" + str(ex) + "）")
            if is_edit and base_prompt:
                # 编辑模式精修失败：用上一张的提示词+本次要求拼兜底，绝不丢掉原图语境
                prompt = base_prompt + ", " + req
        use_img2img = is_edit and CRE.supports_img2img(conf) and last_img
        strength = self._img_edit_strength(req) if is_edit else 0.62
        paths = []
        if is_edit:
            if use_img2img:
                say(f"🎨 正在**基于上一张图（图{last_no:02d}.png）修改**"
                    f"（{'轻改·保留构图' if strength <= 0.4 else '大改'} · {n} 张 · "
                    f"{size}，约 15~60 秒/张）……")
            else:
                say(f"🎨 正在按修改要求以**延续风格**重新出图（{n} 张 · {size}……）。"
                    "当前引擎不带图生图编辑，画的是同风格新版本（与上一张同一文件夹）")
        else:
            say(f"🎨 正在出图（{n} 张 · {size}，每张约 10~60 秒）……")
        for j, idx in enumerate(range(img_start, img_start + n)):
            tag = "基于上一张修改" if (use_img2img and j == 0) else ""
            say(f"…第 {j + 1}/{n} 张生成中（存为 图{idx:02d}.png"
                + ("，底图=上一张" if (use_img2img and j == 0) else "") + "）")
            try:
                p = CRE.generate_image(conf, prompt,
                                       os.path.join(pkg, f"图{idx:02d}.png"),
                                       size, init_image=last_img if use_img2img else None,
                                       strength=strength)
                paths.append((idx, p))
                say(f"…第 {j + 1}/{n} 张完成")
            except Exception as ex:
                say(f"…第 {j + 1}/{n} 张出图失败：{ex}")
                # img2img 异常 → 降级为同风格续画新图（至少不让这轮空手）
                if use_img2img and not paths:
                    use_img2img = False
                    say("（基于上一张编辑失败，已降级为延续风格重新出图）")
                    try:
                        p = CRE.generate_image(conf, prompt,
                                               os.path.join(pkg, f"图{idx:02d}.png"),
                                               size)
                        paths.append((idx, p))
                        say(f"…第 {j + 1}/{n} 张（降级版）完成")
                    except Exception as ex2:
                        say(f"…降级出图也失败：{ex2}")
        if not paths:
            return ("这次没能成功出图。请检查：\n"
                    "1) 设置 → 创作引擎 → 测试连接，确认引擎可用；\n"
                    "2) 换一家服务商或换模型再试；\n"
                    "3) 出图服务对英文提示词更友好。\n\n"
                    "需要的话我可以先按你的需求给出可直接出图的提示词包：直接说"
                    "“把刚才的需求写成提示词”。")
        # 记住"上一张"的精修提示词 → 下一轮"再优化一下"能保持主体与画风
        self._ses_img_prompt = prompt
        self._ses_img_req = req
        self._stamp_conv_meta(img_dir=pkg, img_prompt=prompt, img_req=req[:200])
        tag_edit = "在上一张图的基础上修改" if is_edit and use_img2img else (
            "延续风格的迭代版" if is_edit else "新图")
        if not fresh_dir:
            say(f"（{tag_edit}已追加到同一文件夹：`{pkg}`，不会覆盖之前的图）")
        lines = [f"✅ 图出好了 {len(paths)} 张（{tag_edit}）：\n`{pkg}`"]
        for idx, p in paths:
            lines += ["", f"![图{idx:02d}]({CRE._as_uri(p)})"]
        try:
            PLATFORM_OPS.startfile(pkg)
        except Exception:
            pass
        # v0.30.17：登记效果 → 右栏「产出」自动开一个效果页（图在上、路径在下）
        try:
            _last = paths[-1][1] if paths else ""
            self._mark_effect(
                "image", media=_last, path=_last, paths=[pkg], page=pkg,
                title="🖼 " + (req or "图片").strip()[:12],
                note="要在这张上继续改：点下面「💬 下一步…」直接说改哪里。")
        except Exception:
            logging.exception("mark image effect failed")
        lines += ["", "引擎已接好，下次出图免配置。要再调整这版，直接说改哪里（如"
                      "“光再柔和些”“加一只小狗”），我会在这一张的基础上继续改。"]
        return "\n".join(lines)

    def _create_story(self, kind: str, req: str) -> str:
        """图文短片/漫剧一条龙成片：企划 → 逐镜出图 → 配音 → HTML 播放器(+MP4)。"""
        conf = CRE.get_engine(self.cfg, "image") or {}

        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        is_manga = kind == "manga"
        lab = "漫剧" if is_manga else "图文短片"
        max_scenes = 5 if is_manga else 6
        say(f"🎬 开始创作「{lab}」成片：企划 → 分镜 → 逐镜出图 → 配音 → 合成"
            f"（全程约 1~4 分钟，中途可看进度）")
        system = (
            "你是资深%s编导。围绕用户题材产出一份**可直接开拍的结构化企划**，"
            "只输出一个 JSON 对象、不要任何解释（不要代码块外的文字）：\n"
            '{"title":"片名","style":"统一画风(一句话)",'
            '"scenes":[{"narration":"旁白解说10~40字中文",'
            '"subtitle":"画面字幕短句",'
            '"prompt":"本镜英文文生图提示词（含风格/主体/动作/景别/光线，一段话可出图）"}]}\n'
            "要求：scene 数量 3~%d 个；画面适合竖屏讲故事；剧情有起承转合、结尾留钩子；"
            "prompt 全英文且具体到可直接出图；不产出侵权/血腥/违规内容。"
            % ("漫剧" if is_manga else "短视频", max_scenes))
        # v0.18.0：资料库自学知识注入企划（题材相关的专业知识让剧本更有深度与专业度）
        try:
            _kb = knowledge.inject_relevant(req, 3)
        except Exception:
            _kb = ""
        if _kb:
            system += "\n【资料库自学知识（与题材相关时自然融入创作，更专业）】\n" + _kb
        try:
            ans = self._brain("题材：" + req, system=system,
                              max_tokens=2800, task="filegen")
        except Exception as ex:
            return ("创作需要“大脑”在线（云端 Key 或本地 Ollama）才能写企划，"
                    "当前不可用：" + str(ex))
        story = CRE.normalize_story(ans, kind, max_scenes)
        if not story:
            return ("模型这次没给出可分镜的结构化企划，下面是它实际产出的内容"
                    "（可再试一次，或换更具体的题材）：\n\n" + (ans or "")[:1800])
        scenes = story["scenes"]
        root = CRE.pick_out_root(self.cfg)
        # 同一会话内"在这个基础上重做" → 复用本会话成片目录（不再另起新项目）；
        # 复用前把上一版 图/配音/播放器 整体归档进「旧版_时间」，最新版始终在根目录。
        pkg = self._ses_story_dir
        fresh_dir = not (pkg and os.path.isdir(pkg))
        if fresh_dir:
            pkg = os.path.join(root, CRE.safe_filename(kind,
                                                       story.get("title") or req))
            os.makedirs(pkg, exist_ok=True)
            self._ses_story_dir = pkg
            self._stamp_conv_meta(story_dir=pkg)
        else:
            olds = [f for f in os.listdir(pkg)
                    if re.search(r"\.(png|mp3|html)$", f, re.I)]
            if olds:
                arc = os.path.join(pkg, "旧版_" + time.strftime("%H%M%S"))
                try:
                    os.makedirs(arc, exist_ok=True)
                    for f in olds:
                        os.replace(os.path.join(pkg, f), os.path.join(arc, f))
                except Exception:
                    pass
        outline = "\n".join("  · 第%d镜 %s" % (sc["idx"], (sc.get("subtitle")
                         or sc.get("narration") or "")[:34]) for sc in scenes)
        say(f"📝 企划《{story.get('title') or '未命名'}》（{len(scenes)} 镜）：\n{outline}")
        voice = conf.get("voice") or CRE.DEFAULT_VOICE
        self._vp_memo_p = None               # v0.22.1：新一轮成片重新预检 i2v 引擎
        frames = []
        total = len(scenes)
        for i, sc in enumerate(scenes, 1):
            say(f"🎨 第 {i}/{total} 镜：出图…")
            prompt = (sc.get("prompt") or "").strip()
            if not prompt and story.get("style"):
                prompt = ((sc.get("visual") or sc.get("subtitle") or "") + ", "
                          + story["style"]).strip()
            if not prompt:
                prompt = sc.get("visual") or req
            img = None
            try:
                img = CRE.generate_image(
                    conf, prompt, os.path.join(pkg, f"镜{i:02d}.png"),
                    conf.get("size") or "1024x1024")
            except Exception as ex:
                say(f"…第 {i}/{total} 镜出图失败：{ex}")
            aud = None
            narr = (sc.get("narration") or "").strip()
            if narr:
                say(f"🎙 第 {i}/{total} 镜：配音…")
                aud = CRE.synth_speech(narr, os.path.join(pkg, f"配音{i:02d}.mp3"),
                                       voice, log=say)
            clip = self._anim_clip(img, pkg, i, narr,
                                   sc.get("visual") or "", say) if img else None
            if img or aud:
                frames.append({"file": clip or img, "audio": aud, "narration": narr,
                               "subtitle": sc.get("subtitle") or narr})
            say(f"…第 {i}/{total} 镜就绪（图 {'✓' if img else '✗'} · "
                f"配音 {'✓' if aud else '—'} · "
                f"动画 {'✓' if clip else '—'}）")
        if not frames:
            return ("成片失败：所有镜头都没能出图/配音。请先到 设置 → 创作引擎 → "
                    "测试连接确认引擎可用，再重发一次。")
        try:
            player = CRE.render_html_player(pkg, story, frames)
        except Exception as ex:
            return "成片渲染失败：" + str(ex)
        try:
            PLATFORM_OPS.startfile(pkg)
        except Exception:
            pass
        ff = self._ensure_ff()
        mp4 = ""
        if ff:
            say("🎞 正在合成 MP4 成片（运镜+转场+字幕+淡入淡出）…")
            try:
                mp4 = CRE.render_mp4(pkg, frames, ff)
                say("…MP4 合成完成")
            except Exception as ex:
                say("…MP4 合成失败（保留 HTML 播放器）：" + str(ex))
        lines = [f"🎬《{story.get('title') or '未命名'}》{lab}成片做好了"
                 f"（{len(frames)} 镜）！",
                 f"\n📁 全部文件：`{pkg}`" + ("" if fresh_dir else
                 "\n（本会话重做成片 → 上一版已归档到「旧版_时间」子目录，可对照）"),
                 f"\n▶ 放映：双击目录里的「播放器.html」（含画面+配音+运镜转场，浏览器直接放）"]
        if mp4:
            lines.append(f"\n🎞 视频成片：`{mp4}`")
        elif not ff:
            lines.append("\n提示：装好 ffmpeg（https://ffmpeg.org）后重发本需求，"
                         "可额外合成真实 MP4 视频；现在这个播放器无需任何工具即可放映。")
        if len(frames) < total:
            lines.append(f"\n注：{total - len(frames)} 镜因引擎/网络原因跳过，"
                         "其余镜头已正常成片。")
        # v0.30.17：登记视频 / 漫剧效果。有 MP4 就用它（能播），否则退回播放器 HTML。
        # 诚实边界：右栏现在**还没有内嵌播放器**（全仓无 QMediaPlayer）——
        # v0.31.0 随真浏览器（WebEngine）一起来；这一版先把文件与路径摆好，
        # 并如实说明"现在要点打开用系统播放器看"，不假装右栏能放。
        try:
            _media = mp4 or player or ""
            self._mark_effect(
                "manga" if is_manga else "video",
                media=_media, path=_media or pkg, paths=[pkg], page=pkg,
                copy="🎬《%s》· %d 镜%s" % (story.get("title") or "未命名",
                                           len(frames),
                                           "" if mp4 else "（无 MP4，HTML 播放器成片）"),
                title=("📖 " if is_manga else "🎬 ")
                      + str(story.get("title") or lab)[:12],
                note="右栏暂时只列文件（内嵌播放器在 v0.31.0）；"
                     "现在点「↗ 打开」用系统播放器看。")
        except Exception:
            logging.exception("mark story effect failed")
        lines.append("\n要改剧情/画风/镜头数量，直接说，我在这个基础上重做。")
        return "\n".join(lines)

    # ================= 漫剧分步专业流程（v0.18.0） =================
    # 一条龙"感觉不专业、改不了" → 拆成：企划 → 改镜 → 出图 → 配音 → 合成，
    # 每步可停、可回改；企划落盘 story.json，切会话/重启都能接着做。

    def _anim_clip(self, img, pkg, idx, narr: str, visual: str, say) -> str:
        """图生视频（v0.21.0）：把静态分镜图变成会动的片段。

        引擎来自 设置 → 视频引擎（即梦 Seedance / 本地 ComfyUI）；
        单镜失败返回 None（该镜退回静态 Ken Burns 运镜），不拖垮整条片。
        """
        if VE is None:
            return None
        try:
            if VE.provider_of(self.cfg) == "none":
                return None
        except Exception:
            return None
        # v0.22.1 预检：引擎没配好（workflow 缺失/Key 空/服务没起）→ 整片明确提示，
        # 不再逐镜静默失败（以前每镜都报一次失败、用户看到的就是"静态图+配音"）。
        if not getattr(self, "_vp_ok", True):
            ok, why = self._vp_ok, self._vp_why
        else:
            ok, why = self._vp_preflight()
        if not ok:
            key = "vp|" + str(VE.provider_of(self.cfg))
            if getattr(self, "_vp_warned", "") != key:
                self._vp_warned = key
                self._ui(lambda w=why: self._append(
                    "系统", "⚠️ 图生视频引擎未就绪，本片暂用静态画面合成"
                            "（引擎配好后重发即可出真动态）：\n" + w))
            return None
        if not img or not os.path.isfile(img):
            return None
        if os.path.isfile(os.path.join(pkg, f"动画{idx:02d}.mp4")):
            return os.path.join(pkg, f"动画{idx:02d}.mp4")   # 已生成过，不重烧
        motion = "；".join(x for x in (narr, visual) if x)[:300] \
            or "镜头缓慢推进，画面自然流动"
        out = os.path.join(pkg, f"动画{idx:02d}.mp4")
        say(f"🎬 第 {idx} 镜：图生视频（把画面变成会动的片段）…")
        try:
            VE.gen_i2v(self.cfg, img, motion, out,
                       duration=(10 if len(narr) > 60 else 5),
                       progress=lambda m: say(m))
            say(f"…第 {idx} 镜动画完成")
            return out
        except Exception as ex:
            say(f"…第 {idx} 镜转视频失败（改用静态运镜）：{ex}")
            return None

    def _vp_preflight(self):
        """v0.22.1 i2v 引擎预检（带缓存：已就绪的引擎本场不重复探测网络）。"""
        if VE is None:
            self._vp_ok, self._vp_why = True, ""
            return True, ""
        try:
            p = VE.provider_of(self.cfg)
        except Exception:
            self._vp_ok, self._vp_why = True, ""
            return True, ""
        if p == "none":
            self._vp_ok, self._vp_why = True, ""
            return True, ""
        if (getattr(self, "_vp_memo_p", None) == p and
                getattr(self, "_vp_memo_ok", False)):
            self._vp_ok, self._vp_why = True, ""
            return True, ""
        ok, why = True, ""
        try:
            ok, why = VE.ready(self.cfg)
        except Exception as ex:
            ok, why = False, str(ex)
        self._vp_memo_p, self._vp_memo_ok = p, ok
        self._vp_ok, self._vp_why = ok, why
        return ok, why

    def _ensure_ff(self) -> str:
        """确保 ffmpeg 可用（v0.20.0 系统集成）：探测失败自动下载静态构建。

        下载进度实时打到会话窗；失败如实说明并给手动安装指引，返回空串。
        """
        ff = CRE.find_ffmpeg()
        if ff:
            return ff

        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        try:
            return CRE.ensure_ffmpeg(say)
        except Exception as ex:
            say("⚠ " + str(ex) +
                "\n（也可到 设置 → 创作引擎 手动装 ffmpeg 后重试）")
            return ""

    def _story_pkg(self, title: str, req: str = "", kind: str = "manga") -> str:
        """成片目录（沿用会话归属：同一会话续作复用同一目录）。"""
        pkg = self._ses_story_dir
        if not (pkg and os.path.isdir(pkg)):
            root = CRE.pick_out_root(self.cfg)
            pkg = os.path.join(root, CRE.safe_filename(kind, title or req or kind))
            os.makedirs(pkg, exist_ok=True)
            self._ses_story_dir = pkg
        self._stamp_conv_meta(story_dir=pkg)
        return pkg

    def _story_save(self, pkg: str, story: dict):
        try:
            _save_json(os.path.join(pkg, "story.json"), story)
        except Exception:
            pass

    def _story_load(self) -> dict | None:
        pkg = getattr(self, "_ses_story_dir", None)
        if pkg and os.path.isdir(pkg):
            d = _load_json(os.path.join(pkg, "story.json"), {})
            if isinstance(d, dict) and d.get("scenes"):
                return d
        return None

    def _manga_note(self, out: str):
        """把一个漫剧工序的产出**追加**到同一个漫剧产出页。

        小志：「漫剧这种也是一样，每个工序均向下展开」——
        企划 / 出图 / 配音 / 合成每一步的产出都往同一页后面接一块，
        所以右栏里能看到这部漫剧从企划到成片的完整过程，而不是只剩最后一步。
        """
        try:
            pkg = getattr(self, "_ses_story_dir", "") or ""
            self._mark_effect("manga", page=pkg or "manga",
                              copy=(out or ""), path=pkg,
                              title="📖 漫剧", note="")
        except Exception:
            logging.exception("manga note failed")

    def _manga_dispatch(self, req: str) -> str:
        """漫剧栏目输入分发：一条龙口令 / 企划 / 改镜 / 出图 / 配音 / 合成。

        v0.31.0：每个分支的产出都会**追加**进同一个漫剧产出页（`_manga_note`），
        于是"逐工序向下展开"在右栏看得见。
        """
        s = (req or "").strip()
        story = getattr(self, "_ses_story", None) or self._story_load()
        self._ses_story = story
        if re.search(r"一条龙|直接成片|一键成片|全自动|从头到尾|全部帮我做完", s):
            self._ses_story = None
            topic = re.sub(r"(一条龙|直接成片|一键成片|全自动|从头到尾|全部帮我做完|做|吧|一下)",
                           " ", s).strip(" ，。,.") or s
            return self._create_story("manga", topic)
        if story is None or re.search(r"重新?企划|新企划|换个?题材|先出企划", s):
            topic = re.sub(r"^(帮我?|请|麻烦|给我?)?\s*(先)?(出|做|写|生成)?(一个|一份)?"
                           r"(漫剧|漫画)?(企划|剧本|分镜)[：:，, ]*", "", s).strip()
            if not topic or len(topic) < 2 or re.search(r"企划|剧本|分镜", topic) and len(topic) < 4:
                topic = re.sub(r"(企划|剧本|分镜|漫剧|漫画)", " ", s).strip(" ，。,.：:") or s
            r = self._manga_plan(topic)
            self._manga_note(r)
            return r
        m = re.search(r"(?:重画|重绘|重出)\s*第?\s*([0-9一二三四五六七八九十]+)\s*镜", s)
        if m:
            r = self._manga_shots(only=_cn_num(m.group(1)))
            self._manga_note(r)
            return r
        m = re.search(r"第\s*([0-9一二三四五六七八九十]+)\s*镜", s)
        if m and re.search(r"改成|换成|改为|改成来|调整|修改|变成|换成这样", s):
            r = self._manga_edit_scene(_cn_num(m.group(1)), s)
            self._manga_note(r)
            return r
        if m and re.search(r"画|出图|重画|重绘", s):
            r = self._manga_shots(only=_cn_num(m.group(1)))
            self._manga_note(r)
            return r
        if re.search(r"配音|旁白|加音|音频", s):
            r = self._manga_voice()
            self._manga_note(r)
            return r
        if re.search(r"合成|成片|导出|出视频|生成视频|播放器|最终", s):
            r = self._manga_render()
            self._manga_note(r)
            return r
        if re.search(r"出图|开画|继续画|都画|画吧|继续", s):
            r = self._manga_shots()
            self._manga_note(r)
            return r
        # 不认识的输入 → 当新要求改当前镜头最近的？稳妥：提示可用步骤
        return ("这份企划进行中。你可以：\n"
                "· 「出图」——逐镜画（未画的镜）\n"
                "· 「重画第2镜」——重画某一镜\n"
                "· 「第2镜改成 夜晚的街道」——改某一镜的内容\n"
                "· 「配音」——逐镜配旁白\n"
                "· 「合成」——出成片（播放器 + MP4）\n"
                "· 「一条龙」——不想分步，一键做完\n"
                "· 直接说新题材 = 重新企划。")

    def _manga_plan(self, req: str) -> str:
        """第①步：只出企划（分镜表），不出图。落盘 story.json 等确认。"""
        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        say("📝 正在写漫剧企划（分镜剧本）……")
        system = (
            "你是资深漫剧编导。围绕用户题材产出一份**可直接开拍的结构化企划**，"
            "只输出一个 JSON 对象、不要任何解释（不要代码块外的文字）：\n"
            '{"title":"片名","style":"统一画风(一句话)",'
            '"scenes":[{"narration":"旁白解说10~40字中文",'
            '"subtitle":"画面字幕短句",'
            '"prompt":"本镜英文文生图提示词（含风格/主体/动作/景别/光线，一段话可出图）"}]}\n'
            "要求：scene 数量 3~5 个；画面适合竖屏讲故事；剧情有起承转合、结尾留钩子；"
            "prompt 全英文且具体到可直接出图；不产出侵权/血腥/违规内容。")
        try:
            _kb = knowledge.inject_relevant(req, 3)
        except Exception:
            _kb = ""
        if _kb:
            system += "\n【资料库自学知识（与题材相关时自然融入创作，更专业）】\n" + _kb
        try:
            ans = self._brain("题材：" + req, system=system,
                              max_tokens=2800, task="filegen")
        except Exception as ex:
            return ("写企划需要「大脑」在线（云端 Key 或本地 Ollama）："
                    + str(ex))
        story = CRE.normalize_story(ans, "manga", 5)
        if not story:
            return ("这次没给出可分镜的企划，模型原始输出如下（可再说一次，"
                    "或把题材说得更具体）：\n\n" + (ans or "")[:1800])
        pkg = self._story_pkg(story.get("title") or req, req)
        self._story_save(pkg, story)
        self._ses_story = story
        lines = [f"📝 漫剧企划《{story.get('title') or '未命名'}》好了（{len(story['scenes'])} 镜）：",
                 ""]
        for sc in story["scenes"]:
            lines.append(f"**第{sc['idx']}镜**　{sc.get('subtitle') or sc.get('narration') or ''}")
            if sc.get("narration"):
                lines.append(f"　旁白：{sc['narration']}")
            if sc.get("prompt"):
                lines.append(f"　画面：{sc['prompt'][:84]}")
        lines += ["", "—— 分步制作 ——",
                  "① 满意 → 说「出图」逐镜画；",
                  "② 想改某镜 → 说「第2镜改成 夜晚的霓虹街道」；",
                  "③ 画完 → 说「配音」；最后说「合成」出成片；",
                  "④ 不想分步 → 说「一条龙」一键做完。",
                  f"（企划已存 story.json，切会话/重启都能接着做：`{pkg}`）"]
        return "\n".join(lines)

    def _manga_shots(self, only: int | None = None) -> str:
        """第②步：逐镜出图（only 指定只画/重画某一镜）。"""
        story = getattr(self, "_ses_story", None) or self._story_load()
        if not story:
            return "还没有企划。先给我题材，我先出分镜企划。"
        conf = CRE.get_engine(self.cfg, "image") or {}
        if not CRE.conf_ok(conf):
            return "出图引擎还没接入：请到 设置 → 创作引擎 接入后，再说「出图」。"

        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        pkg = self._story_pkg(story.get("title") or "漫剧")
        targets = [sc for sc in story["scenes"]
                   if (only is None or sc["idx"] == only)]
        done = 0
        for sc in targets:
            i = sc["idx"]
            say(f"🎨 第 {i}/{len(story['scenes'])} 镜：出图…")
            prompt = (sc.get("prompt") or "").strip()
            if not prompt:
                prompt = ((sc.get("visual") or sc.get("subtitle") or "")
                          + ", " + (story.get("style") or "")).strip(", ")
            if not prompt:
                prompt = "manga scene, detailed illustration"
            try:
                CRE.generate_image(conf, prompt,
                                   os.path.join(pkg, f"镜{i:02d}.png"),
                                   conf.get("size") or "1024x1024")
                sc["img"] = f"镜{i:02d}.png"
                done += 1
                say(f"…第 {i} 镜完成")
            except Exception as ex:
                say(f"…第 {i} 镜出图失败：{ex}")
        self._story_save(pkg, story)
        self._ses_story = story
        if not done:
            return "这轮一张都没画出来。请检查 设置 → 创作引擎 → 测试连接，再试一次。"
        if only is not None:
            head = f"✅ 第{only}镜已重画。"
        else:
            head = f"✅ 出图完成（本轮 {done}/{len(targets)} 镜）。"
        undrawn = [sc["idx"] for sc in story["scenes"]
                   if not (sc.get("img") and os.path.exists(os.path.join(pkg, sc["img"])))]
        lines = [head, f"📁 `{pkg}`"]
        if undrawn:
            lines.append(f"还有 {len(undrawn)} 镜未出图（第 {'、'.join(map(str, undrawn))} 镜），"
                         "说「出图」继续。")
        lines.append("下一步：说「配音」逐镜配旁白；某镜不满意就说「重画第N镜」。")
        return "\n".join(lines)

    def _manga_voice(self) -> str:
        """第③步：逐镜配音（旁白 → mp3）。"""
        story = getattr(self, "_ses_story", None) or self._story_load()
        if not story:
            return "还没有企划。先给我题材出企划。"
        conf = CRE.get_engine(self.cfg, "image") or {}
        pkg = self._story_pkg(story.get("title") or "漫剧")
        voice = conf.get("voice") or CRE.DEFAULT_VOICE

        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        ok = []
        for sc in story["scenes"]:
            narr = (sc.get("narration") or "").strip()
            if not narr:
                continue
            say(f"🎙 第 {sc['idx']} 镜：配音…")
            aud = CRE.synth_speech(narr, os.path.join(pkg, f"配音{sc['idx']:02d}.mp3"),
                                   voice, log=say)
            if aud:
                sc["audio"] = f"配音{sc['idx']:02d}.mp3"
                ok.append(sc["idx"])
        self._story_save(pkg, story)
        self._ses_story = story
        if not ok:
            return ("配音没成功（edge-tts 需要联网）。可以跳过直接说「合成」，"
                    "播放器会用字幕推进。")
        return (f"🎙 配音完成（第 {'、'.join(map(str, ok))} 镜）。\n"
                "下一步：说「合成」出成片（HTML 播放器 + MP4）。")

    def _manga_render(self) -> str:
        """第④步：合成成片（HTML 播放器 + 有 ffmpeg 时 MP4）。"""
        story = getattr(self, "_ses_story", None) or self._story_load()
        if not story:
            return "还没有企划。先给我题材出企划。"
        pkg = self._story_pkg(story.get("title") or "漫剧")
        self._vp_memo_p = None               # v0.22.1：新一轮成片重新预检 i2v 引擎

        def say(m):
            self._ui(lambda m=m: self._append("系统", m))
        frames = []
        # —— 图生视频（v0.21.0）：有引擎时逐镜把分镜图转成动态片段 ——
        dirty = False
        for sc in story["scenes"]:
            img = os.path.join(pkg, sc["img"]) if (sc.get("img") and
                os.path.exists(os.path.join(pkg, sc["img"]))) else None
            if not img:
                continue
            clip = self._anim_clip(img, pkg, sc["idx"],
                                   sc.get("narration") or "",
                                   sc.get("visual") or "", say)
            if clip:
                if sc.get("clip") != os.path.basename(clip):
                    sc["clip"] = os.path.basename(clip)
                    dirty = True
        if dirty:
            self._story_save(pkg, story)
        for sc in story["scenes"]:
            i = sc["idx"]
            img = os.path.join(pkg, sc["img"]) if (sc.get("img") and
                os.path.exists(os.path.join(pkg, sc["img"]))) else None
            aud = os.path.join(pkg, sc["audio"]) if (sc.get("audio") and
                os.path.exists(os.path.join(pkg, sc["audio"]))) else None
            clip = os.path.join(pkg, sc["clip"]) if (sc.get("clip") and
                os.path.exists(os.path.join(pkg, sc["clip"]))) else None
            if img or aud:
                frames.append({"file": clip or img, "audio": aud,
                               "narration": sc.get("narration") or "",
                               "subtitle": sc.get("subtitle") or sc.get("narration") or ""})
        if not frames:
            return ("还没有可合成的镜头。先说「出图」（画面必须有），"
                    "配音可跳过。")
        try:
            CRE.render_html_player(pkg, story, frames)
        except Exception as ex:
            return "成片渲染失败：" + str(ex)
        try:
            PLATFORM_OPS.startfile(pkg)
        except Exception:
            pass
        mp4 = ""
        ff = self._ensure_ff()
        if ff:
            self._ui(lambda: self._append(
                "系统", "🎞 正在合成 MP4 成片（运镜+转场+字幕+淡入淡出）…"))
            try:
                mp4 = CRE.render_mp4(pkg, frames, ff)
                self._ui(lambda: self._append("系统", "…MP4 合成完成"))
            except Exception as ex:
                mp4 = ""
                self._ui(lambda: self._append(
                    "系统", "…MP4 合成失败（保留 HTML 播放器）：" + str(ex)))
        lines = [f"🎬《{story.get('title') or '未命名'}》成片好了（{len(frames)} 镜）！",
                 f"\n📁 `{pkg}`",
                 "\n▶ 双击目录里的「播放器.html」放映（画面+配音+运镜转场）。"]
        if mp4:
            lines.append(f"\n🎞 MP4：`{mp4}`")
        undrawn = [sc["idx"] for sc in story["scenes"]
                   if not (sc.get("img") and os.path.exists(os.path.join(pkg, sc["img"])))]
        if undrawn:
            lines.append(f"\n注：第 {'、'.join(map(str, undrawn))} 镜还没出图，"
                         "先「出图」再「合成」即可补齐。")
        return "\n".join(lines)

    def _manga_edit_scene(self, n: int, desc: str) -> str:
        """改镜：按用户要求重写某一镜（保持画风与剧情连贯），改完提示重画。"""
        story = getattr(self, "_ses_story", None) or self._story_load()
        if not story:
            return "还没有企划。先给我题材出企划。"
        sc = next((x for x in story["scenes"] if x["idx"] == n), None)
        if not sc:
            return f"企划里没有第{n}镜（共 {len(story['scenes'])} 镜）。"
        try:
            ans = self._brain(
                "原分镜 JSON：\n" + json.dumps(sc, ensure_ascii=False)
                + "\n\n用户要求：" + desc
                + "\n\n只输出修改后的该镜 JSON 对象（键：narration, subtitle, prompt），"
                  "prompt 为一段可直接出图的英文，不要解释。",
                system="你是漫剧分镜师：保持整体画风与剧情连贯，只按用户要求修改这一个镜头。",
                max_tokens=800, task="brain")
            obj = CRE._extract_json(ans or "")
            if isinstance(obj, dict):
                for k in ("narration", "subtitle", "prompt"):
                    if isinstance(obj.get(k), str) and obj[k].strip():
                        sc[k] = obj[k].strip()
        except Exception as ex:
            return ("改镜需要「大脑」在线（云端 Key 或本地 Ollama）：" + str(ex))
        sc.pop("img", None)                      # 内容变了 → 旧图作废，须重画
        pkg = self._story_pkg(story.get("title") or "漫剧")
        self._story_save(pkg, story)
        self._ses_story = story
        return (f"✏️ 第{n}镜已改：\n"
                f"　旁白：{sc.get('narration') or '（无）'}\n"
                f"　字幕：{sc.get('subtitle') or '（无）'}\n"
                f"　画面：{(sc.get('prompt') or '')[:84]}\n"
                f"说「重画第{n}镜」重画这一镜；或「出图」补画所有未画的镜。")

    def _pet_clicked(self):
        v = self.agent.snapshot()["emotion"]["valence"]
        if v > 0.2:
            lines = ["（被你戳得晃了晃）嘿嘿，我在！", "摸头收到~ 我今天很开心哦！"]
        elif v < -0.2:
            lines = ["（被你戳得晃了晃）我有点低落…陪我聊聊天好吗？", "唔…你戳到我的小心思了。"]
        else:
            lines = ["（被你戳得晃了晃）我在呢，想聊什么？", "呀！你戳到我了~"]
        phrase = lines[hash(time.strftime("%S")) % len(lines)]
        self._append(self.cfg["name"], phrase)
        self.speak("嘿嘿，我在这里呀")
        self.avatar.poke()

    def _refresh_voice_btns(self):
        """v0.27.4：统一刷新语音三键的可见状态。

        真机反馈"右侧按钮状态并不清楚"——现在用文字+配色明确表达：
        朗读开(绿)/关(灰)、朗读中(暂停/停止变亮蓝/红)、暂停中(显示"▶ 继续")。
        """
        on = bool(self.cfg.get("auto_speak"))
        try:
            self.speak_btn.setText("🔊 朗读:开" if on else "🔇 朗读:关")
            self.speak_btn.setStyleSheet(
                "QPushButton{border-radius:6px;padding:2px 10px;border:1px solid %s;"
                "background:%s;color:%s;}"
                % (("#16a34a", "#dcfce7", "#15803d") if on
                   else ("#cbd5e1", "#f1f5f9", "#64748b")))
        except Exception:
            pass
        try:
            import tts as _t
            speaking = _t.is_speaking()
            paused = bool(_t._PAUSE.is_set())
        except Exception:
            speaking, paused = False, False
        try:
            self.pause_btn.setText("▶ 继续" if paused else "⏸ 暂停")
            self.pause_btn.setStyleSheet(
                "QPushButton{border-radius:6px;padding:2px 8px;border:1px solid %s;"
                "background:%s;color:%s;}"
                % (("#2563eb", "#dbeafe", "#1d4ed8") if speaking
                   else ("#e2e8f0", "#ffffff", "#94a3b8")))
            self.stop_btn.setStyleSheet(
                "QPushButton{border-radius:6px;padding:2px 8px;border:1px solid %s;"
                "background:%s;color:%s;}"
                % (("#dc2626", "#fee2e2", "#b91c1c") if speaking
                   else ("#e2e8f0", "#ffffff", "#94a3b8")))
        except Exception:
            pass

    def _voice_tick(self):
        """朗读期间每 0.6s 刷新一次按钮状态，读完自动停（避免按钮状态卡住）。"""
        self._refresh_voice_btns()
        try:
            import tts as _t
            if _t.is_speaking():
                QTimer.singleShot(600, self._voice_tick)
        except Exception:
            pass

    def _toggle_pause_speak(self):
        """⏸/▶ 暂停或继续朗读（不丢弃进度）。"""
        try:
            st = tts_mod.toggle_pause()
        except Exception:
            st = "idle"
        if st == "paused":
            self._append("系统", "朗读已暂停 —— 再点「▶ 继续」从暂停处接着念。")
        elif st == "resumed":
            self._append("系统", "继续朗读。")
        else:
            self._append("系统", "现在没有在朗读。点「🔊 朗读:开」后，每轮回复会自动读给你听。")
        self._refresh_voice_btns()

    def _stop_speak_now(self):
        """⏹ 立刻停掉当前这句（保留自动朗读开关，下一句照常读）。"""
        try:
            was = tts_mod.stop_speaking()
        except Exception:
            was = False
        self._append("系统", "已经停下了。" if was else "当前没有在读。")
        self._refresh_voice_btns()

    def _classify_intent(self, text):
        """工作栏目语义判读：区分 新开工指令 / 疑问或分析请求 / 修改 / 闲聊。

        返回 'order' | 'question' | 'correction' | 'chat'。
        工作栏目里只有 'order' 才强制走 CHIP_SEED 开工；其余交给模型按上下文
        分析/回答/讨论，避免"不管对不对就开干"。"""
        t = (text or "").strip()
        if not t:
            return "chat"
        # 疑问 / 求分析：应当解释、讨论，不盲干
        _q = ("？", "?", "吗", "么", "呢", "啥", "怎么", "怎样", "如何", "为什么",
              "为啥", "为咩", "几多", "几时", "哪里", "哪个", "是否", "可否", "能不能",
              "可唔可以", "系咪", "係唔係", "对不对", "是不是", "有没有", "有无",
              "解释", "说明", "分析", "讲讲", "说说", "讲一下", "讲下", "介绍下",
              "介绍", "概括", "总结", "对比", "评价", "点评", "解读", "看看",
              "分析一下", "什么意思", "咩意思", "点解")
        # 修改类：对已有产物提调整，应由模型在上下文里改，而非重新硬开工
        _c = ("改一下", "改一改", "修改", "调整", "调一下", "重新", "重做",
              "去掉", "删掉", "删除", "加上", "增加", "减少", "缩短", "加长",
              "换一个", "换个", "不要", "改为", "改成", "润色", "优化", "精简",
              "扩充", "补充", "修正", "紧凑", "密啲", "疏啲", "唔好", "唔要",
              "改少少", "调下", "再短", "再长", "再详细", "更简单")
        # 强开工词
        _o = ("帮我", "请帮我", "帮我做", "做个", "做一个", "写一份", "写个",
              "生成", "创作", "开发", "来一个", "来一份", "整一个", "整份",
              "畀我", "俾我", "设计一份", "起草", "拟一份", "出一份", "搞个",
              "来份", "写一版", "出一版")
        # 短确认 / 闲聊
        _ack = ("好", "嗯", "ok", "继续", "收到", "明白", "赞", "对", "是的",
                "系", "係", "可以", "行", "没问题", "好的", "好嘅", "好啊")
        tl = t.lower()
        if any(w in t for w in _q):
            return "question"
        if any(w in t for w in _c):
            return "correction"
        if any(w in t for w in _o):
            return "order"
        if len(t) <= 8 and any(w in tl for w in _ack):
            return "chat"
        # 工作栏目里：非疑问、非修改、非短确认的，默认当作开工指令
        return "order"

    def _voice_input(self):
        """🎤 语音输入：点一下开始听（有提示音），识别结果自动发送。

        v0.27.8：提示音（叮）改由识别引擎**真正进入聆听**那一刻才响——
        听到叮 = 麦克风已经在录，前 1~2 秒不再丢失；此前只会显示「准备中」。"""
        if not asr_mod.has_recognizer():
            self._append("系统", "未检测到中文语音识别引擎。可在 设置→时间和语言→语音 安装"
                                 "「语音识别」后重启本应用使用 🎤 语音输入。")
            return
        self.mic_btn.setEnabled(False)
        self.mic_btn.setText("⏺")
        self._append("系统", "🎤 准备中…（正在打开麦克风）")

        def _on_state(s):
            # 只有真正进入聆听才提示"我在听"（此时叮声已由引擎触发）
            if s == "listening":
                self._append("系统", "🎤 叮——我在听，说完自动识别")

        def done(res):
            # v0.27.3：listen_once 带诊断返回 (text, diag)——旧版把真实错误
            # （没装中文识别器 / 麦克风被占 / 权限没开）全吞成"没听清"，
            # 用户根本无从自查。现在把原因原样说出来。
            if isinstance(res, tuple):
                text, diag = res
            else:
                text, diag = res, ""
            self.mic_btn.setEnabled(True)
            self.mic_btn.setText("🎤")
            if text:
                self.input.setPlainText(text)
                self.input.setFocus()
                self._append("系统", "听到：" + text[:60] + (("\n（" + diag + "）") if diag else ""))
                self.send()
            else:
                self._append("系统", "没听清——**真实原因**：" + (diag or "未知") +
                             "\n\n自查：设置 → 隐私和安全性 → 麦克风 → 打开"
                             "「让桌面应用访问麦克风」；\n"
                             "中文识别需要语音包：设置 → 时间和语言 → 语音 → 安装语音识别。\n"
                             "说「语音自检」我帮你把这几项一次查清楚。")

        # v0.28.4：每按一次🎤都**显式**定语种，绝不沿用上一次的（旧逻辑只切换不复位，
        # 导致"说过一次粤语就永远卡在 zh-HK"，之后说普通话全听不懂）。
        #   · 普通话/四川/河南/北京/云南/东北/山东 → 全部 zh-CN（官话引擎）
        #   · 已学到"粤语"且系统装了粤语识别包 → 主 zh-HK，回落 zh-CN
        #   · 已学到"台湾腔"且装了台湾包 → 主 zh-TW，回落 zh-CN
        _asr_fb = None
        try:
            import accent as _AC
            dn, dscore = _AC.dominant()
            target, fb = "zh-CN", None
            _sup = asr_mod.supported_cultures()
            if dn == "粤语" and dscore >= _AC.MIN_HITS and _sup.get("has_hk"):
                target, fb = "zh-HK", "zh-CN"
            elif dn == "台湾腔" and dscore >= _AC.MIN_HITS and _sup.get("has_tw"):
                target, fb = "zh-TW", "zh-CN"
            # 仅当目标语种与当前引擎不一致才切换，避免每次按🎤都重建引擎
            # （v0.28 的"0.2 秒进入聆听"靠的就是热引擎不被反复重建）
            try:
                if asr_mod.service_info().get("culture") != target:
                    asr_mod.prefer_culture(target)
            except Exception:
                pass
            _asr_fb = fb
        except Exception:
            _asr_fb = None

        asr_mod.listen_async(lambda r: self._ui(lambda: done(r)),
                             timeout=6.0,
                             on_state=lambda s: self._ui(lambda: _on_state(s)),
                             fallback=_asr_fb)

    def _toggle_speak(self):
        self.cfg["auto_speak"] = not self.cfg.get("auto_speak", False)
        _save_json(CONFIG, self.cfg)
        self.speak_btn.setText("朗读:" + ("开" if self.cfg.get("auto_speak") else "关"))
        if not self.cfg.get("auto_speak"):
            try:
                tts_mod.stop_speaking()   # 立刻掐断正在读的 + 作废待读队列
            except Exception:
                pass
        self._append("系统", "语音朗读已" + ("开启（每轮自动朗读回复，马上试一句）"
                                          if self.cfg.get("auto_speak") else "关闭（正在读的已停止）"))
        self._refresh_voice_btns()
        if self.cfg.get("auto_speak"):
            self.speak("朗读开好啦，以后每句话我都念给你听")

    def _emote_word(self) -> str:
        """把当前情绪快照折成朗读语气词（happy/excited/sad/angry/sleepy/''）。"""
        try:
            e = self.agent.snapshot()["emotion"]
            v = e.get("valence", 0.0)
            a = e.get("arousal", 0.0)
            s = e.get("serotonin", 0.0)
        except Exception:
            return ""
        if v > 0.25:
            return "excited" if a > 0.28 else "happy"
        if v < -0.22:
            return "angry" if a > 0.18 else "sad"
        if a < -0.2 or s < -0.3:
            return "sleepy"
        return ""

    def _voice_ctx(self) -> dict:
        """语音上下文：性格 + 性别 + 成长阶段 + 此刻情绪 → tts 选音色/语气。

        v0.27.3：叠加两层"更像人"的调制——
          · persona_style 给的语速/音高倾向（豪爽直率偏低沉、好奇活泼偏轻快）；
          · accent 学到的用户方言 → 换成对应口音（东北→辽宁话音等）。
        """
        try:
            st = GROWTH.pet_state()
        except Exception:
            st = {}
        persona = self.cfg.get("persona", "温和沉稳")
        ctx = {
            "growth": GROWTH.current_growth(),
            "gender": str(st.get("gender") or "none"),
            "persona": persona,
            "engine": self.cfg.get("voice_engine", "auto"),
            "emote": self._emote_word(),
        }
        try:
            import persona_style as _PS
            h = _PS.voice_hint(persona) or {}
            rb = str(h.get("rate") or "0%").replace("%", "").strip()
            pb = str(h.get("pitch") or "0Hz").replace("Hz", "").strip()
            ctx["rate_bias"] = int(rb) if rb.lstrip("+-").isdigit() else 0
            ctx["pitch_bias"] = int(pb) if pb.lstrip("+-").isdigit() else 0
        except Exception:
            pass
        try:
            import accent as _AC
            v = _AC.voice_for(ctx["gender"])
            if v:
                ctx["voice_override"] = v
        except Exception:
            pass
        return ctx

    def speak(self, text):
        """朗读（tts 模块；按 性格/性别/年龄 自动换音色语气；失败提示限频不锁死）。"""
        if not text:
            return
        warn_at = getattr(self, "_tts_warn_at", 0.0)

        def on_result(msg):
            if msg == "ok":
                return
            now = time.time()
            if now - getattr(self, "_tts_warn_at", 0.0) < 90:
                return                      # 同一句失败不再刷屏；仍可重试不锁死
            self._tts_warn_at = now
            self._append("系统", "（语音朗读失败：" + msg +
                         "。可在 设置→时间和语言→语言→中文→语音 安装语音包后重试。）")

        def ui(msg):
            if msg != "ok":
                self._ui(lambda m=msg: on_result(m))

        # v0.27.8：朗读音色跟随"要念的文本语言"——粤语回复用粤语音、繁体用台湾音、
        # 英文用英文音，不再出现"回复是粤语、读出来却是普通话"的别扭。
        ctx = self._voice_ctx()
        try:
            import accent as _AC
            _dial = getattr(self, "_reply_dialect", "")
            self._reply_dialect = ""      # 消费后即清空，避免后续非回复朗读串味
            if not _dial:
                # v0.28.1：用户这句没识别出方言时，再看"要念的这句回复"本身
                # （回复是粤语就用粤语音读，包括系统语音兜底时的声线选择）
                try:
                    _d2, _sc2 = _AC.detect(text or "")
                    if _sc2 > 0:
                        _dial = _d2
                except Exception:
                    pass
            v = _AC.voice_for_text(text, ctx.get("gender", "none"),
                                   fallback=ctx.get("voice_override", ""),
                                   input_dialect=_dial)
            if v:
                ctx["voice_override"] = v
            ctx["lang"] = _dial          # 传给 tts：在线音断了时，系统语音也尽量用对语言
        except Exception:
            pass
        tts_mod.speak_text(text, on_result=ui,
                           log=lambda m: logging.info(m),
                           **ctx)
        self._refresh_voice_btns()                # v0.27.4：立刻反映"开始朗读"
        QTimer.singleShot(600, self._voice_tick)  # 朗读期间持续刷新按钮状态

    def _show_mind(self):
        self._render_mind(self.agent.snapshot())
        self.chat.append(f"<span style='color:#64748b'>它记住的关于你（最近）："
                         f"{'；'.join(n['tag'] for n in self.notes[-5:]) or '还没有'}</span>")

    def _settings(self):
        dlg = SettingsDialog(self.cfg, list_ollama_models(), self)
        if dlg.exec():
            if self.cfg.get("api_key"):
                self.local = {}
                self._set_status(f"云端 · {self.cfg['model']}")
            else:
                self.local = detect_local_llm()
                self._set_status(f"本地 {self.local.get('name','?')}" if self.local else "离线微脑")
            self._apply_persona(reset_agent=self.cfg["persona"] != getattr(self, "_persona_now", ""))
            if hasattr(self, "name_lbl"):
                self.name_lbl.setText(self.cfg.get("name", "小U"))
            if hasattr(self, "brand_lbl"):
                self.brand_lbl.setText(self.cfg.get("name", "小U"))
            self._refresh_model_status()         # 云端 key/模型变化 → 刷新切换下拉
            try:
                self._render_mind(self.agent.snapshot())
            except Exception:
                pass
            self._append("系统", "设置已保存。")

    def _detect_ui(self):
        """「📡 模型」：检测结果用独立弹框展示，绝不写进聊天框（v0.17.3）。"""
        def job():
            local = detect_local_llm()
            ms = list_ollama_models() if local else []
            def put():
                self.local = local
                if local:
                    if not self.cfg.get("local_model"):
                        self.cfg["local_model"] = pick_local_model(ms)
                        _save_json(CONFIG, self.cfg)
                self._refresh_model_status()
                self._box_models(local, ms)
            self._ui(put)
        threading.Thread(target=job, daemon=True).start()

    def _box_models(self, local, ms):
        """弹框显示模型检测结果（不占聊天框、不进会话记录）。"""
        try:
            cur = self._model_label()
        except Exception:
            cur = "自动选择"
        if local:
            QMessageBox.information(
                self, "模型检测",
                f"✅ 本地 Ollama 在线：{local['name']}\n\n"
                f"可用模型：{'、'.join(ms) or '无'}\n\n"
                f"当前大脑：{cur}\n"
                "（底部「大脑」下拉可随时切换 云端 / 本地模型 / 自动）")
        else:
            QMessageBox.information(
                self, "模型检测",
                "未检测到本地 Ollama。\n\n"
                "最简做法：安装 Ollama 后运行\n"
                "　ollama run qwen2.5:1.5b\n\n"
                "保持运行后再点一次即可。\n"
                f"当前大脑：{cur}")



    # ---------- v0.30.4 工作进度面板 ----------
    # ============ v0.30.6：工作侧栏（开合 / 预览 / 产物 / 复制 / 当前工作） ============

    def _reply_html(self, reply: str) -> str:
        """助手回复的最终渲染：**一整块 markdown 卡片** + 下方「📋 复制」。

        为什么不做成"名字：正文"挤一行：DeepSeek 那种观感的关键，是回答被呈现为
        **一块独立内容**（底色 + 圆角 + 边界），复制按钮跟着这一块走 ——
        用户一眼就知道"这块可以整段拿走"。

        实现细节：QTextBrowser 里放不了真按钮，所以复制键是锚点链接
        `pasm://copy/<token>`，由 `_on_anchor` 接住写剪贴板。token 是自增序号，
        原文存 `self._copy_store`（只留最近 30 条，防止无限涨）。
        """
        body = md_to_html(reply or "")
        if not hasattr(self, "_copy_store"):
            self._copy_store = {}
        tok = str(getattr(self, "_copy_seq", 0))
        self._copy_seq = int(tok) + 1
        self._copy_store[tok] = reply or ""
        while len(self._copy_store) > 30:
            self._copy_store.pop(min(self._copy_store, key=lambda k: int(k)), None)
        return ("<div style='background:#f8fafc;border:1px solid #e2e8f0;"
                "border-radius:10px;padding:8px 12px;margin:4px 0;'>"
                + body
                + "<div style='margin-top:6px;'><a href='pasm://copy/" + tok
                + "' style='color:#0369a1;font-size:11px;text-decoration:none;'>"
                "📋 复制</a></div></div>")

    def _right_toggle(self, open_=None):
        """收起 / 展开右侧工作台。

        收起不"消失"：宽度压到 34px，只露出展开键 —— 位置不跑（VSCode 的习惯）。
        """
        if not hasattr(self, "right"):
            return
        if open_ is None:
            open_ = not getattr(self, "_right_open", True)
        self._right_open = bool(open_)
        try:
            self.right_tabs.setVisible(self._right_open)
            # v0.30.17：固定页没了，没产出页时露出来的是 `right_empty` 空态 ——
            # 收起时必须连 `right_stack` 一起藏，否则 34px 窄条里还在夹着那几行字。
            if hasattr(self, "right_stack"):
                self.right_stack.setVisible(self._right_open)
            self.right_title.setVisible(self._right_open)
            self.right_hint.setVisible(self._right_open)
            self.show_clear.setVisible(self._right_open)
            self.right_fold.setVisible(self._right_open)
            self.right_show.setVisible(not self._right_open)
            # v0.30.10：宽度交给分隔条（可拖），这里只切换状态
            self._right_apply()
        except Exception:
            pass

    def _right_apply(self):
        """把「展开/收起 + 用户设的宽度」作用到分隔条上（v0.30.10）。

        ⚠️ 收起时必须把 min/max **都**设成 34：分隔条是按比例分配宽度的，
        只 `setSizes` 的话，窗口还没 show（离屏测试、开机瞬间）时会被按比例拉开，
        看着像"收起没生效"。
        """
        try:
            if not hasattr(self, "split") or not hasattr(self, "right"):
                return
            tot = self.split.width()
            if tot < 400:                       # 还没布局：用窗口宽估一个
                tot = max(760, self.width() - 198)
            if self._right_open:
                self.right.setMinimumWidth(self._RIGHT_MIN)
                self.right.setMaximumWidth(16777215)
                w = max(self._RIGHT_MIN, min(int(self._right_w), self._RIGHT_MAX))
                w = min(w, max(self._RIGHT_MIN, tot - self._MAIN_MIN))
                self.split.setSizes([max(self._MAIN_MIN, tot - w), w])
            else:
                self.right.setMinimumWidth(34)
                self.right.setMaximumWidth(34)
                self.split.setSizes([max(200, tot - 34), 34])
        except Exception:
            logging.exception("右侧栏宽度应用失败（不影响其它功能）")

    def _right_on_drag(self, pos=0, idx=0):
        """拖动分隔条：记住新宽度，并**合并高频事件**后写盘。"""
        try:
            sz = self.split.sizes()
            if len(sz) >= 2 and self._right_open and sz[1] > 34:
                self._right_w = int(sz[1])
                self._persist_right_w()
        except Exception:
            logging.debug("拖分隔条记录宽度失败", exc_info=True)

    def _persist_right_w(self):
        """拖动过程中每像素写一次 config.json 太浪费 → 停手 0.5s 再写。"""
        try:
            if getattr(self, "_rw_timer", None) is None:
                self._rw_timer = QTimer(self)
                self._rw_timer.setSingleShot(True)
                self._rw_timer.timeout.connect(self._save_right_w)
            self._rw_timer.start(500)
        except Exception:
            logging.debug("右侧栏宽度落盘定时器创建失败", exc_info=True)

    def _save_right_w(self):
        try:
            self.cfg["right_w"] = int(self._right_w)
            _save_json(CONFIG, self.cfg)
        except Exception:
            logging.exception("右侧栏宽度落盘失败")

    # ---------- v0.30.9 工作台：浏览器式产出标签 ----------
    def _mini_btn_css(self):
        return ("QPushButton{border:1px solid #cbd5e1;border-radius:6px;"
                "background:#f8fafc;color:#475569;font-size:11px;}"
                "QPushButton:hover{background:#e2e8f0;}")

    # ---------- v0.31.0 右侧「产出」：一个产物一页 + 每轮向下追加 ----------
    #: kind → 块头小胶囊里的中文工种名
    _KIND_CN = {"image": "图像", "ad": "广告", "video": "视频", "manga": "漫剧",
                "doc": "Word", "ppt": "PPT", "xls": "表格", "project": "开发",
                "keep": "留存", "chat": "对话"}

    def _panel_new(self, key, title="", kind="", page=""):
        """一个产出页的登记项。**页 = 一个产物**，页内每轮向下追加。"""
        return {"key": key, "kind": kind or "", "page": page or key,
                "title": title or "产出", "blocks": [], "rounds": 0,
                "html": "", "path": "", "media": "", "paths": [], "arts": [],
                "confirmed": set(), "note": "", "note_level": "info"}

    def _panel_open(self, key, title="", tip="", kind="", page=""):
        """打开（或复用）一个产出页。

        ★ v0.31.0 关键变化：往页里放东西一律走 `_panel_append`（**追加**）。
          这里只管"页"的建立与切换 —— 重复调用同一个 key 不会开新页
          （一个产物始终只有一页）。
        """
        if not hasattr(self, "right_tabs"):
            return {}
        d = self._out_tabs.get(key)
        if d is None:
            d = self._panel_new(key, title, kind, page)
            self._out_tabs[key] = d
            self.right_tabs.addTab(d, (title or "产出")[:14])
        elif title and not d.get("rounds"):
            # 只在还没内容时允许改名（有内容了再改，会让历史块的标题对不上）
            d["title"] = title
            i = self.right_tabs.indexOf(d)
            if i >= 0:
                self.right_tabs.setTabText(i, (title or "产出")[:14])
        i = self.right_tabs.indexOf(d)
        if i >= 0 and self.right_tabs.currentIndex() != i:
            self.right_tabs.setCurrentIndex(i)      # 触发 _panel_switch
        else:
            self._panel_switch()
        self._panel_hint()
        self._right_toggle(True)
        return d

    def _panel_switch(self, idx=None):
        """切页：**只换内容，不换视图**。

        ★ 全应用只有**一个**效果视图（实测每个 QWebEngineView = 一个 Chromium
          渲染进程）。所以"切页"就是把这一页累积的 HTML 重新交给那一个视图，
          而不是切换控件 —— 开多少页都只有一个浏览器进程。
        """
        try:
            self._panel_render(self.right_tabs.currentWidget()
                               if hasattr(self, "right_tabs") else None)
        except Exception:
            logging.exception("panel switch failed")

    def _panel_append(self, d, block_html, path="", media="", note=""):
        """往产出页**追加**一轮（绝不覆盖之前的内容）。"""
        if not d:
            return False
        try:
            d["blocks"].append(block_html or "")
            d["rounds"] = len(d["blocks"])
            if path:
                d["path"] = path
                d["arts"].append((d["rounds"], path))
                if path not in d["paths"]:
                    d["paths"].append(path)
            if media:
                d["media"] = media
            if note:
                d["note"] = note
            d["html"] = EV.wrap_document(d.get("title") or "产出",
                                         "".join(d["blocks"]))
            self._panel_render(d)
            return True
        except Exception:
            logging.exception("panel append failed")
            return False

    def _round_block(self, d, inner="", media="", path="", kind_label="",
                     note="", ask=None):
        """生成这一轮的块 HTML（轮次号 = 当前块数 + 1）。"""
        return EV.block_html(
            (d.get("rounds") or 0) + 1,
            ask=(getattr(self, "_cur_req", "") if ask is None else ask),
            kind_label=kind_label or self._kind_label((d or {}).get("kind")),
            inner=inner, media=media, path=path, note=note)

    @staticmethod
    def _panel_base_dir(d):
        """file:// 的基准目录。

        ⚠️ **必须给**：不给的话页面不是"本地内容"，`LocalContentCanAccessFileUrls`
        不生效，**连绝对 file:// 图片都加载不到**（实测 `img.naturalWidth == 0`）。
        """
        try:
            p = (d or {}).get("path") or ""
            if p and os.path.isdir(p):
                return p
            if p and os.path.isfile(p):
                return os.path.dirname(p)
            pg = (d or {}).get("page") or ""
            if pg and os.path.isdir(pg):
                return pg
            if pg and os.path.isfile(pg):
                return os.path.dirname(pg)
            return ""
        except Exception:                                        # noqa: BLE001
            return ""

    def _make_eff(self):
        """效果区渲染器：能用真浏览器就用；不能就**如实降级**并把原因写出来。"""
        if EV.available():
            w = EV.SharedEffectView(self.p_eff_holder)
            self.p_eff_lay.addWidget(w, 1)
            self._eff_mode = "webengine"
            return ("we", w)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        sc.setStyleSheet("QScrollArea{background:transparent;border:none;}")
        br = FitBrowser()
        sc.setWidget(br)
        self.p_eff_lay.addWidget(sc, 1)
        self._eff_mode = "native"
        self._eff_why = EV.error()
        return ("fit", br)

    def _panel_paint(self, html, base_dir=""):
        """把整份文档交给效果区（懒建渲染器：没人看效果时不建，不给启动加负担）。"""
        if getattr(self, "p_eff", None) is None:
            self.p_eff = self._make_eff()
        kind, w = self.p_eff
        if kind == "we":
            return w.set_doc(html, base_dir)
        try:
            w.setHtml(html)
            return True
        except Exception:
            logging.exception("paint native failed")
            return False

    def _panel_render(self, d=None):
        """把某一页的整份文档 + 信息行 + 操作区刷到界面（切页 / 追加都走这里）。"""
        if not hasattr(self, "right_pane"):
            return
        try:
            if d is None:
                d = self.right_tabs.currentWidget()
            if not d or self.right_tabs.currentWidget() is not d:
                return                  # 不是当前页就不渲染（切回来会再渲）
            tot = d.get("rounds") or 0
            _done = bool(d.get("path")) and (d.get("path") in (d.get("confirmed") or ()))
            self.p_info.setText("%s%s" % (d.get("title") or "产出",
                                          ("　第 %d 轮" % tot) if tot else ""))
            # 效果区
            html = d.get("html") or EV.wrap_document(
                d.get("title") or "产出", "<div class='empty'>还没有内容。</div>")
            ok = self._panel_paint(html, self._panel_base_dir(d))
            # 降级时把"现在不是真浏览器"如实写在界面上，不假装
            if getattr(self, "_eff_mode", "") == "native":
                self.p_info.setText(
                    "%s%s　⚠️ 原生渲染（真浏览器不可用：%s）"
                    % (d.get("title") or "产出",
                       ("　第 %d 轮" % tot) if tot else "",
                       (getattr(self, "_eff_why", "") or "未知")[:60]))
            elif not ok:
                self.p_info.setText("%s　⚠️ 渲染失败" % (d.get("title") or "产出"))
            # 操作区（针对**最新一轮**的产物）
            p = d.get("path") or ""
            self.p_reveal.setVisible(bool(p))
            self.p_open.setVisible(bool(p))
            self.p_del.setEnabled(bool(p))
            self.p_ok.setText("✅ 已留存" if _done else "✅ 确认留存")
            self.p_ok.setEnabled(bool(p) and not _done)
            col = {"info": "#64748b", "ok": "#16a34a",
                   "warn": "#b45309", "err": "#dc2626"}.get(
                d.get("note_level") or "info", "#64748b")
            self.p_note.setText(d.get("note") or "")
            self.p_note.setStyleSheet("QLabel{color:%s;font-size:11px;}" % col)
        except Exception:
            logging.exception("panel render failed")

    def _panel_hint(self):
        """刷新页数提示 + 空态切换；一页都没有时把内容区清干净。

        ⚠️ 清空时**只在渲染器已经建过**时才重绘 —— 否则启动阶段就会把
          WebEngine 视图建起来，白白吃掉启动时间。
        """
        try:
            n = self.right_tabs.count()
            self.right_hint.setText("· %d 个产出" % n if n else "")
            if hasattr(self, "right_stack"):
                self.right_stack.setCurrentIndex(1 if n else 0)
            if not n:
                self.p_info.setText("")
                self.p_note.setText("")
                self.p_ask.setVisible(False)
                if getattr(self, "p_eff", None) is not None:
                    self._panel_paint(EV.wrap_document(
                        "产出", "<div class='empty'>还没有产出。</div>"), "")
        except Exception:                                        # noqa: BLE001
            pass

    def _cur_panel_key(self) -> str:
        try:
            d = self.right_tabs.currentWidget()
        except Exception:                                        # noqa: BLE001
            return ""
        return (d or {}).get("key") or ""

    # ---- 底部操作区（作用于**当前页最新一轮**的产物）----
    def _panel_reveal_cur(self):
        self._panel_reveal(self._cur_panel_key())

    def _art_open_cur(self):
        self._art_open_key(self._cur_panel_key())

    def _panel_confirm_cur(self):
        self._panel_confirm(self._cur_panel_key())

    def _panel_delete_cur(self):
        self._panel_delete(self._cur_panel_key())

    def _panel_ask_toggle_cur(self):
        self._panel_ask_toggle(self._cur_panel_key())

    def _panel_ask_send_cur(self):
        self._panel_ask_send(self._cur_panel_key())

    # ---- 兼容层：老调用点（_present_in_panel / _present_artifact）走这两个 ----
    def _panel_set_text(self, key, title, html, tip=""):
        """把一段文案作为**新的一轮**追加进该页（v0.31.0 起语义 = 追加）。"""
        d = self._panel_open(key, title)
        if not d:
            return False
        self._panel_append(d, self._round_block(d, inner=html or ""))
        return True

    def _panel_set_art(self, key, path, title=""):
        """把一个产物作为**新的一轮**追加进该页（图片/视频/站点由渲染层判定）。"""
        d = self._panel_open(key, title or os.path.basename(path or "产出"))
        if not d:
            return False
        p = str(path or "")
        self._panel_append(d, self._round_block(d, media=p, path=p), path=p, media=p)
        return True

    def _panel_note(self, key, text, level="info"):
        """给某一页写一行状态（成功/失败都如实写，不粉饰）。"""
        d = self._out_tabs.get(key) or {}
        d["note"] = text or ""
        d["note_level"] = level or "info"
        try:
            if hasattr(self, "right_tabs") and self.right_tabs.currentWidget() is d:
                col = {"info": "#64748b", "ok": "#16a34a",
                       "warn": "#b45309", "err": "#dc2626"}.get(level, "#64748b")
                self.p_note.setText(text or "")
                self.p_note.setStyleSheet("QLabel{color:%s;font-size:11px;}" % col)
        except Exception:                                        # noqa: BLE001
            pass

    def _panel_reveal(self, key):
        """在资源管理器里**定位并选中**该产物。文件不在就如实说，不静默。"""
        try:
            d = self._out_tabs.get(key) or {}
            p = d.get("path") or ""
            if not p or not os.path.exists(p):
                self._panel_note(key, "⚠️ 产物已不在原位置：%s" % (p or "(还没有产物)"), "warn")
                return
            if os.path.isdir(p):
                PLATFORM_OPS.open_path(p)              # 跨平台：打开目录
                return
            # 定位到文件（Windows=explorer /select, · macOS=open -R · Linux=打开所在目录）
            PLATFORM_OPS.reveal_in_file_manager(p)
        except Exception as ex:                                  # noqa: BLE001
            logging.exception("reveal artifact failed")
            self._panel_note(key, "定位失败：%s" % ex, "err")

    def _panel_confirm(self, key):
        """「确认留存」= 标记已确认 + 写回工作台账（**不动文件**）。

        语义是小志 2026-09-17 定的：确认只是"这条我认了"，不搬文件 ——
        搬动会让路径变，聊天里那些已经贴出来的旧路径会全部失效。
        确认是**按产物路径**记的，所以同一页里不同轮次的产物可以各自确认。
        """
        d = self._out_tabs.get(key)
        if not d:
            return
        p = d.get("path") or ""
        if p and p in (d.get("confirmed") or set()):
            self._panel_note(key, "已经确认过了（重复点不会有副作用）", "info")
            return
        if not p:
            self._panel_note(key, "这一页还没有产物文件可确认（只有文案/效果）。", "warn")
            return
        if not os.path.exists(p):
            self._panel_note(key, "⚠️ 文件已不在：%s —— 没写进台账（不假装成功）" % p, "err")
            return
        if not WORKLOG:
            self._panel_note(key, "工作台账不可用，这次没记（功能没坏，只是没落账）。", "err")
            return
        try:
            tid = d.get("tid") or getattr(self, "_cur_wid", "") or ""
            if not (tid and WORKLOG.get(tid)):
                # 没挂到具体工作时也留一条 —— 否则"确认"就是个空动作
                t = WORKLOG.create("留存：" + (os.path.basename(p) or "产物"), "keep",
                                   getattr(self, "conv_id", ""))
                tid = t.get("id", "")
            d["tid"] = tid
            WORKLOG.add_artifact(tid, p)
            WORKLOG.update(tid, next_step="已确认留存：%s" % os.path.basename(p))
        except Exception as ex:                                  # noqa: BLE001
            logging.exception("panel confirm failed")
            self._panel_note(key, "写台账失败：%s" % ex, "err")
            return
        d.setdefault("confirmed", set()).add(p)
        self._panel_note(key, "✅ 已确认留存，并记进工作台账（文件没动，路径不变）", "ok")
        self._panel_render(d)
        try:
            if hasattr(self, "work_list"):
                self._work_refresh()
        except Exception:                                        # noqa: BLE001
            pass

    def _panel_delete(self, key):
        """「删除」= 移进**回收站**（可恢复），不是永久抹掉。

        ⚠️ 走 `sysops.delete_path`（自带系统禁区判据 + 操作台账），
        **绝不用 os.remove** —— 用户的东西删了要能捞回来。删前必须问一次。

        v0.31.0：删完**不关页** —— 页里是这件事的完整过程（小志要求"不删除和
        覆盖之前的内容"），关掉等于把过程也抹了。改成如实记一行"已移入回收站"。
        """
        d = self._out_tabs.get(key)
        if not d:
            return
        p = d.get("path") or ""
        if not p:
            self._panel_note(key, "这一页没有可删的文件（只有文案/效果）。", "warn")
            return
        if not os.path.exists(p):
            self._panel_note(key, "文件已经不在了，无需删除。", "info")
            return
        try:
            # 局部导入：本模块没有顶层 QtWidgets（静态守门抓到过这处未定义名字）
            from qt_compat import QtWidgets as _QW
            r = _QW.QMessageBox.question(
                self, "删除产物",
                "把最新一轮的产物移入回收站？\n\n%s\n\n（可以恢复，不是永久删除）" % p,
                _QW.QMessageBox.Yes | _QW.QMessageBox.No, _QW.QMessageBox.No)
            if r != _QW.QMessageBox.Yes:
                self._panel_note(key, "已取消，什么都没动。", "info")
                return
        except Exception:                                        # noqa: BLE001
            logging.exception("delete confirm dialog failed")
            return
        try:
            import sysops as SYS
            res = SYS.delete_path(p, allow_nonempty=True, why="产出页手动删除")
        except Exception as ex:                                  # noqa: BLE001
            logging.exception("delete artifact failed")
            self._panel_note(key, "删除失败：%s —— 文件还在原处" % ex, "err")
            return
        if not res.get("ok"):
            self._panel_note(key, "删除失败：%s —— 文件还在原处"
                             % (res.get("reason") or "未知原因"), "err")
            return
        self._panel_note(key, "🗑 已移入回收站（可恢复）：%s" % os.path.basename(p), "ok")

    def _panel_ask_toggle(self, key):
        """展开/收起讨论区。

        ⚠️ 刻意**不弹窗**：改稿要"边看效果边说"，弹窗会把右栏的效果挡住。
        """
        d = self._out_tabs.get(key)
        if not d:
            return
        try:
            vis = not self.p_ask.isVisible()
            self.p_ask.setVisible(vis)
            if vis:
                self.p_ask_edit.setFocus()
                self._panel_note(key, "说清楚要改什么，回车或点发送即可；"
                                      "新效果会**接着往下排**，上面的不会没。", "info")
        except Exception:                                        # noqa: BLE001
            logging.exception("ask toggle failed")

    def _panel_ask_send(self, key):
        """把讨论区里的话**当作下一轮请求发给聊天** —— 走同一条发送链路。

        ★ 为什么复用 `self.input` + `self.send()`：聊天那条链路已经处理了
        意图分类、附件、busy 排队、流式打字机、会话落盘……另起一条旁路等于
        把这些全漏掉。产物上下文走 `_effect_ctx`（气泡只显示用户原话，
        模型照样知道在改哪一张、第几轮）。
        """
        d = self._out_tabs.get(key)
        if not d:
            return
        txt = (self.p_ask_edit.text() or "").strip()
        if not txt:
            return
        p = d.get("path") or ""
        ttl = d.get("title") or "产出"
        self._effect_ctx = ("【正在讨论的产出】%s（第 %d 轮，共 %d 轮）\n文件：%s\n"
                            "（用户是在就这个产物提下一步要求）"
                            % (ttl, d.get("rounds") or 1, d.get("rounds") or 1, p or "（还没落成文件）"))
        try:
            self.p_ask_edit.clear()
            self.p_ask.setVisible(False)
            self.input.setPlainText(txt)
            self.send()
        except Exception as ex:                                  # noqa: BLE001
            logging.exception("panel ask send failed")
            self._panel_note(key, "发送失败：%s" % ex, "err")

    def _art_open_key(self, key):
        """用系统默认程序打开该产物。"""
        try:
            p = (self._out_tabs.get(key) or {}).get("path") or ""
            if p and os.path.exists(p):
                PLATFORM_OPS.startfile(p)              # noqa: S606  打开的是用户自己的产物
            else:
                self._panel_note(key, "产物不存在（可能已被移走）：%s"
                                 % (p or "(还没有产物)"), "warn")
        except Exception:                                        # noqa: BLE001
            logging.exception("open artifact failed")

    def _panel_close(self, idx: int):
        """关掉一个产出页（右栏没有固定页，**任何**标签都能关）。"""
        try:
            if idx < 0:
                return
            d = self.right_tabs.widget(idx)
            self.right_tabs.removeTab(idx)
            for k, v in list(self._out_tabs.items()):
                if v is d:
                    self._out_tabs.pop(k, None)
            self._panel_hint()
            self._panel_switch()
        except Exception:
            logging.exception("panel close failed")

    def _show_clear(self):
        """关掉全部产出页（不保留任何固定页）。"""
        try:
            while self.right_tabs.count() > 0:
                self.right_tabs.removeTab(0)
            self._out_tabs.clear()
            self._panel_hint()
        except Exception:                                        # noqa: BLE001
            pass

    def _looks_showable(self, html_text: str):
        """判断这段回复值不值得占一个产出页 → (bool, 理由)。

        判据刻意**保守**：只有"结构性内容"才算（表格 / 代码 / 步骤 / 多级标题 /
        长文）。否则用户每说一句闲聊，工作台都弹一次，比不弹还烦。
        """
        h = html_text or ""
        if "<table" in h:
            return True, "表格"
        if "<pre" in h:
            return True, "代码/预格式"
        if h.count("<h") >= 2:
            return True, "多级标题"
        if "<ol" in h and h.count("<li") >= 3:
            return True, "步骤清单"
        if "<ul" in h and h.count("<li") >= 5:
            return True, "要点清单"
        if len(re.sub(r"<[^>]+>", "", h)) >= 600:
            return True, "长文"
        return False, ""

    @staticmethod
    def _find_artifacts(txt: str):
        """从回复里挑出**真实存在**的产物路径（图/视频/文档）。不存在的不要。"""
        out = []
        pat = (r"[A-Za-z]:[\\/][^\s\"'<>|]+?\."
               r"(?:png|jpg|jpeg|webp|gif|bmp|mp4|mov|md|docx|xlsx|pptx|pdf)")
        for m in re.finditer(pat, txt or "", re.I):
            p = m.group(0).strip().rstrip(".,;，。；）)")
            if os.path.exists(p) and p not in out:
                out.append(p)
        return out

    @staticmethod
    def _panel_title_from(text: str) -> str:
        """从回复里取一个像样的页标题：优先一级标题，其次第一句有内容的话。"""
        for ln in (text or "").splitlines():
            t = ln.strip().lstrip("#").strip()
            if t and not t.startswith(("|", "-", "*", ">", "```")):
                return t[:12]
        return "方案"

    def _present_artifact(self, path):
        """把产物开成**自己的产出页**并切过去 —— 广告出图后右侧直接看到效果。"""
        p = str(path or "")
        if not p or not os.path.exists(p):
            return False
        try:
            if p not in self._art_paths:
                self._art_paths.append(p)
            return bool(self._panel_set_art("art:" + p, p))
        except Exception:
            logging.exception("present artifact failed")
            return False

    def _present_in_panel(self, reply_text: str, force: bool = False) -> bool:
        """把回复推进右侧的**产出页**（文案 + 产物）；够"有展示效果"时才自动展开。

        诚实边界：判据不满足就**什么也不做**（不开页、不展开）——
        宁可少展示，也不让工作台每轮乱跳。
        """
        if not hasattr(self, "right_tabs"):
            return False
        try:
            body = md_to_html(reply_text or "")
            ok, why = self._looks_showable(body)
            if not (ok or force):
                return False
            title = self._panel_title_from(reply_text or "")
            key = "doc:%d" % (abs(hash(body)) % (10 ** 12))
            self._panel_set_text(key, "📋 " + title, body, tip=why or "手动展示")
            arts = self._find_artifacts(reply_text or "")
            if arts:
                # 有产物就把产物页也开出来并停在它上面（用户想看的是效果）
                self._present_artifact(arts[-1])
            logging.info("工作台展示：%s" % (why or "强制"))
            return True
        except Exception:
            logging.exception("present in panel failed")
            return False


    def _current_work_task(self, tasks):
        """"当前这件事"对应的工作：优先显式选中，其次最近一个未完成。"""
        try:
            tid = getattr(self, "_wp_picked_tid", "")
            if tid:
                for t in tasks:
                    if t.get("id") == tid:
                        return t
            for t in tasks:
                if t.get("status") != "done" and (t.get("level") or 0) == 0:
                    return t
            return tasks[0] if tasks else None
        except Exception:
            return None


    def _wp_render_panel(self):
        """刷新「工作流」页的工作状态。

        v0.30.9：原来的「工作」页已删除（小志：「工作板块不需要了」
        「复盘是人工作的不需要显示」）—— 这里只做**只读汇总**：
        当前工作一行 + 进度；不再有目录筛选、工作列表、复盘区。
        """
        if not hasattr(self, "wp_cur"):
            return
        try:
            tasks = WORKLOG.recent(60)
        except Exception:
            return
        _cur = self._current_work_task(tasks)
        try:
            if not _cur:
                self.wp_cur.setText("当前工作：—（还没有进行中的工作）")
                # v0.30.13：以前这里直接 return，面板一片空白 —— 用户看到「工作流状态」
                # 却不知道它要怎么用。空态必须自己把用法讲清楚，不能让用户猜。
                if not getattr(self, "_wp_picked_tid", ""):
                    self.wp_next.setHtml(self._WP_EMPTY_HINT)
                return
            _s = (WORKLOG.progress_of(_cur.get("id"))
                  if WORKLOG.children_of(_cur.get("id")) else None)
            _txt = "当前工作：%s" % (_cur.get("title") or "")[:18]
            if _s:
                _txt += "（%d/%d 步 · %d%%）" % (_s["done"], _s["total"], _s["pct"])
            self.wp_cur.setText(_txt)
        except Exception:
            pass
        # 状态区：没手工操作过时，自动报一次最近的工作流进度（免得一开始空着）
        if getattr(self, "_wp_picked_tid", ""):
            return
        try:
            for t in tasks:
                if (t.get("level") or 0) == 0 and t.get("kind") == "workflow" \
                        and WORKLOG.children_of(t.get("id")):
                    s2 = WORKLOG.progress_of(t.get("id"))
                    self.wp_next.setPlainText(
                        "📊 工作流《%s》进度 %d%%（%d/%d 步）" % (
                            (t.get("title") or "")[:18], s2["pct"], s2["done"], s2["total"]))
                    break
        except Exception:
            pass



    def _wp_start(self):
        goal = (self.wp_goal.text() or "").strip()
        if not goal:
            return
        try:
            rid = WORKFLOW.plan_workflow(goal, dir="自动化工作")
            nxt = WORKFLOW.next_step(rid)
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>✅ 已建立工作流：<b>%s</b><br>"
                "首步：%s<br><b>正在自动执行第一步…</b></div>" % (
                    goal[:30], (nxt.get("title") or "")[:30]))
            self.wp_goal.clear()
            self._wp_picked_tid = ""
            self._wp_render_panel()
            # v0.30.13：**建完立刻开跑第一步**（走真执行器）。
            # 以前建完只显示「首步：xxx」就停住，用户以为它会自己跑 —— 于是面板
            # 永远停在 0%、产物永远空（真机原话："并没有自动化工作"）。
            # 自动跑第一步是可接受的：用户刚输入目标，本来就在等结果。
            QTimer.singleShot(0, self._wp_exec_next)
        except Exception as e:
            self.wp_next.setPlainText("⚠️ 建立工作流失败：%s" % e)

    def _wp_resume(self):
        """✅ 完成当前步并推进到下一步（对标白龙马 task-manager 的续跑）。

        说明：这里**不假装执行**——步骤的真实产出由用户/对话完成，
        这个按钮负责「判定这步做完 → 回写父工作流进度 → 报出下一步」，
        所以进度数字始终来自真实完成数（worklog.step_complete 汇总）。
        """
        target = None
        tid = getattr(self, "_wp_picked_tid", "") or ""
        if tid:
            t = WORKLOG.get(tid) or {}
            if (t.get("level") or 0) == 0:
                target = WORKFLOW.next_step(tid)      # 父任务 → 取它的下一步
            elif t.get("status") != "done":
                target = t
            else:
                target = WORKFLOW.next_step(t.get("parent_id") or "")
        if not target:
            # 没选中：取最近一个未完成的工作流，续它的下一步
            for t in WORKLOG.recent(40):
                if (t.get("level") or 0) == 0 and t.get("kind") == "workflow" \
                        and t.get("status") != "done":
                    s = WORKFLOW.next_step(t.get("id"))
                    if s:
                        target = s
                        break
        if not target:
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>没有可推进的步骤：<br>"
                "· 先在下方输入目标「▶ 启动工作流」；或<br>"
                "· 在上方列表点选一个工作，再点「✅ 完成此步」。</div>")
            return
        try:
            pid = (WORKLOG.get(target.get("id")) or {}).get("parent_id") or ""
            WORKLOG.step_complete(target.get("id"), "✅ 已完成（手动推进）")
            s = WORKFLOW.summarize(pid) if pid else {"pct": 100, "done": 1, "total": 1}
            nxt = WORKFLOW.next_step(pid) if pid else {}
            self._wp_picked_tid = pid or target.get("id")
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>✅ 已完成：<b>%s</b><br>"
                "整体进度：<b>%d%%</b>（%d/%d 步）<br>"
                "下一步：%s</div>" % (
                    (target.get("title") or "")[:30], s["pct"], s["done"], s["total"],
                    ("▶ " + (nxt.get("title") or "")[:30]) if nxt else "🎉 全部完成"))
        except Exception as e:
            self.wp_next.setPlainText("⚠️ 推进失败：%s" % e)
        self._wp_render_panel()


    # ---------- v0.30.5 自主 Tick 心跳 + 真执行 ----------
    def _wp_tick(self):
        """定时器入口：先自主巡检，再刷新面板。

        巡检出错**绝不能**影响面板刷新（失败即降级原则）。
        """
        try:
            st = AUTO.tick()
            hint = (st or {}).get("hint") or "待机"
            self.wp_tick_lbl.setText("后台：%s" % hint[:70])
        except Exception as e:
            logging.warning("autopilot tick 失败（已忽略，不影响刷新）: %s", e)
            try:
                self.wp_tick_lbl.setText("后台：巡检异常（已忽略）")
            except Exception:
                pass
        self._wp_render_panel()

    def _wp_toggle_auto(self):
        """开关「自主续跑」。默认关；开启后心跳才会把「下一步」写回台账。"""
        try:
            on = not AUTO.is_enabled()
            AUTO.enable(on)
            self.wp_auto.setText("🤖 自主续跑：%s" % ("开" if on else "关"))
            self.wp_tick_lbl.setText(
                "后台：已%s自主续跑%s" % ("开启" if on else "关闭",
                                          "（会更新「下一步」提示）" if on else "（只读巡检）"))
        except Exception as e:
            self.wp_next.setPlainText("⚠️ 切换自主续跑失败：%s" % e)

    def _wp_exec_next(self):
        """**真跑**工作流的下一步（走 workflow_engine 的执行器注册表）。

        诚实边界：没有执行器的工种只标记「进行中」并如实说明，
        **绝不把这一步标成完成** —— 否则进度条会绿着，而其实什么都没做。
        """
        root = ""
        tid = getattr(self, "_wp_picked_tid", "") or ""
        if tid:
            t = WORKLOG.get(tid) or {}
            root = tid if (t.get("level") or 0) == 0 else (t.get("parent_id") or "")
        if not root:
            _wfs = [t for t in WORKLOG.recent(40)
                    if (t.get("level") or 0) == 0
                    and t.get("kind") == "workflow"]
            for t in _wfs:
                if t.get("status") != "done":
                    root = t.get("id")
                    break
            # 全都完成了也要取最近一个：否则用户刚跑完一个工作流、
            # 再点这里会被回一句"没有可执行的工作流"（误导）。
            # 取到它，run_next 才能如实回 nothing_to_do → "已全部完成"。
            if not root and _wfs:
                root = _wfs[0].get("id")
        if not root:
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>没有可执行的工作流：<br>"
                "· 先在下方输入目标并「▶ 启动工作流」；或<br>"
                "· 在上方列表点选一个工作流。</div>")
            return
        try:
            ws = (self.cfg.get("ws_dir") or "").strip()
            # v0.30.6：把模型入口一起给执行器（否则广告这一步只会产出模板稿）
            # v0.30.11：不再传 out_dir=ws —— 那会把步骤产物直接摊在工作根目录里。
            # 交给 executors._out_dir 按分类落（<工作根>/doc、/project、/image…）。
            r = WORKFLOW.run_next(root, {"llm_fn": self._work_llm()})
        except Exception as e:
            logging.warning("run_next 失败: %s", e)
            self.wp_next.setPlainText("⚠️ 执行失败：%s" % e)
            self._wp_render_panel()
            return

        if r.get("reason") == "nothing_to_do":
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>🎉 这个工作流已经全部完成，"
                "没有待执行的步骤。</div>")
        elif r.get("reason") == "no_executor":
            st = r.get("step") or {}
            _title = (st.get("title") or "")[:30]
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>⚠️ 这一步还没有自动执行器，"
                "已标记为<b>进行中</b>（<b>没有</b>标记完成）。<br>"
                "步骤：%s<br>建议：在对话里直接说「%s」让我来做，"
                "或点「✅ 完成此步」手动推进。</div>" % (_title, _title))
        else:
            arts = r.get("artifacts") or []
            arts_txt = "<br>".join("· %s" % a for a in arts[:5]) \
                or "（本步无需产出文件）"
            self.wp_next.setHtml(
                "<div style='line-height:1.5'>%s<br>整体进度：<b>%s%%</b><br>"
                "产物：<br>%s</div>" % (
                    r.get("progress") or ("✅ 完成" if r.get("ok") else "⚠️ 未完成"),
                    r.get("pct", "?"), arts_txt))
        # v0.30.10（#10）：把产物流到「产物预览」区，用户就地看见生成的图/文档
        # v0.30.15：取**第一个真实存在的**产物（以前直接取 arts[0] —— 若第一步产出的
        #           是别的东西、或该条已被移动，预览区就空着，看着像"没有产物预览"）。
        try:
            _arts = [a for a in (r.get("artifacts") or []) if a]
            _first = next((a for a in _arts if os.path.exists(a)), "")
            if _first:
                self._wp_preview_artifact(_first)
            elif _arts:
                self._wp_preview_artifact(_arts[0])
            else:
                self.wp_preview.setPlainText("（本步未产出文件）")
        except Exception:
            logging.exception("产物预览失败")
        self._wp_picked_tid = root
        self._wp_render_panel()

    # ---------- v0.30.15：产物 / 已完成工作的可操作入口 ----------

    def _wp_open_path(self, path: str, folder: bool = False) -> bool:
        """打开产物（或它所在文件夹）。返回**是不是真的打开了**（不谎报）。"""
        try:
            if not path:
                return False
            if folder:
                d = path if os.path.isdir(path) else os.path.dirname(path)
                if os.path.isdir(d):
                    PLATFORM_OPS.startfile(d)
                    return True
                return False
            if os.path.exists(path):
                PLATFORM_OPS.startfile(path)
                return True
        except Exception:
            logging.exception("打开产物失败：%s", path)
        return False

    def _wp_panel_anchor(self, url):
        """工作流页 / 预览区里的锚点。

        `pasm://task/<tid>` 打开工作详情 · `pasm://art/<n>` 打开产物 ·
        `pasm://artdir/<n>` 打开产物所在文件夹。

        为什么要专门一个处理函数：这些链都是"真路径"的可点击代理 ——
        产物列表如果不给可点的入口，用户还是得自己去文件夹里翻（小志原话：
        "找不到刚完成的工作，我要如何去查找"）。
        """
        try:
            s = url.toString()
        except Exception:                                    # noqa: BLE001
            return
        if s.startswith("pasm://task/"):
            tid = s.rsplit("/", 1)[-1]
            if WORKLOG and WORKLOG.get(tid):
                try:
                    TaskDialog(self, tid).exec()
                    self._work_refresh()
                except Exception as ex:                      # noqa: BLE001
                    self._set_status("打开工作详情失败：%s" % ex)
            else:
                self._set_status("这条工作已不在台账里（可能被清理了）")
            return
        if s.startswith("pasm://artdir/") or s.startswith("pasm://art/"):
            _is_dir = s.startswith("pasm://artdir/")
            idx = s.rsplit("/", 1)[-1]
            p = (getattr(self, "_art_map", {}) or {}).get(idx, "")
            if self._wp_open_path(p, folder=_is_dir):
                self._set_status("已打开：%s" % (os.path.dirname(p) if _is_dir else p))
            else:
                self._set_status("打不开：产物或所在目录已不在（%s）" % (p or "空"))

    def _wp_show_done(self):
        """列出**最近完成的工作**及其真实产物 —— 回答"刚干完的活去哪找"。

        产物一律 `os.path.exists` 过滤过：只列真实存在的文件（消失的不列表，
        也不给可点的假链），点文件名就是 `os.startfile` 打开它。
        """
        try:
            tasks = WORKLOG.recent(80) if WORKLOG else []
        except Exception as e:                               # noqa: BLE001
            self.wp_next.setPlainText("读取工作台账失败：%s" % e)
            return
        _amap, _rows, _seq = {}, [], 0
        for t in tasks:
            if t.get("status") != "done":
                continue
            arts = [a for a in (t.get("artifacts") or []) if a and os.path.exists(a)]
            when = time.strftime("%m-%d %H:%M", time.localtime(t.get("updated") or 0))
            links = []
            for a in arts[:4]:
                _seq += 1
                k = str(_seq)
                _amap[k] = a
                links.append(
                    "<a href='pasm://art/%s' style='color:#0369a1;text-decoration:none'>"
                    "📄 %s</a>" % (k, html.escape(os.path.basename(a)[:32])))
            _art = ("　" + "　".join(links)) if links else \
                "　<span style='color:#94a3b8'>（无文件产物）</span>"
            _rows.append(
                "<div style='margin:5px 0'>✅ "
                "<a href='pasm://task/%s' style='color:#0c4a6e;text-decoration:none'>"
                "<b>%s</b></a>　<span style='color:#94a3b8'>%s · %s · %s</span><br>%s</div>"
                % (t.get("id"), html.escape((t.get("title") or "")[:34]),
                   WORKLOG.kind_label(t.get("kind")), when,
                   html.escape(str(t.get("progress") or "")), _art))
            if len(_rows) >= 12:
                break
        self._art_map = _amap
        if not _rows:
            self.wp_next.setHtml(
                "<div style='line-height:1.6'>还没有<b>已完成</b>的工作。<br><br>"
                "在上面输入目标 → 点 <b>▶ 启动工作流</b>（它会自动跑第一步）→ "
                "跑完点 <b>✅ 完成此步 → 下一步</b>。<br>"
                "<span style='color:#94a3b8'>每一步都是真执行（真出文件），"
                "完成后这里就能看到它们。</span></div>")
            return
        self.wp_next.setHtml(
            "<div style='line-height:1.5'><b>✅ 最近完成的工作</b>"
            "<span style='color:#94a3b8'>（点标题看详情，点文件名直接打开）</span><hr>"
            + "".join(_rows) + "</div>")

    def _art_key(self, path: str) -> str:
        """把产物路径登记进 `_art_map`，返回可写进 href 的键（锚点据此打开文件）。"""
        m = getattr(self, "_art_map", None)
        if not isinstance(m, dict):
            m = {}
            self._art_map = m
        for k, v in m.items():
            if v == path:
                return k
        k = "p%d" % (len(m) + 1)
        m[k] = path
        return k

    def _wp_preview_artifact(self, path):
        """把工作产物（图片/文本/方案）直接预览在「🤖 工作流」页的预览区（#10）。

        小志反馈：除「方案」外的工作产物都不在右侧栏展示。这里让图片/文档真能就地看见，
        不用去文件夹里翻。图片用 data URI 内联（与聊天内联图同一手法），文本/方案显示原文。

        v0.30.15 两处修：
          ① **文本/方案以前永远预览不出来** —— 这一段用的是 `_io.open(...)`，而本模块
             **从没导入过 `_io`** → 每次都 NameError → 被 except 吞成
             「⚠️ 预览失败：name '_io' is not defined」。图片能看、文档看不到，
             用户的原话就是"居然没有产物预览"。（已改为标准库 `io`。）
          ② 预览区顶部加「📂 打开文件 / 📁 所在文件夹」—— 预览只是"看一眼"，
             真要拿去用还得能打开；产物路径都在这里，不必让用户自己去找。
        """
        try:
            if not path or not os.path.exists(path):
                self.wp_preview.setPlainText("（没有可预览的产物）")
                return
            ext = os.path.splitext(path)[1].lower()
            _k = self._art_key(path)
            _head = (
                "<div style='margin-bottom:5px;line-height:1.6'>"
                "<a href='pasm://art/%s' style='color:#0369a1;text-decoration:none'>"
                "📂 打开文件</a>　"
                "<a href='pasm://artdir/%s' style='color:#0369a1;text-decoration:none'>"
                "📁 所在文件夹</a>"
                "<div style='color:#94a3b8;font-size:10px'>%s</div></div>"
                % (_k, _k, html.escape(path)))
            if ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif"):
                from PySide6.QtGui import QPixmap
                from PySide6.QtCore import QByteArray, QBuffer, QIODevice
                pm = QPixmap(path)
                if pm.isNull():
                    self.wp_preview.setHtml(
                        _head + "<div>（图片无法加载：%s）</div>"
                        % html.escape(os.path.basename(path)))
                    return
                maxw = 360
                if pm.width() > maxw:
                    pm = pm.scaledToWidth(maxw, Qt.SmoothTransformation)
                ba = QByteArray(); buf = QBuffer(ba); buf.open(QIODevice.WriteOnly)
                pm.save(buf, "PNG")
                b64 = ba.toBase64().data().decode("ascii")
                self.wp_preview.setHtml(
                    _head
                    + "<div style='text-align:center'>"
                    "<img src='data:image/png;base64,%s' width='%d' height='%d' "
                    "style='border-radius:8px'></div>"
                    "<div style='color:#94a3b8;font-size:10px;text-align:center'>%s</div>"
                    % (b64, pm.width(), pm.height(),
                       html.escape(os.path.basename(path))))
            else:
                try:
                    with io.open(path, encoding="utf-8", errors="replace") as f:
                        txt = f.read(8000)
                except Exception:
                    txt = ""
                if not txt.strip():
                    self.wp_preview.setHtml(
                        _head + "<div>（文件为空或不可读：%s）</div>"
                        % html.escape(os.path.basename(path)))
                    return
                self.wp_preview.setHtml(
                    _head + "<pre style='white-space:pre-wrap;word-wrap:break-word;"
                            "color:#334155;font-size:11px;margin:0'>%s</pre>"
                    % html.escape(txt))
        except Exception as e:
            try:
                self.wp_preview.setPlainText("⚠️ 预览失败：%s" % e)
            except Exception:
                pass

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 10))
    w = CompanionWindow()
    w.show()
    sys.exit(app.exec())
