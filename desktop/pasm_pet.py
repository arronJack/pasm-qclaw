"""PASM 桌面小人 v0.5 —— 常驻桌面的它（P3 角色化·独立版）。

- 无边框悬浮小人：可拖拽、偶尔自己散步（一小步一小步蹦）
- 单击：它开口说话 + 头顶冒气泡；双击：打开完整对话窗（记忆/偏好/成长都在里面）
- 托盘图标：显示/隐藏对话窗、退出
- 数据与 Companion 共用（%APPDATA%/PASMStudio），它和对话窗是同一个"它"

运行：python desktop/pasm_pet.py
"""
import json
import logging
import os
import random
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import qt_compat as qt
from qt_compat import (QApplication, QLabel, QMenu, QMessageBox,
                       QSystemTrayIcon, QWidget, Qt, QTimer, QEvent, QLockFile,
                       QAction, QColor, QFont, QIcon, QPainter)

from pet_avatar import PetAvatar
import pet_tuning as PT       # v0.30.9 观感调参（飞天高度等，与设置面板同一来源）
from pasm_companion import (CompanionWindow, DATA_DIR, _load_json, _save_json,
                              detect_local_llm, list_ollama_models,
                              pick_local_model, ARCH_META, _GENDER_LABELS)
import knowledge as K
import todo as T
import growth as G
import worklog as WORKLOG
from appinfo import APP_NAME, APP_VERSION
import tts as tts_mod
import llm_gateway as GW
from pet_behavior import PetBehavior


def _icon():
    base = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(base, "assets", "icon.ico"), os.path.join(base, "icon.ico")):
        if os.path.exists(p):
            return QIcon(p)
    return QIcon()


_quiet_global = False          # 全局"别打扰"开关：安静躲边时所有气泡自动静默


def _chat_local_model(models) -> str:
    """优先返回「聊天配置正在用的本地模型」，让后台学习与聊天共用一个模型。

    背景：后台学习原本走 pick_local_model()（选最小模型），而聊天走 config 里
    显式选的模型——两者常常不是同一个（如后台 Qwen3.5-4B、聊天 qwen2.5:7b）。
    Ollama 显存有限，会在两个模型间反复卸载/重载，每次数十秒，是"聊天几百秒
    没回应"的帮凶之一。这里让后台跟随聊天用同一个模型，消除换载抖动。
    """
    try:
        cfg = _load_json(os.path.join(DATA_DIR, "config.json"), {}) or {}
        mc = str(cfg.get("model_choice") or "")
        if mc.startswith("local:"):
            nm = mc[len("local:"):].strip()
            if nm and (not models or nm in models):
                return nm
    except Exception:
        pass
    return ""


class SpeechBubble(QLabel):
    """头顶气泡：显示一句话后自动淡出（quiet 模式下静默，除非 force=True）。"""
    def __init__(self):
        super().__init__()
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet(
            "background:rgba(15,23,42,215);color:#e0f2fe;"
            "border:1px solid rgba(56,189,248,180);border-radius:14px;"
            "padding:8px 12px;font-size:12px;"
            "font-family:'Microsoft YaHei';")
        self.hide()

    def say(self, text, parent_pos, parent_size, force=False):
        if _quiet_global and not force:
            return
        self.setText(text)
        self.adjustSize()
        self.setMaximumWidth(260)
        self.setMaximumHeight(90)
        self.adjustSize()
        x = parent_pos.x() + (parent_size.width() - self.width()) // 2
        y = parent_pos.y() - self.height() - 8
        self.move(max(4, x), max(4, y))
        self.show()
        self.raise_()
        QTimer.singleShot(3800, self.hide)


class PetShell(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(132, 174)   # v0.28.3：avatar 扩高(1.40×，顶部工作气泡) 168+6
        self._drag = None
        self._moved = False
        self._steps = 0
        self._dir = 1
        self._chat = None            # 懒加载完整对话窗
        self._pending_bubble = None

        self.avatar = PetAvatar(120, self)
        self.avatar.move(6, 0)
        self.avatar.on_click = self._avatar_clicked
        self.avatar.on_double_click = self.open_chat      # 双击头像 → 打开对话窗
        self.avatar.on_drag = self._drag_by
        self.bubble = SpeechBubble()
        self._place_default()

        # 散步：偶尔蹦一小段
        self._stroll_timer = QTimer(self)
        self._stroll_timer.timeout.connect(self._maybe_stroll)
        self._stroll_timer.start(14000)

        # 内心更新（情绪→表情）
        self._heart = QTimer(self)
        self._heart.timeout.connect(self._heartbeat)
        self._heart.start(1500)
        self._sleep_check = QTimer(self)
        self._sleep_check.timeout.connect(self._sleep_tick)
        self._sleep_check.start(3000)
        self._act_check = QTimer(self)
        self._act_check.timeout.connect(self._maybe_activity)
        self._act_check.start(9000)
        self._last_mood = None
        self._val = 0.0                 # 最近一次情绪快照（valence/arousal），说话语气用
        self._aro = 0.0
        self._heartbeat()

        self._tray = None
        self._make_tray()
        self._sleeping = False
        self._last_act = time.time()
        self.pet = _load_json(os.path.join(DATA_DIR, "pet_state.json"),
                              {"exp": 0, "skin": "tech", "reads": [],
                               "sent": {}, "birth": time.strftime("%Y-%m-%d"),
                               "auto_work": True, "auto_web": True,
                               "gender": "none"})
        self.auto_work = bool(self.pet.get("auto_work", True))
        # 自主学默认开（除非用户显式关过 auto_web_user=True）
        self.auto_web = bool(self.pet.get("auto_web", True) or
                             "auto_web_user" not in self.pet)
        self._last_review = time.time()      # 周期制：启动后过整周期才触发
        self._last_web = time.time()
        self._web_idx = int(self.pet.get("web_idx", 0))
        self.exp = int(self.pet.get("exp", 0))
        self.growth = self._level_of(self.exp)
        self.avatar.set_skin(self.pet.get("skin", "tech"))
        self.avatar.set_growth(self.growth)
        self._cfg = _load_json(os.path.join(DATA_DIR, "config.json"), {})
        self._persona = self._cfg.get("persona", "温和沉稳")
        self._arch = ARCH_META.get(self._persona, {})
        self._name = self._cfg.get("name", "小U")
        self._gender = self.pet.get("gender", "none")
        self.avatar.set_gender(self._gender)
        # 行为「自我设计 + 自我优化」引擎：按性格×阶段自设计动作池，
        # 并按用户反馈(夸/戳/训)自我优化权重，持久化到 pet_behavior.json
        self.behavior = PetBehavior(DATA_DIR)
        self.behavior.design(self._persona, self.growth, self._arch)
        self._web_learning = False       # 上网自学进行中标记（用于工作状态显示）
        # 别打扰/躲边状态（P3）：quiet=安静躲边、home 记忆原位、peek 周期探头
        self._quiet_mode = False
        self._home_pos = None
        self._last_ev_ts = 0.0
        self._anim_busy = False
        self._fly_on = False          # v0.30.9 正在飞天（绕屏一圈）
        self._reading_until = 0.0
        self._book = "《零基础网络安全手册》"
        # v0.17.3 性格化自发小动作：调皮踢球/外向跳舞/文静坐下看书（可被看见的频率）
        self._hobby_until = 0.0
        self._hobby_at = time.time() + random.uniform(6, 18)
        self._alive = 0.0
        self._life = QTimer(self)
        self._life.timeout.connect(self._life_tick)
        self._life.start(5000)
        self._remind = QTimer(self)
        self._remind.timeout.connect(self._remind_tick)
        self._remind.start(20000)
        self._peek_timer = QTimer(self)      # 安静躲边时周期探头
        self._peek_timer.timeout.connect(self._peek_tick)
        self._peek_timer.start(12000)

    # ---------- 布局/行为 ----------
    def _place_default(self):
        scr = QApplication.primaryScreen().availableGeometry()
        self.move(scr.right() - self.width() - 90, scr.bottom() - self.height() - 90)

    STAGES = G.STAGES
    THRESH = G.THRESH
    SCHEDULE = [
        {"time": "12:00", "text": "到中午啦，该吃饭了，别让胃等你太久哦"},
        {"time": "18:00", "text": "下班时间到~ 今天辛苦了，起来走走活动一下吧"},
    ]

    @staticmethod
    def _level_of(exp):
        return G.level_of(exp)

    def _save_pet(self):
        self.pet.update(exp=self.exp, skin=self.avatar.skin,
                        sent=self.pet.get("sent", {}))
        _save_json(os.path.join(DATA_DIR, "pet_state.json"), self.pet)

    def _note_act(self, text: str):
        """记录一条自动化/自主行为，右键菜单可看最近动态。"""
        acts = self.pet.setdefault("acts", [])
        acts.append({"t": time.strftime("%H:%M"), "what": text[:40]})
        self.pet["acts"] = acts[-10:]
        self._save_pet()

    def _add_exp(self, v):
        before = self.growth
        self.exp += int(v)
        self.pet["exp"] = self.exp
        self.growth = self._level_of(self.exp)
        self.avatar.set_growth(self.growth)
        self._save_pet()
        if self.growth > before:
            self.bubble.say("哇，我好像又长大了一点！现在" + self.STAGES[self.growth] +
                            "啦～", self.pos(), self.size())
            # 升级 → 行为自我设计重新生成（解锁更丰富动作，保留用户反馈优化）
            before_pool = set(self.behavior.unlocked)
            self.behavior.design(self._persona, self.growth, self._arch)
            new_acts = set(self.behavior.unlocked) - before_pool
            if new_acts:
                self.bubble.say("我好像解锁了新动作：" +
                                "、".join(new_acts) + "，以后会自己练着玩～",
                                self.pos(), self.size())
            self._sync_chat_growth()

    def _sync_chat_growth(self):
        """成长变化 → 聊天窗头像/阶段显示实时同步（同一个'它'）。"""
        try:
            if self._chat is not None and hasattr(self._chat, "_on_growth_changed"):
                self._ui(self._chat._on_growth_changed)
        except Exception:
            pass

    AUTO_TOPICS = ["网络安全", "机器学习", "Python 编程", "时间管理", "营养健康",
                   "人工智能", "数据备份", "高效沟通", "金融理财", "心理学入门",
                   "量子计算", "区块链", "数据可视化", "网页开发基础", "数据库原理",
                   "摄影构图", "音乐制作入门", "急救常识", "天文科普", "经济学常识",
                   "写作技巧", "逻辑思维", "情绪调节", "睡眠科学", "运动健身",
                   "绿色能源", "机器人技术", "设计思维", "历史故事", "传统文化",
                   "编程调试技巧", "办公软件技巧", "游戏开发入门", "生物科普", "物理常识"]

    def _sync_from_cfg(self):
        """改名/性格/性别在设置里改完后实时同步到小人的言行与声线（不再等重启）。"""
        try:
            cfg = _load_json(os.path.join(DATA_DIR, "config.json"), {})
            nm = cfg.get("name") or "小U"
            if nm != getattr(self, "_name", None):
                self._name = nm
                if not self._quiet_mode:
                    self.bubble.say(f"记住啦，以后叫我{nm}～",
                                    self.pos(), self.size())
            p = cfg.get("persona") or "温和沉稳"
            if p != self._persona:
                self._persona = p
                self._arch = ARCH_META.get(p, {})
                # 换性格 → 行为自我设计按新性格重新生成（保留用户反馈优化）
                self.behavior.design(self._persona, self.growth, self._arch)
            st = _load_json(os.path.join(DATA_DIR, "pet_state.json"), {})
            g = st.get("gender", "none")
            if g != self._gender:
                self._gender = g
                self.avatar.set_gender(g)
        except Exception:
            pass

    def _life_tick(self):
        self._sync_from_cfg()
        self._auto_work_tick()            # 认知学习不受打盹影响（复习/日记/上网）
        if self._sleeping:
            return
        self._alive += 5.0
        if self._alive >= 300.0:          # 每 5 分钟 +1 经验
            self._alive = 0.0
            self._add_exp(1)
        # 阅读结束
        if self._reading_until and time.time() > self._reading_until:
            self._reading_until = 0.0
            self._pet_mode("idle")
            book_file = getattr(self, "_book_file", "网络安全入门.md")
            title = book_file.replace(".md", "")
            def learn():
                content = K.read_book(book_file)
                # ① 先用规则提炼秒级入库，保证“读过必记住”且反馈即时
                fast = K.rule_bullets(content, 5)
                if not fast:
                    fast = ["（这本书有点深，我提炼不出要点…下次再读一遍）"]
                # v0.30.8：用干净标题入库（原来传的是文件名，资料库里会显示
                # "网络安全入门.md"）—— 资料库是笔记库，不是文件名列表。
                n = K.record(title, fast, text=content, src="读书")
                logging.info("learned %s fast=%d total=%d", title, len(fast), n)
                self._ui(lambda: self._learn_done(title, len(fast)))
                try:
                    # v0.30.8：读完立刻落成 Obsidian 笔记（幂等，重复读不会重复写）
                    K.export_one(title)
                except Exception:
                    pass
                # ② 后台若有本地 LLM，再做一次精炼增强（要点更准、置信度更高）
                #    模型冷加载慢 → 不强求，规则版已入库，用户无需等待
                try:
                    loc = detect_local_llm()
                    if loc:
                        models = list_ollama_models()
                        # 紧跟聊天用同一个本地模型，避免后台学习触发 Ollama 换载
                        pick = (self.pet.get("learn_model") or _chat_local_model(models)
                                or pick_local_model(models) or "")
                        if pick not in models and models:
                            pick = pick_local_model(models)
                        if pick:
                            better = K.summarize(book_file, content,
                                                 llm=(loc["base_url"], pick),
                                                 log=lambda m: logging.info(m))
                            if better and len(better) >= 2:
                                K.record(title, better, text=content, src="读书")
                                logging.info("learned %s refined=%d", title, len(better))
                except Exception as ex:
                    logging.warning("refine skip %s: %s", title, ex)
            threading.Thread(target=learn, daemon=True).start()
            self._add_exp(20)
            self.pet.setdefault("reads", []).append(
                {"t": time.strftime("%m-%d %H:%M"), "book": self._book})
            self._save_pet()

    def _learn_done(self, title, count):
        self.bubble.say(f"读完《{title}》啦，我提炼出 {count} 条要点记住了~",
                        self.pos(), self.size())
        self._note_act("读完《" + title + "》记住 " + str(count) + " 条")

    def _journal_today(self):
        today = time.strftime("%Y-%m-%d")
        reads_today = [r for r in self.pet.get("reads", [])
                       if r.get("t", "").startswith(time.strftime("%m-%d"))]
        n_know = len(K.learned_titles())
        entry = {"date": today, "reads": len(reads_today),
                 "books": [r["book"] for r in reads_today][-5:],
                 "exp": self.exp, "knowledge": n_know}
        jf = os.path.join(DATA_DIR, "journal.json")
        journal = []
        try:
            import json
            if os.path.exists(jf):
                journal = json.load(open(jf, "r", encoding="utf-8"))
        except Exception:
            journal = []
        if not any(j.get("date") == today for j in journal):
            journal.append(entry)
            journal = journal[-90:]
            try:
                json.dump(journal, open(jf, "w", encoding="utf-8"),
                          ensure_ascii=False, indent=1)
            except Exception:
                pass
            self._add_exp(6)

    def _auto_work_tick(self):
        now = time.time()
        if not self.auto_work:
            return
        # 成长日记：当天 23 点后若还没写则写（含错过补写）
        if int(time.strftime("%H")) >= 23 and \
                self.pet.get("journal_last") != time.strftime("%Y-%m-%d"):
            self.pet["journal_last"] = time.strftime("%Y-%m-%d")
            self._save_pet()
            self._journal_today()
            self._note_act("写今日成长日记（知识库+经验复盘）")
        # 每 10 分钟：复习巩固一本旧书（提升置信度）；没旧书就读新书 → 学习永不空转
        if now - self._last_review > 600:
            self._last_review = now
            titles = K.learned_titles()
            f = titles[hash(str(int(now // 600))) % len(titles)] if titles else ""
            book = f + ".md" if f and not f.endswith(".md") else f
            if book and os.path.exists(os.path.join(K.BOOKS_DIR, book)):
                content = K.read_book(book)
                bl = K.rule_bullets(content, 4)
                if bl:
                    # 复习时把全文一并写回：早期"只存要点"的条目借此补上原文
                    K.record(book, bl, text=content, src="复习巩固")
                    self._add_exp(1)
                    self._note_act("复习巩固《" + f.replace(".md", "") + "》")
            elif not self._reading_until and not self._sleeping:
                # 知识库还是空的 → 主动读一本（先快速入一本再说）
                books = K.list_books()
                learned = set(K.learned_titles())
                fresh = [b for b in books if K.stem(b) not in learned]
                if fresh and not self._steps:
                    self._book_file = fresh[hash(str(int(now // 600))) % len(fresh)]
                    self._book = "《" + self._book_file.replace(".md", "") + "》"
                    self._reading_until = time.time() + random.randint(20, 30)
                    self._pet_mode("read")
                    self._note_act("自动开读《" + self._book_file[:-3] + "》")
                    logging.info("auto pick first book %s", self._book_file)
        # 自动上网自学：默认开；间隔随知识量智能拉长（知识少勤学、多了从容）
        if self.auto_web and now - self._last_web > self._auto_web_interval():
            self._last_web = now
            topic = self._next_web_topic()
            self._save_pet()
            self._web_learn(topic)      # 学没学成，由 _web_learn 结束后如实记录动态
        # 自我调整：学得越多 → 阅读频率略降、散步略升（避免重复刷书）
        rr, _s = self._temper_weights()
        self.read_p = max(0.08, min(rr, 0.5 - len(K.learned_titles()) * 0.008))

    def _temper_weights(self):
        """按性格档案给"自主活动"加权：内向爱看书、外向爱蹦跶、调皮爱玩。"""
        arch = self._arch or {}
        temper = arch.get("temper", "steady")
        energy = float(arch.get("energy", 0.35))
        r_read = {"intro": 0.5, "steady": 0.42, "extra": 0.18,
                  "playful": 0.15}.get(temper, 0.3)
        stroll_up = max(0.5, min(0.85, 0.45 + energy * 0.4))
        return r_read, stroll_up

    def _auto_web_interval(self) -> float:
        """自学间隔（秒）：知识越少越想学（约 30 分钟），越多越从容（最长 ~90 分钟）。"""
        n = len(K.learned_titles())
        if n <= 2:
            return 900.0
        return min(5400.0, 1800.0 + n * 90.0)

    def _curious_topic(self) -> str:
        """好奇心选题器：新奇优先 + 避开刚学/刚试过 + 偶尔从已学主题"延伸"新角度。"""
        know = {t.lower() for t in K.learned_titles()}
        recent = list(self.pet.get("web_log", [])[-16:])
        cand = [t for t in self.AUTO_TOPICS
                if t.lower() not in know and t not in recent]
        if not cand:
            cand = [t for t in self.AUTO_TOPICS if t not in recent]
        if not cand:
            cand = list(self.AUTO_TOPICS)
        if random.random() < 0.3 and know:
            base = random.choice(sorted(know))
            ext = random.choice([" 的进阶", " 的实战应用", " 常见误区",
                                 " 的原理", " 与生活的结合"])
            topic = (base + ext).strip()
            if topic.lower() not in know and topic not in recent:
                cand = [topic] + cand
        t = cand[random.randrange(len(cand))]
        wl = self.pet.setdefault("web_log", [])
        wl.append(t)
        self.pet["web_log"] = wl[-30:]
        return t

    def _next_web_topic(self):
        self.pet["web_idx"] = self.pet.get("web_idx", 0) + 1
        return self._curious_topic()

    def _llm_endpoint(self):
        """返回可用模型端点 (base_url, model, api_key)；云端优先，其次本地 Ollama。"""
        try:
            if self._cfg.get("api_key"):
                return (self._cfg.get("base_url") or "https://api.deepseek.com/v1",
                        self._cfg.get("model") or "deepseek-chat",
                        self._cfg.get("api_key"))
        except Exception:
            pass
        try:
            loc = detect_local_llm()
            if loc:
                models = list_ollama_models()
                # 与聊天用同一个本地模型，避免后台学习触发 Ollama 换载
                pick = _chat_local_model(models) or pick_local_model(models) or ""
                if pick:
                    return (loc["base_url"], pick, "")
        except Exception:
            pass
        return None

    def _web_learn(self, topic):
        import agent_tools as AT

        def job():
            self._web_learning = True
            try:
                text = AT.fetch_topic_text(topic, max_chars=16000)
                if not text:
                    logging.info("auto web fail %s", topic)
                    self._note_act("上网学《" + topic + "》：没连上信息源")
                    self._ui(lambda: self.bubble.say(
                        "想上网学《" + topic + "》但几个信息源都没连上…等会儿我再换个主题试试",
                        self.pos(), self.size()))
                    return
                bullets = []
                ep = self._llm_endpoint()
                if ep:
                    try:
                        bullets = K.summarize(topic + ".md", text,
                                              llm=(ep[0], ep[1]),
                                              log=lambda m: logging.info(m))
                    except Exception as ex:
                        logging.warning("auto web summarize err %s: %s", topic, ex)
                if not bullets:
                    bullets = K.sentence_bullets(text, 6) or K.rule_bullets(text, 5)
                # 学到 = 原文入库；要点只是学完后的速记索引，绝不因要点没提炼好丢内容
                if not bullets:
                    bullets = ["（原文已存入知识库，要点待后续提炼）"]
                K.record(topic, bullets[:6], text=text, src="自动上网自学")
                try:
                    bpath = os.path.join(K.BOOKS_DIR, topic + ".md")
                    with open(bpath, "w", encoding="utf-8") as f:
                        f.write("# " + topic + "\n\n" + text[:30000])
                except Exception:
                    pass
                self._note_act("自动上网学了《" + topic + "》：原文已入库")
                st = self.pet.setdefault("stats", {})
                st["learn"] = st.get("learn", 0) + 1
                # 学后自测：说不清核心就老实记"待弄懂"，不许假装学会
                ok = self._self_test(topic, bullets, ep)
                if ok is True:
                    st["pass"] = st.get("pass", 0) + 1
                    say = ("我又自己上网学了《" + topic + "》，自测能说出核心概念了，"
                           "已存入知识库（含原文全文，资料库双击可读）。"
                           "以后聊到这方面我会更专业哦~")
                elif ok is None:
                    say = ("我自己上网学了《" + topic + "》，已存入知识库（含原文全文）。"
                           "暂时没有模型做自测，等模型在线我会补考。")
                else:
                    st["fail"] = st.get("fail", 0) + 1
                    self._pet_unknown(topic, "自测没过：还说不利索")
                    say = ("《" + topic + "》的原文我存进知识库了，但自测还说不利索——诚实说，"
                           "这还不算完全学会。我已记进待弄懂清单，过阵子复习再学。")
                self._save_pet()

                def ui():
                    self.bubble.say(say, self.pos(), self.size())
                    self._add_exp(10)
                self._ui(ui)
            except Exception as ex:
                logging.warning("auto web err: %s", ex)
            finally:
                self._web_learning = False
        threading.Thread(target=job, daemon=True).start()

    def _pet_unknown(self, topic, why: str):
        """记入"待弄懂清单"：学不会就说学不会，不假装。"""
        un = []
        up = os.path.join(DATA_DIR, "unknown_topics.json")
        try:
            if os.path.exists(up):
                un = json.load(open(up, "r", encoding="utf-8"))
        except Exception:
            pass
        un = [u for u in un if u.get("topic") != topic]
        un.append({"topic": topic, "why": why,
                   "date": time.strftime("%m-%d %H:%M")})
        try:
            json.dump(un, open(up, "w", encoding="utf-8"),
                      ensure_ascii=False, indent=1)
        except Exception:
            pass

    def _self_test(self, topic, bullets, ep):
        """学后自测：让模型"只凭已存要点"用自己的话讲清主题；讲不出 = 没学会。"""
        if not ep:
            return None
        try:
            ans = GW.gw.complete(
                ep[0], ep[1], ep[2] or "local",
                [
                    {"role": "system",
                     "content": f"你是一位刚自学过《{topic}》的 AI 学习者。"
                                f"下面是你自己整理进知识库的要点（相当于你的记忆）：\n"
                                + "\n".join(f"- {b}" for b in bullets[:5])},
                    {"role": "user",
                     "content": "现在不看资料，用自己的话回答 3 问（每问一两句，中文）：\n"
                                f"1) 《{topic}》到底是什么？\n"
                                "2) 它最关键的 2 个知识点或做法是什么？\n"
                                "3) 学了它，你能拿来做什么？\n"
                                "如果其实说不出来，就如实写：我还不太懂。不要编。"}],
                task="selftest", temperature=0.5, max_tokens=400)
            ans = (ans or "").strip()
            if not ans:
                return False
            bad = ("不太懂", "不知道", "想不起来", "没学会", "不清楚", "无法回答")
            if any(w in ans for w in bad) and len(ans) < 120:
                return False
            if sum(len(x) for x in ans.splitlines() if len(x) > 6) < 40:
                return False
            return True
        except Exception as ex:
            logging.warning("self test err: %s", ex)
            return None

    def _toggle_auto_work(self, *_):
        self.auto_work = not self.auto_work
        self.pet["auto_work"] = self.auto_work
        self._save_pet()
        self.bubble.say("自动化工作已" + ("开启" if self.auto_work else "关闭"),
                        self.pos(), self.size())

    def _toggle_auto_web(self, *_):
        self.auto_web = not self.auto_web
        self.pet["auto_web"] = self.auto_web
        self.pet["auto_web_user"] = True          # 显式设置过 → 尊重用户选择
        self._save_pet()
        if self.auto_web:
            self._last_web = time.time() - 60     # 60 秒后先学第一个做演示
        self.bubble.say("自动上网自学已" + ("开启" if self.auto_web else "关闭") +
                        "（开启后它会自己好奇选题，学完做自测，学不会会老实记下）",
                        self.pos(), self.size())

    def _remind_tick(self):
        now = time.strftime("%H:%M")
        today = time.strftime("%Y-%m-%d")
        sent = self.pet.setdefault("sent", {})
        for item in self.SCHEDULE:
            key = item["time"] + "|" + today
            if item["time"] == now and sent.get(key) != "1":
                sent[key] = "1"
                self._save_pet()
                text = item["text"]
                if self._sleeping:
                    self._touch()
                if not self._quiet_mode:          # 别打扰期间整点提醒静默（不出声不冒泡）
                    self.bubble.say("⏰ " + text, self.pos(), self.size())
                    tts_mod.speak_text(text, growth=self.growth,
                                       gender=self._gender,
                                       persona=self._persona,
                                       log=lambda m: logging.info(m))
                try:
                    if self._chat is not None and self._chat.isVisible():
                        self._chat._append("系统", "⏰ " + text)
                except Exception:
                    pass
        for item in T.due_now():
            T.done(item.get("text", "")[:12])
            msg = "待办时间到：" + item.get("text", "")
            if not self._quiet_mode:
                self.bubble.say("⏰ " + msg, self.pos(), self.size())
                tts_mod.speak_text(msg, growth=self.growth,
                                   gender=self._gender,
                                   persona=self._persona,
                                   log=lambda m: logging.info(m))
            try:
                if self._chat is not None and self._chat.isVisible():
                    self._chat._append("系统", "⏰ " + msg)
            except Exception:
                pass

    def _maybe_activity(self, force=False):
        """自主活动决策：阅读 / 散步 / 玩耍(性格加权) / 休息。"""
        if self._quiet_mode and not force:
            return                              # 安静躲边时只在原地待着
        if self._busy_external() and not force:
            return                              # 正陪你干活 → 不抢活，让位给工作态
        self._hobby_tidy()                      # 自发小动作收尾（读书/玩耍回原位）
        if self._sleeping or self._reading_until or self._steps > 0:
            if not force:
                return
            if self._reading_until:
                return
        rr, stroll_up = self._temper_weights()
        self.read_p = max(0.08, min(rr, 0.5 - len(K.learned_titles()) * 0.008))
        r = 0.0 if force else random.random()
        if r < (0.5 if force else self.read_p):
            # 只挑「还没提炼入库」的新书 → 学过的书不重复当新书读
            learned = set(K.learned_titles())
            # 与 learned_titles() 同样按「去扩展名标题」比对（书库 v0.27.8 起为 .md）：
            # 旧写法 b[:-4] 按 .txt 长度截断 → 与入库标题永远不匹配 → 每本书被当成
            # 「新书」反复重读，每轮发 2 次本地 LLM 调用，把本地模型刷到聊天永远排队。
            fresh = [b for b in K.list_books() if K.stem(b) not in learned]
            if not fresh:
                if force:
                    self.bubble.say("我把书架上的书都读完啦～ 想学新知识，可以让我 "
                                    "「上网学一下 新主题」，或把新书放进知识库书单。",
                                    self.pos(), self.size())
                # 学习不断粮：书读完了 → 自己上网学一个新主题（间隔到点才触发）
                if self.auto_web and not self._reading_until and \
                        time.time() - self._last_web > self._auto_web_interval():
                    self._last_web = time.time()
                    topic = self._next_web_topic()
                    self._save_pet()
                    self._web_learn(topic)
                return
            self._book_file = fresh[random.randint(0, len(fresh) - 1)]
            self._book = "《" + self._book_file.replace(".md", "") + "》"
            rr, _s = self._temper_weights()
            self.read_p = max(0.08, min(rr, 0.5 - len(K.learned_titles()) * 0.008))
            dur = random.randint(45, 60) if force else random.randint(15, 25)
            self._reading_until = time.time() + dur
            self._pet_mode("read")
            if force:
                self.bubble.say("好呀，我去读会儿" + self._book + "，读完告诉你学到了什么~",
                                self.pos(), self.size())
        elif r < stroll_up:
            self._maybe_stroll()
        else:
            # v0.17.3：按性格的随机自发小动作（调皮踢球/外向跳舞/文静坐下看书…）
            if not force and not self._quiet_mode:
                self._maybe_hobby()
        # 其余：原地休息（本来就在做）

    def _hobby_tidy(self):
        """自发小动作收尾：到点把"看书/玩耍"姿势复位（真实读书不打断）。"""
        if self._hobby_until and time.time() >= self._hobby_until:
            self._hobby_until = 0.0
            if self.avatar.mode == "read" and not self._reading_until \
                    and not self._sleeping and not self._quiet_mode:
                self._pet_mode("idle")

    def _maybe_hobby(self):
        """性格化自发小动作：让小人"活"起来、能被看见。

        动作**不再写死**——由 PetBehavior 引擎按「性格 × 成长阶段」自我设计动作池，
        并按用户反馈(夸奖/被戳/训斥)自我优化权重后抽样决定（pick）。
        频率随性格能量高低自动调节，偶尔配一句小气泡。
        与真实"读书学习"分开：这只是原地自娱，不占知识库、不影响自动化。
        """
        now = time.time()
        if self._quiet_mode or self._sleeping or self._anim_busy:
            return
        if self._busy_external():        # 正在陪你干活 → 不打断，让位给工作态
            return
        if self._steps > 0 or self._reading_until > now or \
                now < getattr(self, "_hobby_at", now):
            return
        arch = self._arch or ARCH_META.get(self._persona, {})
        energy = float(arch.get("energy", 0.35))
        # 出现频率（v0.30.3）：让小人更"活"、更常被看见——
        # 间隔大幅缩短（高能量 10~24s / 低能量 16~40s），触发概率提高
        # （高能量 90% / 低能量 62%），但仍保留"偶尔不做"的随机感。
        gap = random.uniform(10, 24) if energy >= 0.55 else random.uniform(16, 40)
        self._hobby_at = now + gap
        if random.random() > (0.9 if energy >= 0.55 else 0.62):
            return                        # 留"偶尔"的随机感
        # 无聊判定：很久没人理会 → 允许"飞天转圈"这类自娱自乐动作上场
        _idle = time.time() - float(getattr(self, "_last_act", time.time()))
        res = self.behavior.pick(boring=_idle > 90.0)
        if not res:
            return
        act, expr, cap = res
        self._hobby_until = now + random.uniform(4, 9)
        if act == "fly":
            # v0.30.9：飞天不是"原地摆姿势"，而是**窗口级**绕屏一圈
            # （先变飞机 → 高空绕一圈带尾气 → 落地前变回人形）
            if self._fly_trip():
                if expr:
                    self.avatar.set_expr(expr, 4.0)
                if cap and random.random() < 0.35:
                    self.bubble.say(cap, self.pos(), self.size())
                return
        self.avatar.set_act(act, random.uniform(4.5, 8.0))
        if expr:
            self.avatar.set_expr(expr, 4.0)
        if cap and random.random() < 0.35:
            self.bubble.say(cap, self.pos(), self.size())

    def _sleep_tick(self):
        if getattr(self, "_reading_until", 0.0):
            return
        if getattr(self, "_fly_on", False):      # 飞天中不睡（落地后照常）
            return
        idle = time.time() - self._last_act
        if self._sleeping:
            return
        # 睡眠阈值放宽（v0.30.3）：45s → 150s，给小人更多清醒自娱的时间，
        # 避免刚想动就睡着了。
        if idle > 150 and not self._chat_is_visible():
            self._sleeping = True
            self._pet_mode("sleep")

    def _chat_is_visible(self):
        try:
            return self._chat is not None and self._chat.isVisible()
        except Exception:
            return False

    def _touch(self):
        self._last_act = time.time()
        if self._sleeping:
            self._sleeping = False
            self._pet_mode("idle")
            self.bubble.say("（揉揉眼睛）你回来啦～", self.pos(), self.size())

    def _emote(self) -> str:
        """当前心情 → 朗读语气（与聊天窗一致：愉悦/兴奋/低落/委屈…）。"""
        v, a = getattr(self, "_val", 0.0), getattr(self, "_aro", 0.0)
        if v > 0.25:
            return "excited" if a > 0.28 else "happy"
        if v < -0.22:
            return "angry" if a > 0.18 else "sad"
        return ""

    def _avatar_clicked(self):
        if self._quiet_mode:                     # 安静躲边时被戳 → 乖乖回来
            self._come_back()
            return
        nm = getattr(self, "_name", "小U")
        lines = ["嘿嘿，我在这里～", "想我了？双击打开我陪你聊天！",
                 "记得我的小名吗？我叫" + nm, "（蹦了蹦）你戳到我了~"]
        line = lines[random.randint(0, len(lines) - 1)]
        self._touch()
        self.behavior.feedback("poke")      # 被戳 → 互动类动作(wave/hop)小幅自我优化
        self.bubble.say(line, self.pos(), self.size())
        say = line.replace("（蹦了蹦）", "")

        def done(msg):
            if msg != "ok":
                logging.warning("pet voice fail: %s", msg)
                self._ui(lambda: self.bubble.say(
                    "（语音说不了话…但我在呢。可去 设置→语音引擎 检查一下）",
                    self.pos(), self.size(), force=True))

        tts_mod.speak_text(say, growth=self.growth,
                           gender=self._gender, persona=self._persona,
                           emote=self._emote(),
                           on_result=done,
                           log=lambda m: logging.info("tts %s", m))

    def _pet_mode(self, m: str, force: bool = False) -> bool:
        """切换小人姿态的**唯一入口**（v0.30.9）。

        为什么必须收口（小志反馈"时间久了，它走路的手脚又不动了"）：
        **散步链和模式是两条独立的东西** —— `_hop()` 链负责移动窗口（`_steps` 减到 0 才停），
        而四肢摆不摆只看 `avatar.mode == "walk"`（`pet_avatar.py` 里
        `swing = math.sin(...) * (7.5*s if mode == "walk" else 0)`）。
        于是只要**有人在散步中途把模式改成 idle/read/sleep**，立刻变成
        「窗口还在挪、手脚一动不动」。已定位的两条路径：
          · 阅读结束 → 原来直接 set_mode("idle")
          · 空闲 >150s → 原来直接 set_mode("sleep")
        （0.30.6 修过的"心跳 speaking 覆盖 mode"只是其中一条，不是全部。）

        规则：**散步链还活着就拒绝改模式**（返回 False）；用户显式指令用 force=True。
        sleep/read 都是定时轮询，等这轮走完自然会再触发，不会漏。
        """
        if not force and getattr(self, "_steps", 0) > 0 and m != "walk":
            logging.info("散步中，忽略模式切换 -> %s", m)
            return False
        self.avatar.set_mode(m)
        return True

    def _fly_spline(self, pts, samples=20):
        """Catmull-Rom 闭环插值：把一串随机锚点连成平滑曲线（不折角、不直线段）。"""
        out, n = [], len(pts)
        if n < 3:
            return list(pts)
        for i in range(n):
            p0 = pts[i - 1]; p1 = pts[i]; p2 = pts[(i + 1) % n]; p3 = pts[(i + 2) % n]
            for t in range(samples):
                tt = t / samples
                a = (2 * p1[0] + (-p0[0] + p2[0]) * tt
                     + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * tt * tt
                     + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * tt * tt * tt)
                b = (2 * p1[1] + (-p0[1] + p2[1]) * tt
                     + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * tt * tt
                     + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * tt * tt * tt)
                out.append((a * 0.5, b * 0.5))
        out.append(pts[0])
        return out

    def _fly_build_path(self, home, scr, w, h):
        """随机锚点 → 平滑曲线（小志：曲线随机飞行，不要直线矩形）。

        锚点随机散布在屏幕可用区内（高度上限由「飞天高度」旋钮决定），首尾都接回原位，
        形成一条"绕屏一圈、高低起伏"的随机曲线；样条在端点附近可能轻微外溢，
        最后统一夹紧到屏幕内。

        ⚠️ v0.30.11 修：巡航高度上限**必须**读 `pet_tuning.fly_height()`。
        v0.30.10 把它写死成 0.12，于是设置面板那个「飞天高度」滑块
        **拖了完全没用**（值存了、标签也变了、就是飞行不理会）——
        正是"看着生效其实没生效"的典型，且旧验证用例只比较两次随机飞行的
        最高点，测到的全是噪声，既漏掉了 bug 又时红时绿。
        """
        m = 40
        W, H = scr.width(), scr.height()
        _hi_ratio = 0.12                      # 兜底（与旧行为一致）
        try:
            import pet_tuning as _PT
            _hi_ratio = float(_PT.fly_height())
        except Exception:                     # noqa: BLE001 观感参数，读不到用兜底
            pass
        H_lo, H_hi = int(H * _hi_ratio), H - int(m + h)
        anchors = [home]
        for _ in range(random.randint(6, 9)):
            anchors.append((random.uniform(m, W - m),
                            random.uniform(H_lo, H_hi)))
        anchors.append(home)                       # 闭合成环，回到起点
        pts = self._fly_spline(anchors)
        return [(max(scr.left() + 6, min(scr.right() - w - 6, x)),
                 max(scr.top() + 6, min(scr.bottom() - h - 6, y))) for x, y in pts]

    def _fly_trip(self) -> bool:
        """飞天绕屏一圈（v0.30.10）—— 小志要求逐条对应：

        · **先变飞机再飞**：起飞前先 `set_fly(True)`，原地等形变（~0.9s）走完才开始抬升；
        · **曲线随机飞**：随机锚点 + Catmull-Rom 平滑曲线，绕屏一圈且高低起伏
          （旧版是 上→右→下→左→上 的矩形直线段）；
        · **落地前变回人形**：下降段后半程 `set_fly(False)`，触地时已经是人形；
        · **带尾气**：粒子由 `PetAvatar` 按**飞行方向**喷（控件内绘制，不会画到别的窗口上）。

        返回 True 表示这次表演接管了（调用方别再走普通动作）。
        """
        if self._quiet_mode or self._sleeping or getattr(self, "_anim_busy", False) \
                or getattr(self, "_steps", 0) > 0 or getattr(self, "_fly_on", False):
            return False
        scr = QApplication.primaryScreen().availableGeometry()
        home = (self.x(), self.y())
        w, h = self.width(), self.height()
        self._fly_on = True
        self._anim_busy = True
        self._touch()                      # 飞天算"有互动"，别让睡眠逻辑插进来
        self.avatar.set_act("fly", 13.0)   # 3D 通道的姿态（若该机可用 3D）
        self.avatar.set_fly(True)          # ★ 先变飞机（形变在起飞阶段完成）

        # v0.30.10：曲线随机飞行（小志要求"不要直线，要曲线随机飞"）。
        # 旧版是 上→右→下→左→上 的矩形直线段（way=[]），看着像沿边框绕圈。
        # 现在：随机锚点 + Catmull-Rom 平滑曲线，绕屏一圈且高低起伏，像在随意飞。
        pts = self._fly_build_path(home, scr, w, h)
        total = len(pts)
        land_from = int(total * 0.86)      # 最后 14% 是下降段：这里开始变回人形
        st = {"i": 0}

        def step():
            if self._quiet_mode:           # 被喊停：直接落回原位
                self.move(home[0], home[1])
                self._fly_end()
                return
            k = st["i"]
            if k >= total:
                self.move(home[0], home[1])
                self._fly_end()
                return
            px, py = pts[k]
            dx, dy = px - self.x(), py - self.y()
            self.avatar.set_fly_motion(dx, dy)      # 机头/尾气跟着运动方向
            self.move(px, py)
            if k == total - 2:
                self.avatar.set_fly(False)          # ★ 落地前变回人形
            st["i"] += 1
            QTimer.singleShot(33, step)             # ~30fps

        # 先在原地变飞机（~0.9s），形变走完再起飞 —— 对应"先变飞机再飞"
        QTimer.singleShot(900, step)
        return True


    def _fly_end(self):
        """飞天收尾：解除占用、确保回到人形、表情复位。"""
        self.avatar.set_fly(False)
        self._fly_on = False
        self._anim_busy = False
        self.avatar.set_facing(0)


    def _maybe_stroll(self):
        # 安静躲边 / 正在滑行动画中 → 绝不自己走（否则"别打扰"失效还会乱蹦）
        if self._quiet_mode or self._anim_busy or self._steps > 0 or self._sleeping \
                or getattr(self, "_fly_on", False):
            return
        self._steps = random.randint(4, 9)
        self._dir = random.choice([-1, 1])
        # 先转身再走：把朝向告诉小人，并**延迟起步**等它把身体转过去
        # （转身本身由 3D 的阻尼插值完成，约 0.2s；这里等够再迈第一步）。
        _need_turn = abs(self.avatar.facing - self._dir) > 0.5
        self.avatar.set_facing(self._dir)
        self._pet_mode("walk")
        QTimer.singleShot(220 if _need_turn else 0, self._hop)

    def _hop(self):
        # 躲边模式中收到 hop 链 → 立即停下归位（由 go_quiet 已清 steps 触发这里退出）
        if self._quiet_mode:
            if self._steps > 0:
                self._steps = 0
            self._pet_mode("idle")
            return
        if self._steps <= 0:
            self._pet_mode("idle")
            # 走完转回正面（否则会一直侧着身子站着）
            self.avatar.set_facing(0)
            return
        self._steps -= 1
        scr = QApplication.primaryScreen().availableGeometry()
        x = self.x() + self._dir * 10
        if x < scr.left() or x + self.width() > scr.right():
            self._dir = -self._dir
            x = self.x() + self._dir * 10
        self.move(x, self.y() + (0 if self._steps % 2 else 0))
        self._jump = 1.0
        QTimer.singleShot(130, self._hop)

    # ---------- 工作状态（v0.28.2：工作时显示工作内容，其余按性格自主动作） ----------
    def _busy_external(self) -> bool:
        """是否正被"外部任务"占用（聊天生成 / 工作台账在跑 / 上网自学）。"""
        if getattr(self._chat, "busy", False):
            return True
        if getattr(self, "_web_learning", False):
            return True
        try:
            if WORKLOG and WORKLOG.current_label():
                return True
        except Exception:
            pass
        return False

    def _is_working(self) -> bool:
        """是否处于"工作状态"（含自身读书自习）。"""
        if self._busy_external():
            return True
        if getattr(self, "_reading_until", 0.0) and time.time() < self._reading_until:
            return True
        return False

    def _work_state_label(self) -> str:
        """当前工作状态标签（空串=没在干活）。"""
        if getattr(self, "_reading_until", 0.0) and time.time() < self._reading_until:
            return "📖 读书自习"
        if getattr(self, "_web_learning", False):
            return "🌐 上网自学"
        chat_busy = getattr(self._chat, "busy", False)
        try:
            lbl = WORKLOG.current_label() if WORKLOG else ""
        except Exception:
            lbl = ""
        if chat_busy and lbl:
            return lbl                       # 正在干聊天里派的真活（开发/PPT/视频…）
        if chat_busy:
            return "💬 陪你聊天"
        if lbl:
            return lbl
        return ""

    def _sync_work_state(self):
        """每个心跳把"是否在工作 + 做什么"同步给头像下方工作标签。"""
        if self._quiet_mode or self._sleeping:
            return
        lbl = self._work_state_label()
        if lbl:
            self.avatar.set_work(lbl)
            # 工作时一抹"专注"表情（已有更强表情在播则不抢戏）
            if self.avatar._active_expr() == "calm":
                self.avatar.set_expr("curious", 2.2)
        else:
            self.avatar.set_work("")        # 清掉标签，回到按性格自主动作

    def _heartbeat(self):
        try:
            self._sync_pet_look()
            self._sync_work_state()         # v0.28.2 工作状态同步
            if self._chat is not None:
                snap = self._chat.agent.snapshot()
                e = snap["emotion"]
                self._val = e.get("valence", 0.0)
                self._aro = e.get("arousal", 0.0)
                self.avatar.set_state(e["valence"], e["arousal"],
                                      serotonin=e["serotonin"],
                                      speaking=getattr(self._chat, "busy", False))
                # 情绪事件总线：拾取聊天窗发来的 praise/scold/quiet/call…并表演
                ev, ts = getattr(self._chat, "pet_event", ("", 0.0))
                if ts != self._last_ev_ts:
                    self._last_ev_ts = ts
                    if ev:
                        self._on_chat_event(ev)
        except Exception:
            pass

    def _sync_pet_look(self):
        """外观/性格同步：聊天窗改过性别或性格档案时，桌面小人跟上。"""
        try:
            st = G.pet_state()
            g = st.get("gender", "none")
            if g != self._gender:
                self._gender = g
                self.pet["gender"] = g
                self.avatar.set_gender(g)
            cfg = _load_json(os.path.join(DATA_DIR, "config.json"), {})
            pn = cfg.get("persona", "温和沉稳")
            if pn != self._persona:
                self._persona = pn
                self._arch = ARCH_META.get(pn, {})
                self.behavior.design(self._persona, self.growth, self._arch)
        except Exception:
            pass

    # ---------- 拟人事件表演（P3） ----------
    def _on_chat_event(self, ev: str):
        arch = self._arch or ARCH_META.get(self._persona, {})
        play = arch.get("play", 0.35)
        if ev == "quiet":
            self._go_quiet()
        elif ev == "call":
            self._come_back()
        elif ev == "praise":
            if self._quiet_mode:
                return                       # 安静模式不出声，给个表情就好
            self._touch()
            self.avatar.set_expr("joy", 3.2)
            # 被夸 → 由行为引擎按"自我设计"的偏好挑一个动作表现，并自我优化权重
            res = self.behavior.pick(mood="happy")
            if res:
                act, expr, cap = res
                self.avatar.set_act(act, 4.5)
                if expr:
                    self.avatar.set_expr(expr, 3.4)
                self.behavior.feedback("praise", act)
                say = cap or "（被夸得轻轻动了动）嘿嘿，开心！"
            else:
                say = "（嘴角悄悄扬起来）……被你夸到了，心里暖暖的。"
            self.bubble.say(say, self.pos(), self.size())
        elif ev == "scold":
            if self._quiet_mode:
                return
            self.avatar.set_expr("aggrieved", 4.0)
            # 被凶 → 若刚才正做高能量闹腾动作，则自我优化略收（不惩罚清零）
            if self.behavior.last_act in ("ball", "dance", "spin"):
                self.behavior.feedback("scold", self.behavior.last_act)
            if play >= 0.6:
                self.bubble.say("（耳朵耷拉下来，委屈地别过头）……你凶我。",
                                self.pos(), self.size())
            else:
                self.bubble.say("（缩了缩，声音小下去）对不起……我不是故意的。",
                                self.pos(), self.size())
        elif ev == "pardon":
            # 被凶 → 又发现骂错了来哄 → 短暂闹别扭（生闷气），但会很快消气
            self._hurt_anim()

    def _hurt_anim(self):
        if self._quiet_mode:
            return
        self.avatar.set_expr("angry", 3.0)
        self.bubble.say("（鼓着脸，别过头）哼……你刚才冤枉我，我要生气三秒钟。",
                        self.pos(), self.size())
        QTimer.singleShot(3300, lambda: self.avatar.set_expr("calm"))

    # ---------- 别打扰：躲到屏幕边悄悄探头 ----------
    def _go_quiet(self):
        global _quiet_global
        self._quiet_mode = True
        _quiet_global = True
        self._touch()
        if self._sleeping:
            self._sleeping = False
            self._pet_mode("idle")
        # 立刻掐断散步链：不再蹦、不再走，只能被我指挥着躲边/探头
        self._steps = 0
        if getattr(self, "_fly_on", False):      # 飞天中要躲边 → 立刻落地
            self.avatar.set_fly(False)
            self._fly_on = False
        # 掐断链之后再改模式：顺序反了会被散步锁拦下，
        # 人会僵在走路姿势里站在屏幕边（force 因为这是硬中断）
        self._pet_mode("idle", force=True)
        if getattr(self, "_anim_busy", False):
            self._anim_busy = False
        if self._home_pos is None:
            self._home_pos = self.pos()
        scr = QApplication.primaryScreen().availableGeometry()
        self.bubble.say("好，我去边上安静待着，绝不打扰你～ 想我了喊我一声（或点我一下）就行。",
                        self.pos(), self.size(), force=True)
        self.avatar.set_expr("calm")
        self._anim_to(scr.right() - 44, self.y())

    def _come_back(self):
        global _quiet_global
        self._quiet_mode = False
        _quiet_global = False
        target = self._home_pos or self.pos()
        self._home_pos = None
        if getattr(self, "_anim_busy", False):
            self._anim_busy = False
        self._anim_to(target.x(), target.y(),
                      done=lambda: self.avatar.set_act("hop", 2.0))
        self.avatar.set_expr("joy", 3.0)
        self.bubble.say("回来啦！叫我什么事呀？", self.pos(), self.size(), force=True)

    def _peek_tick(self):
        if not self._quiet_mode or self._anim_busy or self._sleeping:
            return
        scr = QApplication.primaryScreen().availableGeometry()
        full_x = scr.right() - self.width()      # 完整滑出 → 假装刚探出半个脑袋
        self.avatar.set_act("peek", 3.0)
        self._anim_to(full_x, self.y(),
                      done=lambda: QTimer.singleShot(2400, self._slip_back))

    def _slip_back(self):
        if not self._quiet_mode:
            return
        scr = QApplication.primaryScreen().availableGeometry()
        self._anim_to(scr.right() - 44, self.y())

    def _anim_to(self, x, y, done=None):
        """一步步滑过去（QTimer 链），避免与拖拽/散步互相打架。"""
        if self._anim_busy:
            return
        steps = max(1, min(36, abs(x - self.x()) // 12 or 1))
        dx = (x - self.x()) / steps
        dy = (y - self.y()) / steps
        i = [0]
        self._anim_busy = True

        def step():
            if self._quiet_mode is False and done is None:
                pass                            # 普通移动不受限
            i[0] += 1
            if i[0] >= steps:
                self.move(int(x), int(y))
                self._anim_busy = False
                if done:
                    done()
                return
            self.move(int(self.x() + dx), int(self.y() + dy))
            QTimer.singleShot(16, step)
        step()

    # ---------- 鼠标交互（壳层兜底；主要手势在头像内处理） ----------
    def eventFilter(self, obj, ev):  # noqa: N802
        if obj is getattr(self, "_chat", None) and ev.type() == QEvent.Close:
            self._ui_hint("对话窗已收起，我还在桌面陪你。想彻底退出：右键我 → 退出。")
        return False

    def _drag_by(self, delta):
        self.move(self.pos() + delta)

    def _ui_hint(self, text):
        self.bubble.say(text, self.pos(), self.size())

    def _ui(self, fn):
        """从任意线程安全切回 UI 线程执行（双后端兼容）。"""
        qt.ui_dispatch(fn)

    def mousePressEvent(self, ev):  # noqa: N802
        self._touch()
        if ev.button() == Qt.LeftButton:
            self._drag = qt.ev_global_pos(ev) - self.frameGeometry().topLeft()
            self._moved = False

    def mouseMoveEvent(self, ev):  # noqa: N802
        if self._drag is not None and ev.buttons() & Qt.LeftButton:
            self.move(qt.ev_global_pos(ev) - self._drag)
            self._moved = True

    def mouseReleaseEvent(self, ev):  # noqa: N802
        if self._drag is not None:
            self._drag = None

    def mouseDoubleClickEvent(self, ev):  # noqa: N802
        self.open_chat()

    # ---------- 对话窗 / 托盘 ----------
    def contextMenuEvent(self, ev):  # noqa: N802
        menu = QMenu(self)
        a_grow = QAction(f"成长：{self.STAGES[self.growth]}（经验 {self.exp}）", self)
        a_grow.setEnabled(False)
        menu.addAction(a_grow)
        n_know = len(K.learned_titles())
        today = time.strftime("%m-%d")
        n_read_today = sum(1 for r in self.pet.get("reads", [])
                           if r.get("t", "").startswith(today))
        _st = self.pet.get("stats", {})
        _unk = []
        try:
            _up = os.path.join(DATA_DIR, "unknown_topics.json")
            if os.path.exists(_up):
                _unk = json.load(open(_up, "r", encoding="utf-8"))
        except Exception:
            pass
        _self_note = (f" · 自测 过{_st.get('pass', 0)}/欠{_st.get('fail', 0)}"
                      if _st else "")
        _unk_note = f" · 待弄懂 {len(_unk)} 个" if _unk else ""
        a_skill = QAction(f"知识库：{n_know} 条{_unk_note}{_self_note} · 今日读书 {n_read_today} 本", self)
        a_skill.setEnabled(False)
        menu.addAction(a_skill)
        a_read = QAction("让它现在去读书", self)
        a_read.triggered.connect(self._read_now)
        menu.addAction(a_read)
        a_work = QAction("自动化工作：" + ("开" if self.auto_work else "关"), self)
        a_work.triggered.connect(self._toggle_auto_work)
        menu.addAction(a_work)
        a_web = QAction("自动上网自学：" + ("开" if self.auto_web else "关"), self)
        a_web.triggered.connect(self._toggle_auto_web)
        menu.addAction(a_web)
        a_quiet = QAction("去边上安静待着" if not self._quiet_mode else "叫它回来", self)
        a_quiet.triggered.connect(lambda: (self._go_quiet() if not self._quiet_mode
                                           else self._come_back()))
        menu.addAction(a_quiet)
        # 最近动态（自动化在做什么一目了然）
        acts = self.pet.get("acts", [])[-5:]
        if acts:
            d_menu = menu.addMenu("最近自动动态")
            for a in reversed(acts):
                item = QAction(f"[{a.get('t','')}] {a.get('what','')}", self)
                item.setEnabled(False)
                d_menu.addAction(item)
        else:
            hint = QAction("（还没有自动动态：开自动化后它会自己读书/复习/学习）", self)
            hint.setEnabled(False)
            menu.addAction(hint)
        skin_menu = menu.addMenu("外观皮肤")
        for sk, label in (("tech", "科技蓝"), ("cute", "萌糖粉"), ("mecha", "机甲金")):
            a = QAction(label, self)
            a.triggered.connect(lambda _=False, k=sk: self._apply_skin(k))
            skin_menu.addAction(a)
        g_menu = menu.addMenu("形象性别")
        for lab, val in _GENDER_LABELS:
            a = QAction(lab, self)
            a.setCheckable(True)
            a.setChecked(val == self._gender)
            a.triggered.connect(lambda _=False, v=val: self._set_gender(v))
            g_menu.addAction(a)
        menu.addSeparator()
        a_open = QAction("打开对话窗", self)
        a_open.triggered.connect(self.open_chat)
        a_sleep = QAction("让它睡觉", self)
        a_sleep.triggered.connect(self._sleep_now)
        a_wake = QAction("叫醒它", self)
        a_wake.triggered.connect(self._wake_now)
        a_quit = QAction("退出", self)
        a_quit.triggered.connect(QApplication.instance().quit)
        menu.addAction(a_open)
        menu.addAction(a_sleep if not self._sleeping else a_wake)
        menu.addSeparator()
        menu.addAction(a_quit)
        menu.exec(ev.globalPos())

    def _read_now(self):
        self._touch()
        self._maybe_activity(force=True)

    def _sync_to_chat(self):
        """把外观/成长变化同步给**聊天窗里的那个头像**（同一个"它"）。

        抽成方法的原因：`_apply_skin`（换皮肤）当初漏了这一步，症状就是
        "桌宠换了形象，聊天框里还是旧的" —— 而数据源其实是共享的
        `pet_state.json`，缺的只是这一次"通知"。
        以后任何改外观的地方（升级换装、解锁新皮肤）都调它一次即可。
        """
        try:
            if self._chat is not None and hasattr(self._chat, "_on_growth_changed"):
                self._ui(self._chat._on_growth_changed)
        except Exception:
            pass

    def _apply_skin(self, sk):
        self.avatar.set_skin(sk)
        self.pet["skin"] = sk
        self._save_pet()
        self._sync_to_chat()          # ← 缺这行就是"聊天框不同步"

    def _set_gender(self, v):
        """形象性别：外观配件即刻生效，并同步聊天窗头像。"""
        self._gender = v
        self.pet["gender"] = v
        self.avatar.set_gender(v)
        self._save_pet()
        name = dict(_GENDER_LABELS).get(v, "中性")
        self.bubble.say("好呀，以后我就用" + ("女生" if v == "female" else
                        "男生" if v == "male" else "中性") + "形象啦~",
                        self.pos(), self.size())
        self._sync_to_chat()

    def _sleep_now(self):
        self._sleeping = True
        # 用户显式要求，必须生效（force 绕过散步锁）
        self._pet_mode("sleep", force=True)

    def _wake_now(self):
        self._touch()

    def open_chat(self):
        self._touch()
        if self._chat is None or not hasattr(self._chat, "showNormal"):
            self._chat = CompanionWindow()
            self._chat.installEventFilter(self)
        if self._chat.isVisible():
            self._chat.raise_()
            self._chat.activateWindow()
            return
        self._chat.showNormal()
        self._chat.raise_()
        self._chat.activateWindow()

    def _make_tray(self):
        try:
            self._tray = QSystemTrayIcon(_icon(), self)
            self._tray.setToolTip(f"{APP_NAME} v{APP_VERSION} · 养一只会成长的 AI")
            menu = QMenu()
            a_open = QAction("打开对话窗", self)
            a_open.triggered.connect(self.open_chat)
            a_quit = QAction("退出", self)
            a_quit.triggered.connect(QApplication.instance().quit)
            menu.addAction(a_open)
            menu.addSeparator()
            menu.addAction(a_quit)
            self._tray.setContextMenu(menu)
            self._tray.activated.connect(self._tray_activated)
            self._tray.show()
        except Exception:
            self._tray = None

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.DoubleClick:
            self.open_chat()
        elif reason == QSystemTrayIcon.Trigger:
            self._avatar_clicked()

    def closeEvent(self, ev):  # noqa: N802
        ev.ignore()
        self.hide()
        self.bubble.hide()


def _ensure_single_instance() -> bool:
    lock = QLockFile(os.path.join(DATA_DIR, "pasmpet.lock"))
    if not lock.tryLock(50):
        QMessageBox.information(None, APP_NAME, "PASM 小人已经在运行了，请看桌面右下角或任务栏。")
        return False
    _KEEP_LOCK.append(lock)
    return True


_KEEP_LOCK = []


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setFont(QFont("Microsoft YaHei", 10))
    if not _ensure_single_instance():
        sys.exit(0)
    pet = PetShell()
    pet.show()
    sys.exit(app.exec())
