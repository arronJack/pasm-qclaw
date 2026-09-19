# -*- coding: utf-8 -*-
"""语音唤醒 —— 用「短时 ASR + 开头呼名」做伪唤醒（零训练、断网可用）。

为什么不做真正的 KWS（关键词检测）：
    sherpa-onnx 那类 KWS 需要针对唤醒词**单独训练模型**，换个名字就得重训。
    而产品要求是「改名字后唤醒词跟着变」—— KWS 结构上做不到，
    伪唤醒可以：唤醒词就是配置里的角色名，纯文本匹配，改名即生效。
    代价是比 KWS 费一点 CPU，换来零训练 + 任意改名 + 断网可用。

判定规则（**故意从严**）：
    只有「开头呼名」才算唤醒 —— 允许名字前面出现称谓/语气词，
    但**不接受"句中提到名字"**（"我今天和小U聊天"不该唤醒）。
    识别不准时**宁可不唤醒**：误唤醒会打断用户正在做的事，比漏唤醒恼人得多。

安全约束：
    · 必须可一键关闭（常驻监听占着麦克风）
    · 音频只在识别引擎内部流转，本模块**不落盘任何音频**
    · 连续异常自动停止，不做无限重试（不能因为麦克风坏了就一直转）
"""
from __future__ import annotations

import re
import sys
import threading
import time

# 名字前面允许出现的语气/称谓（"小U" / "喂小U" / "哎，小U" 都算叫它）
_PREFIX_NOISE = r"(?:\s|，|,|。|、|：|:|！|!|？|\?|哎|诶|喂|嘿|嗨|那个|你好)*"
# 名字后面必须是停顿、语气助词、句尾或请求词。
# 关键："小U帮我看看"✓ / "小U盘在哪"✗ —— 后者是同音词误撞，绝不能被唤醒。
# 所以允许列表是**穷举**的，绝不写成 "名字+任意字符"。
_SUFFIX_STOP = (r"(?:\s|，|,|。|、|：|:|！|!|？|\?|$|啊|呀|哎|诶|哦|噢|哈|"
                r"帮我|请|你|能不能|可以|来|看|听|在吗|在不在|你在)")


def normalize(text) -> str:
    """把识别文本压平：去空白、常见同音标点统一。"""
    try:
        s = str(text or "")
    except Exception:
        return ""
    s = s.replace("\u3000", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def wake_word_of(name) -> str:
    """从角色名推出唤醒词 —— 名字本身就是唤醒词（去掉首尾空白）。"""
    try:
        s = str(name or "").strip()
    except Exception:
        return ""
    return s[:12]                      # 超长名字截断，避免正则被撑爆


def matched(text, name) -> bool:
    """判断一段识别文本是否在**呼叫**这个名字。

    只认「开头呼名」，且名字后面要跟停顿或请求词 —— 这是为了让
    "小U盘在哪"这类同音误撞不会把助手喊醒（实测这类误唤醒最恼人）。
    """
    t = normalize(text)
    w = wake_word_of(name)
    if not t or not w:
        return False
    try:
        pat = "^" + _PREFIX_NOISE + re.escape(w) + _SUFFIX_STOP
        if re.search(pat, t):
            return True
        # 整句就等于名字（"小U"）—— 也算呼名
        if t == w:
            return True
        return False
    except Exception:
        return False


def strip_wake_word(text, name) -> str:
    """把唤醒词从文本里剥掉，剩下的当指令（"小U，帮我查天气" → "帮我查天气"）。"""
    t = normalize(text)
    w = wake_word_of(name)
    if not t or not w:
        return t
    try:
        pat = "^" + _PREFIX_NOISE + re.escape(w) + r"\s*[，,。、：:！!？?]?\s*"
        return re.sub(pat, "", t, count=1).strip()
    except Exception:
        return t


class WakeWord(object):
    """常驻监听器（伪唤醒）。

    用法：
        w = WakeWord(name_getter=lambda: cfg.get("name", "小U"),
                     on_wake=lambda cmd: ..., listen_fn=asr.listen_once)
        w.start()      # 开始监听
        w.stop()       # 停止（释放麦克风）
        w.set_name("新名字")   # 改名即时生效，不用重启
    """

    def __init__(self, name_getter, on_wake=None, listen_fn=None,
                 interval=0.25, min_gap=1.2):
        self._name_getter = name_getter
        self._on_wake = on_wake
        self._listen_fn = listen_fn
        self._interval = float(interval)
        self._min_gap = float(min_gap)
        self._stop_ev = threading.Event()
        self._th = None
        self._name = ""
        self.hits = 0
        self.errors = 0
        self.last_text = ""
        self.last_error = ""

    # ---------- 生命周期 ----------
    @property
    def running(self) -> bool:
        return self._th is not None and self._th.is_alive()

    def start(self):
        """开始监听。已在跑就什么都不做（幂等）。"""
        if self.running:
            return False
        if self._listen_fn is None:
            self.last_error = "没有可用的识别通道"
            return False
        self._stop_ev.clear()
        self._refresh_name()
        self._th = threading.Thread(target=self._loop, name="pasm-wake",
                                    daemon=True)
        self._th.start()
        return True

    def stop(self, join_timeout=1.5):
        """停止监听并释放麦克风（**幂等**，可重复调）。"""
        self._stop_ev.set()
        th = self._th
        self._th = None
        if th is not None and th.is_alive():
            try:
                th.join(timeout=join_timeout)
            except Exception:
                pass
        return True

    def set_name(self, name):
        """改名即时生效 —— 唤醒词就是角色名，不需要重训也不需要重启。"""
        self._name = wake_word_of(name)
        return self._name

    def _refresh_name(self):
        try:
            self._name = wake_word_of(self._name_getter())
        except Exception:
            self._name = ""
        return self._name

    # ---------- 主循环 ----------
    def _loop(self):
        while not self._stop_ev.is_set():
            try:
                self._refresh_name()          # 每轮重读名字 → 改名立刻跟上
                if not self._name:
                    time.sleep(self._interval)
                    continue
                res = self._listen_fn(timeout=4.0)
                text = res[0] if isinstance(res, tuple) else res
                text = normalize(text)
                if not text:
                    continue
                self.last_text = text
                if matched(text, self._name):
                    self.hits += 1
                    cmd = strip_wake_word(text, self._name)
                    # 唤醒后先停（把麦克风让给正常对话流程），由界面决定何时重启
                    self._stop_ev.set()
                    try:
                        if self._on_wake:
                            self._on_wake(cmd)
                    except Exception as e:
                        self.last_error = "回调异常: %s" % e
                    return
            except Exception as e:
                self.errors += 1
                self.last_error = "%s: %s" % (type(e).__name__, e)
                # 连续异常就放弃 —— 不能因为麦克风坏了让线程一直空转
                if self.errors >= 5:
                    self.last_error = "连续 %d 次异常，已停止监听（%s）" % (
                        self.errors, self.last_error)
                    self._stop_ev.set()
                    return
                time.sleep(max(0.5, self._interval))
        return

    def status(self) -> str:
        if self.running:
            return "监听中（唤醒词：%s）" % (self._name or "未设置")
        if self.last_error:
            return "已停止（%s）" % self.last_error
        return "未开启"


# ——————————————————————————————————————————————————————
# 自检（纯逻辑，不需要麦克风；把"误唤醒"当成主要敌人来测）
# ——————————————————————————————————————————————————————

def selftest() -> int:
    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS %s %s" % (name, extra))
        else:
            failed += 1
            print("FAIL %s %s" % (name, extra))

    N = "小U"
    # 1) 该唤醒的
    check("直呼其名", matched("小U", N))
    check("带语气词", matched("喂，小U", N))
    check("名字+请求", matched("小U帮我查一下天气", N))
    check("名字+冒号", matched("小U：现在几点", N))
    check("名字+问句", matched("小U在吗", N))
    check("识别带句号", matched("小U。", N))
    check("念了两次也只算一次", matched("小U，小U你好", N))

    # 2) 绝不该唤醒的（误唤醒是主要敌人）
    check("句中提到名字不唤醒", not matched("我今天和小U聊过天", N))
    check("同音词不唤醒（U盘）", not matched("小U盘在哪", N))
    check("完全无关不唤醒", not matched("今天天气不错", N))
    check("空文本不唤醒", not matched("", N) and not matched(None, N))
    check("名字为空不唤醒", not matched("小U你好", ""))
    check("只提到请求不唤醒", not matched("帮我查一下天气", N))

    # 3) 改名即时生效
    check("改名后新名字可唤醒", matched("小霖在吗", "小霖"))
    check("改名后旧名字失效", not matched("小U在吗", "小霖"))
    check("wake_word_of 去空白", wake_word_of("  小U  ") == "小U")
    check("wake_word_of 超长截断", len(wake_word_of("名" * 40)) == 12)
    check("wake_word_of 脏输入不崩", wake_word_of(None) == "")

    # 4) 剥词
    check("剥掉唤醒词", strip_wake_word("小U，帮我查天气", N) == "帮我查天气")
    check("剥掉带语气词的", strip_wake_word("喂小U 打开文件夹", N) == "打开文件夹")
    check("只有名字时剥成空", strip_wake_word("小U", N) == "")

    # 5) 同音/近似不误撞的边界（防"越改越松"）
    check("“小U啊”算唤醒", matched("小U啊", N))
    check("“不小U”不唤醒", not matched("不小U", N))

    # 6) 生命周期（用假 listen_fn，不碰真麦克风）
    seq = ["", "今天天气", "小U帮我看下日程"]
    box = {"i": 0, "woke": None, "stop": None}

    def fake_listen(timeout=4.0):
        i = box["i"]
        box["i"] += 1
        if i < len(seq):
            return seq[i]
        # 说完就不再返回内容，避免死循环
        time.sleep(0.05)
        return ""

    w = WakeWord(name_getter=lambda: N, on_wake=lambda c: box.__setitem__("woke", c),
                 listen_fn=fake_listen, interval=0.02)
    check("初始未运行", w.running is False)
    check("start 返回 True", w.start() is True)
    t0 = time.time()
    while box["woke"] is None and time.time() - t0 < 3:
        time.sleep(0.02)
    check("命中唤醒词触发回调", box["woke"] == "帮我看下日程", repr(box["woke"]))
    check("唤醒后自动停止（让出麦克风）", not w.running)
    check("命中计数为 1", w.hits == 1, "hits=%d" % w.hits)
    check("stop 幂等", w.stop() is True and w.stop() is True)
    check("status 可读", isinstance(w.status(), str))

    # 7) 连续异常会自动停（不会无限空转）
    ebox = {"n": 0}

    def boom(timeout=4.0):
        ebox["n"] += 1
        raise RuntimeError("麦克风坏了")

    w2 = WakeWord(name_getter=lambda: N, listen_fn=boom, interval=0.01)
    w2.start()
    t0 = time.time()
    while w2.running and time.time() - t0 < 3:
        time.sleep(0.02)
    check("连续异常自动停止", not w2.running, "调用 %d 次" % ebox["n"])
    check("异常被记录下来", "麦克风坏了" in w2.last_error, w2.last_error[:40])

    # 8) 没有识别通道时不假装能跑
    w3 = WakeWord(name_getter=lambda: N, listen_fn=None)
    check("无识别通道时 start 返回 False", w3.start() is False)

    print("\n%d 项，%s" % (passed + failed, "全部通过" if not failed else "%d 项失败" % failed))
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__)
