"""进程内语音朗读 —— 性格 / 性别 / 年龄 / 情绪 都会影响"怎么说话"（v0.16.3+）。

双通道：
1. SAPI（离线兜底）：直接用 Windows 自带 PowerShell + System.Speech 朗读
   （零第三方依赖、打包冻结友好；按性别挑系统真实装着的微软中文语音，
   语速/音量随 性格 + 成长阶段 + 情绪 变化）。
2. edge-tts（在线神经音，首选）：真·音色差异 —— 幼儿童声(Xiaoshuang 女童 /
   Xiaoyou 男童，奶声奶气)、少年男声(Yunxi)、青年男声(Yunjian)、温柔女声(Xiaoxiao)，
   配合 pitch/rate/volume 实现"奶声/少年感/急性连珠炮/文静/开心/委屈"的语气差异。
   装没装、网络通不通都不影响运行：探测不到 / 连续失败自动冷却回落 SAPI，绝不假装出声。

- 全局串行锁：并发朗读串行化（PS / edge 都只同时一个）
- edge 连续失败进入冷却期（默认 90 秒内不再尝试），避免网络不稳时每句都傻等
- 长文本分块由 System.Speech 自身处理；失败结果回传 on_result（可提示用户）
"""
from __future__ import annotations

import ctypes
import os
import re
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional

_LOCK = threading.Lock()
_EDGE_STATE = "untried"        # untried | ok | off（探测一次缓存）
_EDGE_COOLDOWN_UNTIL = 0.0     # edge 失败冷却截止时间戳

# ---------- 立即停止朗读（v0.20.1：点「朗读:关」立刻静音 + 清空待读队列） ----------
_STOP = threading.Event()      # 置位后：播放通道立刻中断（MCI stop / SND_PURGE / 杀 PS 进程）
_GEN_LOCK = threading.Lock()
_GEN = 0                       # 代际计数：每次 stop +1，队列里旧代任务直接作废
_PLAYING = threading.Event()   # 当前是否真的在发声（诊断用）
_PS_PROC = None                # SAPI 直读的 PowerShell 进程引用（stop 时 terminate）
_MCI_ALIAS = "pasm_voice"      # MCI 播放别名（stop 命令按此寻址）
_LAST_ERR = ""                 # v0.27.4：最后一次失败的具体原因（供提示与自检，不再笼统）
# v0.28.1：edge 通道的**真实**失败原因与连续失败计数。
# 旧版 `except Exception: return ""` 把异常整个吞掉，voice.log 里只剩一句
# "edge synth fail" —— 明明"预检通过、合成失败"却查不出为什么（真机事故）。
_LAST_EDGE_ERR = ""
_EDGE_FAILS = 0
# 探到"服务级"不可用时要记的时间戳：TCP 能连不等于服务能合成，两者必须分开记
_EDGE_SVC_DOWN_UNTIL = 0.0

# ---------- v0.27.3 暂停 / 继续（真机痛点：只能开/关，想"先停一下再接着听"做不到）----------
_PAUSE = threading.Event()     # 置位：播放线程在块间/轮询处挂起，MCI 下 pause 命令


def pause_speaking() -> bool:
    """暂停当前朗读（保留进度，可 resume 继续）。返回是否真的暂停了。"""
    if not _PLAYING.is_set():
        return False
    _PAUSE.set()
    if os.name == "nt":
        try:
            ctypes.windll.winmm.mciSendStringW(
                f"pause {_MCI_ALIAS}", None, 0, None)
        except Exception:
            pass
    _vlog("pause_speaking")
    return True


def resume_speaking() -> bool:
    """继续被暂停的朗读。"""
    was = _PAUSE.is_set()
    _PAUSE.clear()
    if os.name == "nt" and was:
        try:
            ctypes.windll.winmm.mciSendStringW(
                f"resume {_MCI_ALIAS}", None, 0, None)
        except Exception:
            pass
    _vlog("resume_speaking was_paused=%s" % was)
    return was


def toggle_pause() -> str:
    """一键暂停/继续，返回给 UI 展示的状态：paused / resumed / idle。"""
    if _PAUSE.is_set():
        resume_speaking()
        return "resumed"
    if pause_speaking():
        return "paused"
    return "idle"


def is_paused() -> bool:
    """v0.30.6：是否处于「暂停朗读」状态（公开接口）。

    之前 UI 直接读模块私有的 `_PAUSE`，改名就会静默失效；
    这里收成一个稳定接口，UI 只认它。
    """
    return _PAUSE.is_set()


def is_speaking() -> bool:
    """当前是否正在发声（UI 用来决定按钮显示 ⏸ 还是 ▶）。"""
    return _PLAYING.is_set() or _PAUSE.is_set()


def stop_speaking() -> bool:
    """立即停止正在进行的朗读，并作废队列里还没轮到的待读。

    三通道一起掐：MCI stop（edge mp3 播放）/ winsound SND_PURGE（WAV 播放）/
    terminate（SAPI 直读的 PowerShell 进程）。返回停止前是否正在发声。
    """
    global _GEN
    with _GEN_LOCK:
        _GEN += 1                      # 队列里所有旧代任务作废
    was = _PLAYING.is_set()
    _STOP.set()
    if os.name == "nt":
        try:
            ctypes.windll.winmm.mciSendStringW(
                f"stop {_MCI_ALIAS}", None, 0, None)
        except Exception:
            pass
        try:
            import winsound
            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception:
            pass
    p = _PS_PROC
    if p is not None:
        try:
            p.terminate()
        except Exception:
            pass
    _vlog("stop_speaking: was_playing=%s gen=%d" % (was, _GEN))
    return was

# ---------- 语音诊断日志（解决"反复无声却查不到原因"：每次朗读都落盘真实结果） ----------
_VLOG_LOCK = threading.Lock()


def _vlog(msg: str):
    """把一次语音调用的关键事实追加到 %APPDATA%/PASMStudio/voice.log。

    内容含：引擎选择 / 网络探测 / 合成结果 / PowerShell 输出与错误 ——
    真机无声时读这个文件即可定位到底卡在哪一环，不用反复猜。
    """
    try:
        import os
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "PASMStudio")
        os.makedirs(d, exist_ok=True)
        with _VLOG_LOCK:
            with open(os.path.join(d, "voice.log"), "a", encoding="utf-8") as f:
                f.write(time.strftime("%m-%d %H:%M:%S") + " " + msg + "\n")
    except Exception:
        pass

# ---------- 性格 → 语气基调（rate/volume/停顿微调，SAPI 可真实生效） ----------
# 急脾气/活泼 = 连珠炮（语速快、停顿短）；温柔/沉稳 = 文静（语速慢、停顿长）
_PERSONA_SPEED = [
    ("调皮", 48, 8), ("活泼", 42, 10), ("外向", 40, 10), ("好奇", 36, 8),
    ("机灵", 24, 6), ("敏锐", 22, 6), ("温柔", -30, -6), ("内向", -26, -4),
    ("沉稳", -24, -4), ("温和", -20, -4), ("可靠", -16, -4), ("安静", -22, -4),
]

# 成长阶段(0幼儿 1童年 2少年 3青年) → SAPI (语速, 音量) 基准
_PROFILE = {0: (208, 90), 1: (194, 94), 2: (178, 97), 3: (160, 100)}

# ---------- 情绪 → 语气修饰（edge_rate%, edge_pitchHz, edge_volume%,
#                            sapi_rate增量, sapi_vol增量, pause增量） ----------
_EMOTE = {
    "happy":   (8,   6, "+0%",  10,  4, -0.03),
    "excited": (14, 12, "+6%",  16,  6, -0.05),
    "sad":     (-10, -6, "-10%", -16, -8,  0.06),
    "angry":   (12,  2, "+16%", 14,  8, -0.04),
    "sleepy":  (-16, -9, "-16%", -20, -8,  0.07),
}


def _tone(growth: int, persona: str = "", emote: str = "") -> tuple:
    """返回 (rate, volume, pause)。growth 基础 + 性格偏移 + 情绪偏移。"""
    rate, vol = _PROFILE.get(int(growth), _PROFILE[2])
    pause = 0.13
    for key, dr, dv in _PERSONA_SPEED:
        if key in (persona or ""):
            rate += dr
            vol += dv
            pause = max(0.03, 0.13 - dr * 0.002)
            break
    em = _EMOTE.get(emote or "")
    if em:
        rate += em[3]
        vol += em[4]
        pause = max(0.02, pause + em[5])
    return max(110, min(320, rate)), max(45, min(100, vol)), pause


def _clean_text(text: str) -> str:
    t = text or ""
    t = re.sub(r"```[\s\S]*?```", "（代码略）", t)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"[*_~#>|]{1,}", "", t)
    t = re.sub(r"[（(](?:备注|提示|系统|你|小U|小霖|它)[^）)]{0,20}[）)]", "。", t)
    t = re.sub(r"[（(][^）)]*[）)]", "", t)
    t = re.sub(r"[\U0001F300-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ，。、；：")
    return t[:900] or "嗯。"


# ================= SAPI 通道（PowerShell + System.Speech，零第三方依赖） =================
_PWSH = None
_PS_TPL = r"""
$ErrorActionPreference = 'SilentlyContinue'
$p = $args[0]
$txt = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.Rate = {RATE} } catch {}
try { $s.Volume = {VOL} } catch {}
$wantPref = '{WANT_PREF}'
$want = '{WANT}'
$sel = $null
foreach ($v in $s.GetInstalledVoices()) {
  $n = $v.VoiceInfo.Name
  Write-Output ("VOICE: " + $n + " | " + $v.VoiceInfo.Culture)
}
# 第一轮：优先匹配"语言/方言"声线（如粤语 zh-HK）；第二轮：再按性别挑普通话声线
if ($wantPref) {
  foreach ($v in $s.GetInstalledVoices()) {
    $n = $v.VoiceInfo.Name
    if ($n -match $wantPref) { try { $s.SelectVoice($n); $sel = $n; break } catch {} }
  }
}
if (-not $sel -and $want) {
  foreach ($v in $s.GetInstalledVoices()) {
    $n = $v.VoiceInfo.Name
    if ($n -match $want) { try { $s.SelectVoice($n); $sel = $n; break } catch {} }
  }
}
Write-Output ("USING: " + $(if ($sel) { $sel } else { $s.Voice.Name }))
Write-Output ("RATEVOL: " + $s.Rate + " / " + $s.Volume)
try { $s.Speak($txt); Write-Output "SPEAK_OK" }
catch { Write-Output ("SPEAK_ERR: " + $_.Exception.Message) }
$s.Dispose()
Remove-Item $p -ErrorAction SilentlyContinue
"""


def _pwsh() -> Optional[str]:
    global _PWSH
    if _PWSH is None:
        for cand in (r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
                     "powershell"):
            if os.path.isfile(cand) or cand == "powershell":
                _PWSH = cand
                break
        if _PWSH is None:
            _PWSH = ""
    return _PWSH or None


# v0.28.1：方言 → SAPI 声线优先匹配正则（系统装了对应语音包才生效；没装则自动落到普通话声线）
_SAPI_LANG_WANT = {
    "粤语": r"(?i)zh-HK|zh-MO|Cantonese|粤语|粵語|HiuGaai|WanLung|Tracy|Danny",
    "台湾腔": r"(?i)zh-TW|Taiwan|國語|国语|Yating|HanHan|Zhiwei",
    # v0.28.2：四川/河南/东北/山东 also route to their regional SAPI voice if installed
    "四川": r"(?i)zh-CN-sichuan|Sichuan|Yunxi",
    "河南": r"(?i)zh-CN-henan|Henan|Yundeng",
    "东北": r"(?i)zh-CN-liaoning|Liaoning|Xiaobei",
    "山东": r"(?i)zh-CN-shandong|Shandong|Yunxiang",
}


def _sapi_want(gender: str, lang: str = "") -> tuple:
    """返回 (方言优先正则, 性别兜底正则)。

    在线神经音断了、回落系统语音时，至少让**粤语回复用粤语声线读**
    （前提是系统装了 zh-HK 语音包；没有就老老实实用普通话声线，不装样子）。
    """
    want = {"female": "(?i)huihui|xiaoxiao|xiaoyi",
            "male": "(?i)kangkang|yunxi|yunjian|yunyang|huihui|zh-cn"}.get(
        gender or "none",
        "(?i)huihui|xiaoxiao|kangkang|yunxi|zh-cn|chinese")
    return (_SAPI_LANG_WANT.get(lang or "", ""), want)


def _sapi_direct(text: str, rate: int, vol: int, gender: str,
                 lang: str = "") -> bool:
    """System.Speech 直接朗读（离线）。返回是否成功。rate 为内部档位(110-320)。"""
    global _LAST_ERR
    ps = _pwsh()
    if ps is None:
        _LAST_ERR = "找不到 PowerShell"
        _vlog("SAPI fail: no powershell")
        return False
    clean = _clean_text(text)
    if not clean:
        _vlog("SAPI fail: empty text after clean")
        return False
    ps_rate = max(-10, min(10, round((rate - 160) / 10)))
    want_pref, want = _sapi_want(gender, lang)
    script = _PS_TPL.replace("{RATE}", str(ps_rate)).replace(
        "{VOL}", str(max(40, min(100, vol)))).replace(
        "{WANT_PREF}", want_pref).replace("{WANT}", want)
    tmp_txt = tmp_ps = None
    try:
        f1 = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        f1.write(clean)
        f1.close()
        tmp_txt = f1.name
        f2 = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                         encoding="utf-8")
        f2.write(script)
        f2.close()
        tmp_ps = f2.name
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}   # 无窗口
        t0 = time.time()
        p = subprocess.Popen([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                              "-File", tmp_ps, tmp_txt],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             **kw)
        globals()["_PS_PROC"] = p          # stop_speaking 可终止朗读进程
        try:
            out_b, err_b = p.communicate(timeout=120)
        finally:
            globals()["_PS_PROC"] = None
        r_rc = p.returncode
        out = (out_b or b"").decode("utf-8", errors="ignore")
        err = (err_b or b"").decode("utf-8", errors="ignore")
        ok = r_rc == 0 and "SPEAK_OK" in out
        if not ok:
            _LAST_ERR = ("系统语音(power-shell)发声失败 rc=%d；多为缺少中文语音包"
                         % r_rc)
        # 诊断：声线列表 / 实际选用声线 / 错误信息 全部落盘，真机无声一眼定位
        _vlog("SAPI rc=%d ok=%s %.1fs rate=%d vol=%d want=%r\n  OUT: %s\n  ERR: %s"
              % (r_rc, ok, time.time() - t0, rate, vol, want,
                 " | ".join(x.strip() for x in out.splitlines()[:14]),
                 (err or "").strip()[:400]))
        return ok
    except Exception as ex:
        _vlog("SAPI exception: %r" % (ex,))
        return False
    finally:
        for p in (tmp_txt, tmp_ps):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


_WAV_TPL = r"""
$ErrorActionPreference = 'SilentlyContinue'
$p = $args[0]
$out = $args[1]
$txt = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
try { $s.Rate = {RATE} } catch {}
try { $s.Volume = {VOL} } catch {}
$wantPref = '{WANT_PREF}'
$want = '{WANT}'
$sel = $null
# 第一轮：优先匹配"语言/方言"声线（如粤语 zh-HK）；第二轮：再按性别挑普通话声线
if ($wantPref) {
  foreach ($v in $s.GetInstalledVoices()) {
    $n = $v.VoiceInfo.Name
    if ($n -match $wantPref) { try { $s.SelectVoice($n); $sel = $n; break } catch {} }
  }
}
if (-not $sel -and $want) {
  foreach ($v in $s.GetInstalledVoices()) {
    $n = $v.VoiceInfo.Name
    if ($n -match $want) { try { $s.SelectVoice($n); $sel = $n; break } catch {} }
  }
}
Write-Output ("USING: " + $(if ($sel) { $sel } else { $s.Voice.Name }))
try { $s.SetOutputToWaveFile($out); $s.Speak($txt); $s.SetOutputToNull(); Write-Output "WAV_OK" }
catch { Write-Output ("WAV_ERR: " + $_.Exception.Message) }
$s.Dispose()
Remove-Item $p -ErrorAction SilentlyContinue
"""


def _sapi_wav_speak(text: str, rate: int, vol: int, gender: str,
                    lang: str = "") -> bool:
    """SAPI 兜底（可靠通道）：合成成 WAV 文件后，用 winsound 播放。

    与「系统提示音」走完全相同的 Windows 音频通道（用户已确认可听），
    避免 PowerShell 进程内直接发声在某些机器/被占用时的"说完了却没声音"。
    """
    ps = _pwsh()
    if ps is None:
        _vlog("SAPI-WAV fail: no powershell")
        return False
    clean = _clean_text(text)
    if not clean:
        return False
    ps_rate = max(-10, min(10, round((rate - 160) / 10)))
    want_pref, want = _sapi_want(gender, lang)
    script = _WAV_TPL.replace("{RATE}", str(ps_rate)).replace(
        "{VOL}", str(max(40, min(100, vol)))).replace(
        "{WANT_PREF}", want_pref).replace("{WANT}", want)
    tmp_txt = tmp_ps = tmp_wav = None
    try:
        f1 = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        f1.write(clean)
        f1.close()
        tmp_txt = f1.name
        f2 = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                         encoding="utf-8")
        f2.write(script)
        f2.close()
        tmp_ps = f2.name
        f3 = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        f3.close()
        tmp_wav = f3.name
        kw = {"creationflags": 0x08000000} if os.name == "nt" else {}
        t0 = time.time()
        r = subprocess.run([ps, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", tmp_ps, tmp_txt, tmp_wav],
                           capture_output=True, timeout=120, **kw)
        out = (r.stdout or b"").decode("utf-8", errors="ignore")
        err = (r.stderr or b"").decode("utf-8", errors="ignore")
        syn_ok = r.returncode == 0 and "WAV_OK" in out and \
            os.path.exists(tmp_wav) and os.path.getsize(tmp_wav) > 1000
        if not syn_ok:
            _LAST_ERR = ("系统语音合成失败 rc=%d；多为缺少中文语音包（设置→时间和语言→"
                         "语音→安装）" % r.returncode)
            _vlog("SAPI-WAV synth fail rc=%d ok=%s\n  OUT: %s\n  ERR: %s"
                  % (r.returncode, syn_ok,
                     " | ".join(x.strip() for x in out.splitlines()[:8]),
                     (err or "").strip()[:300]))
            return False
        # v0.27.4：优先用 MCI 播放 WAV —— 与 edge 通道同一条可控通道，
        #   暂停/继续/停止都生效（旧版走 winsound：能出声但无法暂停）。
        #   MCI 打不开（少数机器）再回退 winsound 保证"至少有声音"。
        if _STOP.is_set():
            _vlog("SAPI-WAV skipped: stop requested before play")
            return False
        played = _mci_play(tmp_wav)
        if not played and not _STOP.is_set():
            import winsound
            _vlog("SAPI-WAV MCI 打不开 → winsound 回退（不可暂停）")
            _PLAYING.set()
            try:
                winsound.PlaySound(tmp_wav, winsound.SND_FILENAME)   # 同步播放
            finally:
                _PLAYING.clear()
            played = not _STOP.is_set()
        stopped = _STOP.is_set()
        _vlog("SAPI-WAV %s %.1fs len=%dB rate=%d vol=%d want=%r"
              % ("stopped" if stopped else "played ok", time.time() - t0,
                 os.path.getsize(tmp_wav), rate, vol, want))
        return not stopped
    except Exception as ex:
        _vlog("SAPI-WAV exception: %r" % (ex,))
        return False
    finally:
        for p in (tmp_txt, tmp_ps, tmp_wav):
            if p and os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass


def _sapi_speak(text: str, rate: int, vol: int, gender: str,
                lang: str = "") -> bool:
    """System.Speech 朗读总入口。

    v0.27.4：改为 **先合成 WAV 再用 MCI 播放**——这条通道可暂停/继续/停止。
    （旧版首选 PowerShell 进程内直接发声：阻塞且无法暂停，真机表现为
      "点了暂停没反应 / 不能续播"。）WAV 通道失败再回退直接发声，保证至少有声音。
    v0.28.1：新增 lang —— 在线音断了时，让方言回复尽量用对应方言的系统声线。
    """
    if _sapi_wav_speak(text, rate, vol, gender, lang):
        return True
    _vlog("SAPI WAV+MCI failed → try PowerShell direct fallback")
    return _sapi_direct(text, rate, vol, gender, lang)


# ================ edge-tts 通道（在线神经音：奶声/少年/青年×男女×性格语速×情绪） ================
def _edge_ok() -> bool:
    global _EDGE_STATE
    if _EDGE_STATE != "untried":
        return _EDGE_STATE == "ok"
    try:
        import edge_tts  # noqa: F401
        _EDGE_STATE = "ok"
    except Exception:
        _EDGE_STATE = "off"
    return _EDGE_STATE == "ok"


_NET_OK = None            # 最近一次 edge 服务可达性探测结果（None=未探）
_NET_AT = 0.0


def _svc_probe(timeout: float = 4.0) -> bool:
    """**服务级**探测：真做一次 TLS 握手 + HTTP 请求，只取状态码。

    为什么必须升级（v0.28.1）：旧版只做 TCP 连通性探测，真机事故里
    "TCP 能连（probe=True）但合成句句失败"——预检通过反而制造假信心，
    日志上还看不到任何原因。HTTP 4xx 说明服务本身活着（是我们的请求被拒），
    仍然算"可达"；只有连不上/TLS 失败/5xx 才算服务不可用。
    """
    global _LAST_EDGE_ERR
    try:
        import http.client
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            ctx = ssl.create_default_context()
        c = http.client.HTTPSConnection("speech.platform.bing.com", 443,
                                        timeout=timeout, context=ctx)
        try:
            c.request("GET", "/")
            r = c.getresponse()
            code = int(getattr(r, "status", 0) or 0)
            try:
                r.read(64)
            except Exception:
                pass
            return code < 500
        finally:
            try:
                c.close()
            except Exception:
                pass
    except Exception as ex:
        _LAST_EDGE_ERR = "%s: %s @服务预检" % (type(ex).__name__, str(ex)[:200] or "-")
        return False


def _net_ok(timeout: float = 4.0) -> bool:
    """edge 服务是否真的可用的秒级预检（线程内探测，绝不让主链路卡在 DNS/TLS 上）。

    - 可达结果缓存 5 分钟；不可达缓存 60 秒后允许再试（网络恢复能自动感知）。
    - 关键：DNS/TLS 即使要挂，也只会挂在后台探测线程里——我们 4~5 秒内就
      拿到结论并立刻回落 SAPI 兜底，用户不会"点了没声音还没提示"。
    """
    global _NET_OK, _NET_AT
    now = time.time()
    if _NET_OK is not None and now - _NET_AT < (300 if _NET_OK else 60):
        return _NET_OK
    ok = [False]
    done = threading.Event()

    def _probe():
        try:
            ok[0] = _svc_probe(timeout)
        except Exception:
            ok[0] = False
        finally:
            done.set()

    t = threading.Thread(target=_probe, daemon=True)
    t.start()
    finished = done.wait(timeout + 1.2)
    r = bool(finished and ok[0])
    _NET_OK, _NET_AT = r, now
    _vlog("edge 服务预检: %s (%.1fs)%s" % (
        r, time.time() - now,
        ("  原因=" + _LAST_EDGE_ERR) if (not r and _LAST_EDGE_ERR) else ""))
    return r


# (性别, 成长阶段0-3) → (声线, 基准pitchHz)。幼儿/童年用真童声，奶声奶气。
# v0.29.1 声线表（2026-09-14 对照服务端 list_voices 全量校验后重订）
#
# 【为什么改了】微软会**静默下架**神经声线 —— 一旦选中的声线消失，edge 合成会
# 返回**空音频**（真机实测报 NoAudioReceived），App 只能回落到系统 SAPI 声线。
# 表现就是"声音突然变了 / 中英混排念得乱七八糟"，而且**不报任何错**，极难排查。
# 本次实测已下架：zh-CN-XiaoshuangNeural（女童）、zh-CN-XiaoyouNeural（男童）——
# 恰好覆盖了幼儿/童年两档，而真机正处在童年档 → 每次合成必失败（voice.log 连刷）。
# 现改为"存活声线 + 音高塑形"：用年轻声线配高 pitch 做出童声感，不再依赖已下架声线。
_EDGE_VOICE = {
    ("female", 0): ("zh-CN-XiaoyiNeural", 45),       # 年轻女声 + 高音 = 奶声
    ("female", 1): ("zh-CN-XiaoyiNeural", 24),       # 年轻女声 + 偏高 = 童声
    ("female", 2): ("zh-CN-XiaoyiNeural", 12),       # 年轻女声：少年感
    ("female", 3): ("zh-CN-XiaoxiaoNeural", 0),      # 温柔女声：青年
    ("female", 4): ("zh-CN-XiaoxiaoNeural", -14),    # 成年女声：更沉稳
    ("male", 0): ("zh-CN-YunxiaNeural", 42),         # 少年男声 + 高音 = 童声
    ("male", 1): ("zh-CN-YunxiNeural", 28),          # 少年男声
    ("male", 2): ("zh-CN-YunxiNeural", 10),
    ("male", 3): ("zh-CN-YunjianNeural", -6),        # 青年男声
    ("male", 4): ("zh-CN-YunjianNeural", -22),       # 成年男声：更低沉
    ("none", 0): ("zh-CN-XiaoyiNeural", 40),
    ("none", 1): ("zh-CN-XiaoyiNeural", 16),
    ("none", 2): ("zh-CN-XiaoxiaoNeural", 6),
    ("none", 3): ("zh-CN-XiaoxiaoNeural", -2),
    ("none", 4): ("zh-CN-XiaoxiaoNeural", -16),      # 中性成年声线
}

# 各性别在"原声线失效"时的安全替身（都取自当前存活声线）
_EDGE_FALLBACK = {"female": "zh-CN-XiaoxiaoNeural",
                  "male": "zh-CN-YunxiNeural",
                  "none": "zh-CN-XiaoxiaoNeural"}
_EDGE_BAD = set()                       # 已确认返回空音频的声线（运行时自愈）
_EDGE_KNOWN = {"names": None, "ts": 0.0, "asked": False}


def _edge_warm_voices():
    """后台线程拉一次"服务端当前可用声线"，缓存 1 小时（不阻塞发声）。"""
    def job():
        try:
            import asyncio
            import edge_tts
            names = {v["ShortName"] for v in asyncio.run(edge_tts.list_voices())}
            if names:
                _EDGE_KNOWN["names"] = names
                _EDGE_KNOWN["ts"] = time.time() + 3600.0
                _vlog("声线表已刷新：服务端现有 %d 条" % len(names))
        except Exception as ex:
            _vlog("声线表刷新失败（忽略）: %s" % str(ex)[:80])

    try:
        threading.Thread(target=job, name="pasm-edge-voices", daemon=True).start()
    except Exception:
        pass


def _edge_safe_voice(voice: str, gender: str = "none") -> str:
    """声线守卫：失效声线 → 换成当前可用的同性别声线（**永不静默变哑**）。

    两道判据：① 运行时黑名单（合成返回空音频的声线）；
              ② 服务端声线表（缓存，微软下架后能提前发现）。
    都拿不到时**照原样返回**，绝不因为守卫本身的问题把声音弄没。
    """
    if not voice:
        voice = ""
    if voice and voice in _EDGE_BAD:
        _vlog("声线 %s 在失效名单里 → 换替身" % voice)
        voice = ""
    names = _EDGE_KNOWN["names"] if _EDGE_KNOWN["ts"] > time.time() else None
    if names is None and not _EDGE_KNOWN["asked"]:
        _EDGE_KNOWN["asked"] = True
        _edge_warm_voices()
    if names and voice and voice not in names:
        _vlog("声线 %s 已不在服务端列表 → 换替身" % voice)
        voice = ""
    if voice:
        return voice
    g = gender if gender in ("male", "female") else "none"
    fb = _EDGE_FALLBACK[g]
    if names and fb not in names:
        cand = sorted(n for n in names if n.startswith("zh-CN-"))
        if cand:
            fb = cand[0]
    return fb


def _edge_cfg(growth: int, gender: str, persona: str = "",
              emote: str = "", voice_override: str = "",
              rate_bias: int = 0, pitch_bias: int = 0) -> tuple:
    """edge-tts 参数：返回 (voice, rate_pct, pitch_hz, volume_pct)。

    v0.27.3：
      · voice_override —— 学到的方言口音优先（如东北→辽宁话音）；
      · rate_bias / pitch_bias —— 性格话术规格给的语速/音高微调。
    """
    try:
        g = max(0, min(4, int(growth)))   # 5 档成长（0~4），成年=4
    except Exception:
        g = 2
    gen = gender or "none"
    if gen not in ("male", "female", "none"):
        gen = "none"
    voice, pitch = _EDGE_VOICE[(gen, g)]
    if voice_override:
        voice = voice_override          # 方言口音（学到用户口音后）
    pitch += int(pitch_bias or 0)
    # 性格 → 语速基调（急性子连珠炮 +，文静 -）＋成长基准
    rate = 6 if g == 0 else 10 if g == 1 else 4 if g == 2 else 0
    rate += int(rate_bias or 0)
    for key, _dr, _dv in _PERSONA_SPEED:
        if key in (persona or ""):
            rate += 14 if _dr > 20 else -9 if _dr < -12 else 0
            pitch += 3 if ("温柔" in key or "温和" in key) else 0
            break
    volume = "+0%"
    em = _EMOTE.get(emote or "")
    if em:
        rate += em[0]
        pitch += em[1]
        volume = em[2]
    pitch = max(-60, min(60, pitch))
    rate = max(-30, min(55, rate))
    # v0.29.1：出口统一过声线守卫 —— 方言口音/成长档选到的声线若已被下架，
    # 在这里换成可用替身，避免整条 edge 通路静默失败后掉到系统声线。
    voice = _edge_safe_voice(voice, gen)
    return voice, f"{rate:+d}%", f"{pitch:+d}Hz", volume


def _edge_split(text: str, limit: int = 90) -> list:
    """把长文本切成"句块"：按 。！？；!?;\n 断句，凑到 ~limit 字一块。

    让 edge 逐块合成播放——第一块几秒内出声，长文不再"整段合成完才开播"
    （v0.22.4 修复朗读/点小人语音延时）。
    """
    if not text:
        return []
    text = _clean_text(text)
    if len(text) <= limit:
        return [text]
    raw = re.split(r"(?<=[。！？!?；;])", text)
    chunks, cur = [], ""
    for seg in raw:
        seg = seg.strip()
        if not seg:
            continue
        if cur and len(cur) + len(seg) > limit:
            chunks.append(cur)
            cur = seg
        else:
            cur += seg
    if cur:
        chunks.append(cur)
    out = [c for c in chunks if c.strip()]
    return out or [text]


def _edge_synth(text: str, voice: str, rate: str, pitch: str, volume: str) -> str:
    """合成一段 mp3 到临时文件，返回路径；失败返回空串（含 30s 超时）。

    v0.28.1：失败时把**真实异常**（类型 + 消息 + 末尾栈帧）写进 _LAST_EDGE_ERR
    并落盘 voice.log。旧版只留一句 "edge synth fail"，排查时只能靠猜。
    """
    global _LAST_EDGE_ERR
    import asyncio
    import edge_tts

    async def _run():
        c = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch, volume=volume)
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            fname = f.name
        agen = c.stream()
        try:
            async with asyncio.timeout(30):
                with open(fname, "wb") as f:
                    async for chunk in agen:
                        data = chunk.get("audio") or chunk.get("data")
                        if isinstance(data, (bytes, bytearray)):
                            f.write(data)
        finally:
            try:
                await agen.aclose()
            except Exception:
                pass
        if os.path.getsize(fname) > 0:
            return fname
        # 服务端对这个声线**一个字节都没返回** → 判定该声线已失效并记入黑名单，
        # 下一句自动换替身。这是"微软静默下架声线"唯一可靠的运行时信号。
        if voice:
            _EDGE_BAD.add(voice)
            _vlog("声线 %s 返回空音频 → 记入失效名单，下次改用替身" % voice)
        return ""

    try:
        return asyncio.run(_run())
    except BaseException as ex:          # noqa: BLE001 —— 必须连 CancelledError/超时都记下来
        import traceback
        tb = traceback.extract_tb(getattr(ex, "__traceback__", None) or [])
        where = ("%s:%d" % (os.path.basename(tb[-1].filename), tb[-1].lineno)) if tb else "?"
        _LAST_EDGE_ERR = "%s: %s @%s" % (type(ex).__name__, str(ex)[:240] or "-", where)
        return ""


def _edge_speak(text: str, voice: str, rate: str, pitch: str, volume: str) -> bool:
    """边合成边播（生产-消费：合成线程连续出块，播放线程逐块播）。

    先做可达性预检；随后把长文本切成句块：
    - 第一块最快出声（点小人/短句几乎无感）；
    - 合成线程与播放并行 → 块与块之间没有静音等待；
    - 每块播放都走 _mci_play 轮询，点「朗读:关」≤200ms 掐断并丢弃后续队列。
    """
    if not _edge_ok() or not _net_ok():
        _vlog("edge skip: module_ok=%s net_ok=%s" % (_edge_ok(), _NET_OK))
        return False
    t0 = time.time()
    chunks = _edge_split(text)
    if not chunks:
        return False

    def _forget(p):
        if p and os.path.exists(p):
            try:
                os.remove(p)
            except Exception:
                pass

    played_any = False
    try:
        import queue as _queue
        q = _queue.Queue(maxsize=2)

        def _producer():
            global _EDGE_FAILS
            for k, ch in enumerate(chunks):
                if _STOP.is_set():
                    break
                _t1 = time.time()
                f = _edge_synth(ch, voice, rate, pitch, volume)
                # v0.28.1：服务侧偶发 403 / 连接被掐时，隔 0.6s 再来一次基本就过
                # （旧版一次不成整句放弃 → 直接掉到机械音，用户体感"朗读坏了"）。
                # 但只在"快速失败"时重试——真正卡网络（>5s）就不重试了，
                # 否则会让用户白等两倍时间。
                if not f and not _STOP.is_set() and (time.time() - _t1) < 5.0:
                    time.sleep(0.6)
                    if not _STOP.is_set():
                        f = _edge_synth(ch, voice, rate, pitch, volume)
                if not f:
                    _EDGE_FAILS += 1
                    _vlog("edge synth fail chunk %d/%d %.1fs  真实原因=%s"
                          % (k, len(chunks), time.time() - t0,
                             _LAST_EDGE_ERR or "未知"))
                    break
                _EDGE_FAILS = 0
                if _STOP.is_set():               # 合成期间被停：不留临时文件、不入队
                    _forget(f)
                    break
                q.put(f)
            q.put(None)                          # 结束哨兵

        threading.Thread(target=_producer, daemon=True).start()
        n_played = 0
        while True:
            if _STOP.is_set():
                break
            f = q.get()                        # 等下一块（生产者已提前合成）
            if f is None:
                break
            ok = _mci_play(f)
            _forget(f)
            if not ok:
                break
            n_played += 1
            played_any = True
        # 停止时清空队列里没播的
        while True:
            try:
                _forget(q.get_nowait())
            except Exception:
                break
        _vlog("edge synth+play: ok=%s chunks=%d/%d %.1fs" %
              (played_any and not _STOP.is_set(), n_played, len(chunks),
               time.time() - t0))
        return played_any and not _STOP.is_set()
    except Exception as ex:
        _vlog("edge exception: %r %.1fs" % (ex, time.time() - t0))
        return played_any


def _mci_play(path: str) -> bool:
    """播放 mp3（MCI winmm）。v0.22.1 改为「异步起播 + 轮询模式」：

    旧实现用 'play … wait' 阻塞线程等播完，stop_speaking 跨线程下发的
    'stop pasm_voice' 在 wait 语义下偶尔不生效（真机实证：关朗读后
    40s 长句仍继续播完）。现在由本函数每 100ms 检查 _STOP 与播放状态，
    点「朗读:关」≤200ms 内必定掐断并返回 False，由上层安静收工。
    """
    if _STOP.is_set():
        _vlog("mci skip: stop requested before play")
        return False
    try:
        w = ctypes.windll.winmm.mciSendStringW
        # v0.27.4：支持 WAV（SAPI 合成产物）——此前只认 mpegvideo，
        #   导致 SAPI 通道走 winsound（无法暂停/继续）。现在两条通道统一走 MCI，
        #   暂停/继续/停止对 edge 与 SAPI 一视同仁。
        _typ = "waveaudio" if str(path).lower().endswith(".wav") else "mpegvideo"
        r1 = w(f'open "{path}" type {_typ} alias {_MCI_ALIAS}', None, 0, None)
        if r1 != 0:
            return False
        _PLAYING.set()
        try:
            if _STOP.is_set():
                return False
            w(f"play {_MCI_ALIAS}", None, 0, None)   # 异步起播，不用 wait
            buf = ctypes.create_unicode_buffer(64)
            while not _STOP.is_set():
                time.sleep(0.1)
                # v0.27.3：暂停支持。被 pause 时下发 MCI pause 并原地等待，
                #   resume 后继续播——不把它当成"播完"而提前收工。
                if _PAUSE.is_set():
                    try:
                        w(f"pause {_MCI_ALIAS}", None, 0, None)
                    except Exception:
                        pass
                    while _PAUSE.is_set() and not _STOP.is_set():
                        time.sleep(0.1)
                    if _STOP.is_set():
                        break
                    try:
                        w(f"resume {_MCI_ALIAS}", None, 0, None)
                    except Exception:
                        pass
                    continue
                try:
                    w(f"status {_MCI_ALIAS} mode", buf, len(buf), None)
                except Exception:
                    break
                if buf.value.strip().lower() == "paused":
                    continue                 # 暂停中：不算播完
                if buf.value.strip().lower() != "playing":
                    break                    # 自然播完
            if _STOP.is_set():
                try:
                    w(f"stop {_MCI_ALIAS}", None, 0, None)   # 立即掐断
                except Exception:
                    pass
                return False
            return True
        finally:
            _PLAYING.clear()
            try:
                w(f"close {_MCI_ALIAS}", None, 0, None)
            except Exception:
                pass
    except Exception:
        return False


# ================= 统一入口 =================
def speak_text(text: str,
               on_result: Optional[Callable[[str], None]] = None,
               log: Optional[Callable[[str], None]] = None,
               growth: int = 2,
               gender: str = "none",
               persona: str = "",
               emote: str = "",
               engine: str = "auto",
               voice_override: str = "",
               rate_bias: int = 0,
               pitch_bias: int = 0,
               lang: str = "") -> threading.Thread:
    """异步朗读（全局串行）。engine: auto(edge可用则用) / edge / sapi。

    语气随参数自动变：
    - growth 年龄：0幼儿→真童声奶声奶气，1童年→童声，2少年→少年音，3青年→成人声线
    - gender 声线：male/female 各挑对应男/女声
    - persona 节奏：活泼调皮=连珠炮，温柔沉稳=文静
    - emote 情绪：happy/excited/sad/angry/sleepy 会抬高/压低语速音高、调音量
    """
    phrase = _clean_text(text)
    with _GEN_LOCK:                    # 捕获当前代际：提交前被 stop 过则作废
        gen = _GEN
    _STOP.clear()                      # 新朗读开始：允许发声（不影响旧代作废）
    _PAUSE.clear()                     # v0.27.3：新一句开读，清掉上一句的暂停态

    def _job():
        global _EDGE_COOLDOWN_UNTIL
        if gen != _GEN:                # 排队期间被 stop：静默丢弃，不再出声
            _vlog("speak_text dropped: stale gen %d != %d %r"
                  % (gen, _GEN, phrase[:20]))
            return
        msg = "ok"
        t0 = time.time()
        with _LOCK:
            if gen != _GEN:            # 排队期间被 stop：拿到锁也要作废
                _vlog("speak_text dropped: stale gen %d != %d %r"
                      % (gen, _GEN, phrase[:20]))
                return
            used = False
            # v0.17.2：发声前先做一次系统音频自愈（默认端点取消静音/音量拉回），
            # 避免"应用在播但系统输出端被静音/音量过低"造成的听不见
            try:
                import audio as _audio_mod
                _audio_mod.ensure_audible()
            except Exception:
                pass
            if engine != "sapi" and _edge_ok() and \
                    time.time() >= _EDGE_COOLDOWN_UNTIL:
                voice, rate, pitch, volume = _edge_cfg(
                    growth, gender, persona, emote,
                    voice_override=voice_override,
                    rate_bias=rate_bias, pitch_bias=pitch_bias)
                if _edge_speak(phrase, voice, rate, pitch, volume):
                    used = True
                    _EDGE_COOLDOWN_UNTIL = 0.0     # 成功 → 清冷却
                elif _STOP.is_set():
                    used = True        # 被主动停止：不回落、不进冷却，安静收工
                    msg = "ok"         # 用户主动停止不算朗读失败
                else:
                    _EDGE_COOLDOWN_UNTIL = time.time() + 90   # 网络不稳：90s 内不再傻试
                    # v0.28.1：把**真实原因**带出来（旧版只说"不可用"，用户和我们都只能猜）
                    if log:
                        log("在线语音(edge-tts)连不上，本轮已回落系统语音，90s 后自动重试；"
                            "原因：" + (_LAST_EDGE_ERR or "未知"))
            if not used and not _STOP.is_set():
                # 离线兜底：PowerShell System.Speech（系统自带，打包零依赖）
                rate, vol, _pause = _tone(growth, persona, emote)
                if not _sapi_speak(phrase, rate, vol, gender, lang):
                    msg = "系统语音不可用：" + (_LAST_ERR or "PowerShell/中文语音包缺失")
                    if log:
                        log(msg)
        _vlog("speak_text done: engine_used=%s msg=%s growth=%d gender=%s %.1fs text=%r"
              % ("edge" if used else "sapi", msg, growth, gender,
                 time.time() - t0, phrase[:24]))
        if on_result:
            try:
                on_result(msg)
            except Exception:
                pass

    t = threading.Thread(target=_job, daemon=True)
    t.start()
    return t


def has_zh_voice() -> bool:
    """是否可能发声：有 PowerShell 即视为具备（真实语音由 System.Speech 探测）。"""
    return _pwsh() is not None


def system_sound_test() -> tuple:
    """播放系统提示音，返回 (是否成功, 诊断文本)。

    播放前先做默认端点自愈（取消静音/音量拉回），诊断文本可原样展示给用户，
    帮用户看清"输出到了哪个设备、音量多少、是否被静音"。
    """
    diag = ""
    try:
        import audio as _audio_mod
        diag = _audio_mod.heal_and_diagnose()
    except Exception as ex:
        diag = "音频诊断异常：" + str(ex)
    ok = play_system_sound()
    _vlog("system sound test: ok=%s diag=%s"
          % (ok, diag.replace("\n", " | ")[:200]))
    return ok, diag


def play_system_sound() -> bool:
    """播放 Windows 系统提示音（不依赖任何 TTS 声线）。

    用于"无声到底是谁的问题"自检：若系统音也听不到 → 扬声器/音量/驱动问题，
    与应用无关；系统音正常而朗读无声 → 看 voice.log 定位 TTS 环节。

    v0.17.1：改纯 Python winsound 直调系统 API。此前走 PowerShell
    Add-Type System.Media，真机 voice.log 证实该脚本在 try 内加载程序集失败
    被 catch 误报 PLAY_ERR（"系统音测试错误"），与扬声器无关——现已绕开。
    """
    import winsound
    steps = []
    # 1) MessageBeep(MB_ICONASTERISK)：最底层提示音，同步短音
    try:
        winsound.MessageBeep(0x00000040)
        steps.append("MessageBeep")
    except Exception as ex1:
        # 2) 显式播放 Windows Notify.wav（异步）
        try:
            wav = os.path.join(os.environ.get("windir", r"C:\Windows"),
                               "Media", "Windows Notify.wav")
            if os.path.isfile(wav):
                winsound.PlaySound(wav, winsound.SND_FILENAME
                                   | winsound.SND_ASYNC)
                steps.append("PlaySound:wav")
        except Exception as ex2:
            # 3) 系统通知别名兜底
            try:
                winsound.PlaySound("SystemNotification",
                                   winsound.SND_ALIAS | winsound.SND_ASYNC
                                   | winsound.SND_NODEFAULT)
                steps.append("PlaySound:alias")
            except Exception as ex3:
                _vlog("system sound all fail: beep=%r wav=%r alias=%r"
                      % (ex1, ex2, ex3))
    _vlog("system sound test: channels=%s"
          % ("+".join(steps) if steps else "ALL_FAIL"))
    return bool(steps)


def engine_desc() -> str:
    """设置/状态里可读的说明。"""
    if _edge_ok():
        return "拟真语音(edge-tts 在线，含童声/男女声/情绪) + 离线回落"
    return "系统语音(离线 SAPI)"


def _sapi_voice_list() -> list:
    """列出系统已安装的 SAPI 声线（'名字|语言'）。探测失败返回 []。"""
    ps = _pwsh()
    if ps is None:
        return []
    script = ("Add-Type -AssemblyName System.Speech;"
              "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
              "$s.GetInstalledVoices()|ForEach-Object{"
              "$_.VoiceInfo.Name+'|'+$_.VoiceInfo.Culture.Name};"
              "$s.Dispose()")
    try:
        r = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
             "-Command", script],
            capture_output=True, timeout=30,
            creationflags=0x08000000 if os.name == "nt" else 0)
        out = (r.stdout or b"").decode("utf-8", "ignore")
        return [ln.strip() for ln in out.splitlines()
                if "|" in ln and not ln.strip().startswith("#")]
    except Exception:
        return []


def tts_diag() -> str:
    """v0.27.4：朗读通道自检——一次说清"在线语音"与"系统语音"各自能不能用。

    真机痛点：朗读失败只给一句"系统语音不可用（PowerShell/语音包缺失）"，
    用户分不清到底是没网、没装语音包，还是别的问题。
    """
    lines = ["🔊 **朗读（TTS）自检**"]
    try:
        e = _edge_ok()
    except Exception:
        e = False
    if e:
        lines.append("✅ 在线拟真语音（edge-tts）：可用 —— 音色/情绪最丰富，优先使用")
    else:
        lines.append("⚠️ 在线拟真语音（edge-tts）：不可用 —— "
                     + ("当前连不上网（离线时自动改用系统语音）" if not _net_ok()
                        else "联网正常但语音服务连不上（可能被网络策略拦截）"))
    voices = _sapi_voice_list()
    zh = [v for v in voices if "zh" in v.lower()]
    if not voices:
        lines.append("❌ 系统语音：探测不到任何声线（PowerShell 调不动，或系统未装语音组件）")
    elif not zh:
        lines.append("⚠️ 系统语音：有声线但**没有中文**（%s）\n"
                     "   装中文语音包：设置→时间和语言→语言→中文(简体)→语言选项→语音"
                     % "、".join(v.split("|")[0] for v in voices[:3]))
    else:
        lines.append("✅ 系统语音：有中文声线 —— "
                     + "、".join(v.split("|")[0] for v in zh[:3]))
    if _LAST_ERR:
        lines.append("ℹ️ 最近一次失败的具体原因：" + _LAST_ERR)
    if _edge_ok() or zh:
        lines.append("👉 结论：朗读通道可用。若仍听不到，先查系统音量/默认播放设备，"
                     "再看 %APPDATA%\\PASMStudio\\voice.log。")
    else:
        lines.append("👉 结论：两条通道都不可用。装中文语音包"
                     "（设置→时间和语言→语音）后重启本应用，或联网后重试在线语音。")
    return "\n".join(lines)
