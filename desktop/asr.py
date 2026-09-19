"""Windows SAPI 语音识别（中文）—— 语音输入用。v0.27.8 常驻热进程版。

---- 真机问题（v0.27.x 及以前）----
用户点 🎤 听到「叮」之后就"没有反应、听不到我说话"。实测定位到两处硬伤：

  1. 「叮」由 Python 在**启动识别进程之前**响（winsound.Beep），而
     PowerShell 冷启动 + 加载 System.Speech 程序集实测要 **1.9 秒**。
     用户听到叮立刻开口 → 那 1~2 秒的内容**完全没有被录到**，
     短指令（"帮我写个东西"）基本整句丢失 → 识别为空。
  2. 识别用 RecognizeAsync + 轮询判断，识别为空还**静默重试一次**：
     实测 listen_once(3s) 要 **11 秒**才返回，用户等不到任何反馈就走了。

---- v0.28 修法 ----
  · **常驻热进程**：程序启动时后台把 PowerShell + 语音引擎预热好，
    之后每次识别只需 ~0.2s 就进入聆听（不再有 2 秒空窗）。
  · **「叮」由引擎侧发号**：脚本在真正进入聆听（接上麦克风并 `RecognizeAsync`）
    之前输出 LISTENING，Python 收到才响叮 —— 听到叮 = 麦克风确实已经在听，一秒不浪费。
  · **异步 RecognizeAsync + 事件**：用 `SpeechRecognized` 事件抓最终文本（拿到即停），
    不再用同步 `Recognize(TimeSpan)`（本机会挂起）。静音场景靠「硬超时 → Cancel」收尾。
  · 空闲时 `SetInputToNull()` 松开麦克风，只在聆听那一刻占用，不霸占设备。
  · 支持 `PREFER <culture>`：跟随用户语言切换识别器（粤语 zh-HK 等）。

对外 API（保持兼容）：
  listen_once(timeout, with_diag, on_listening) -> 文本 | (文本, 诊断)
  listen_async(on_done, timeout, ding, on_state, lang_hint) -> Thread
  has_recognizer() -> bool
  diagnose() -> str
  warm_up() / shutdown() / prefer_culture() / service_info()
"""
from __future__ import annotations

import atexit
import os
import queue
import subprocess
import tempfile
import threading
import time
from typing import Callable, Optional

# ---------------------------------------------------------------- PS 脚本
_PS_SVC = r"""
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function New-Engine($cul) {
  $e = New-Object System.Speech.Recognition.SpeechRecognitionEngine($cul)
  $e.BabbleTimeout = [TimeSpan]::FromSeconds(0)
  $e.EndSilenceTimeout = [TimeSpan]::FromSeconds(1.6)
  $e.InitialSilenceTimeout = [TimeSpan]::FromSeconds(8)
  $g = New-Object System.Speech.Recognition.DictationGrammar
  $e.LoadGrammar($g)
  return $e
}

try {
  Add-Type -AssemblyName System.Speech
} catch {
  [Console]::Out.WriteLine('READY_ERR 加载 System.Speech 失败：' + $_.Exception.Message)
  [Console]::Out.Flush(); exit
}

try {
  $recs = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()
} catch {
  [Console]::Out.WriteLine('READY_ERR 列举识别器失败：' + $_.Exception.Message)
  [Console]::Out.Flush(); exit
}

if ($recs.Count -eq 0) {
  [Console]::Out.WriteLine('READY_ERR 系统里没有任何语音识别器：设置 → 时间和语言 → 语音 → 安装「语音识别」')
  [Console]::Out.Flush(); exit
}

# v0.28.4：默认识别器**强制优先 zh-CN**——它覆盖普通话与所有官话方言
# （四川/河南/北京/云南/东北/山东），是绝大多数用户的正确默认。
# 旧逻辑"取第一个 zh*"在装了粤语/台湾语音包时可能误选 zh-HK/zh-TW，
# 导致普通话识别整段失败（表现就是"听不懂"）。没 zh-CN 才退而求其次选第一个 zh*。
$sel = $null
foreach ($x in $recs) { if ($x.Culture.Name -eq 'zh-CN') { $sel = $x; break } }
if (-not $sel) {
  foreach ($x in $recs) { if ($x.Culture.Name -like 'zh*') { $sel = $x; break } }
}
$warn = ''
if (-not $sel) {
  $sel = $recs[0]
  $warn = ('系统没有中文识别器，已改用 ' + $sel.Culture.Name + '（中文识别会不准，建议装中文语音包）')
}

try {
  $r = New-Engine $sel.Culture
} catch {
  [Console]::Out.WriteLine('READY_ERR 构造识别器失败（' + $sel.Culture.Name + '）：' + $_.Exception.Message)
  [Console]::Out.Flush(); exit
}

try { $r.SetInputToNull() } catch {}

# 探测系统到底装了哪些中文识别包（供语种回落 / 自检使用）
$curCul = $sel.Culture.Name
$hasHK = $false; $hasTW = $false
foreach ($x in $recs) {
  if ($x.Culture.Name -eq 'zh-HK') { $hasHK = $true }
  if ($x.Culture.Name -eq 'zh-TW') { $hasTW = $true }
}

if ($warn) { [Console]::Out.WriteLine('READY_WARN ' + $warn + ' ||CUL=' + $curCul + ' ||HAS_HK=' + $hasHK + ' ||HAS_TW=' + $hasTW) }
else       { [Console]::Out.WriteLine('READY ' + $curCul + ' ||HAS_HK=' + $hasHK + ' ||HAS_TW=' + $hasTW) }
[Console]::Out.Flush()

# v0.28.4：把"听一次"抽成函数，主识别失败（空 / 置信度过低）可自动用回落语种再听一次。
$CONF_MIN = 0.50
function DoListen($engine, $t) {
  try { $engine.SetInputToDefaultAudioDevice() } catch { return @('', 0.0) }
  try { $engine.InitialSilenceTimeout = [TimeSpan]::FromSeconds($t) } catch {}
  [Console]::Out.WriteLine('LISTENING'); [Console]::Out.Flush()
  $global:__text = $null; $global:__got = $false; $global:__conf = 0.0; $global:__completed = $false
  $spH = {
    param($s, $evt)
    try {
      if ($evt.Result -and $evt.Result.Text) {
        $global:__text = $evt.Result.Text
        $global:__got  = $true
        $global:__conf = [double]$evt.Result.Confidence
      }
    } catch {}
  }
  $cpH = {
    param($s, $evt)
    try {
      if (-not $global:__got -and $evt.Result -and $evt.Result.Text) {
        $global:__text = $evt.Result.Text
        $global:__got  = $true
        $global:__conf = [double]$evt.Result.Confidence
      }
    } catch {}
    $global:__completed = $true
  }
  $sub1 = Register-ObjectEvent -InputObject $engine -EventName SpeechRecognized  -Action $spH
  $sub2 = Register-ObjectEvent -InputObject $engine -EventName RecognizeCompleted -Action $cpH
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $engine.RecognizeAsync([System.Speech.Recognition.RecognizeMode]::Single)
    while (-not $global:__got -and -not $global:__completed -and $sw.Elapsed.TotalSeconds -lt $t) {
      Start-Sleep -Milliseconds 60
    }
    if ($global:__got) {
      try { $engine.RecognizeAsyncStop() } catch {}
    } elseif (-not $global:__completed) {
      try { $engine.RecognizeAsyncCancel() } catch {}
      $sw2 = [System.Diagnostics.Stopwatch]::StartNew()
      while (-not $global:__got -and -not $global:__completed -and $sw2.Elapsed.TotalSeconds -lt 1.5) {
        Start-Sleep -Milliseconds 60
      }
    }
  } finally {
    try { Unregister-Event -SubscriptionId $sub1.Id } catch {}
    try { Unregister-Event -SubscriptionId $sub2.Id } catch {}
  }
  try { $engine.SetInputToNull() } catch {}
  if ($global:__got -and $global:__text) { return @($global:__text, $global:__conf) }
  return @('', 0.0)
}

while ($true) {
  $line = $null
  try { $line = [Console]::In.ReadLine() } catch { break }
  if ($null -eq $line) { break }
  $line = $line.Trim()
  if ($line -eq '') { continue }
  if ($line -eq 'QUIT') { break }
  if ($line -eq 'PING') { [Console]::Out.WriteLine('PONG'); [Console]::Out.Flush(); continue }

  if ($line.StartsWith('PREFER ')) {
    $want = $line.Substring(7).Trim()
    try {
      $found = $null
      foreach ($x in [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()) {
        if ($x.Culture.Name -eq $want) { $found = $x; break }
      }
      if ($found) {
        try { $r.SetInputToNull() } catch {}
        try { $r.Dispose() } catch {}
        $r = New-Engine $found.Culture
        $curCul = $found.Culture.Name
        try { $r.SetInputToNull() } catch {}
        [Console]::Out.WriteLine('PREFER_OK ' + $want)
      } else {
        [Console]::Out.WriteLine('PREFER_NO ' + $want)
      }
    } catch {
      [Console]::Out.WriteLine('PREFER_ERR ' + $_.Exception.Message)
    }
    [Console]::Out.Flush()
    continue
  }

  if (-not $line.StartsWith('LISTEN')) { continue }
  $t = 6
  $fb = ''
  $parts = $line.Split(' ')
  if ($parts.Count -ge 2) { $tv = 0; if ([int]::TryParse($parts[1], [ref]$tv)) { $t = $tv } }
  if ($t -lt 3) { $t = 3 }
  if ($t -gt 20) { $t = 20 }
  # 解析可选回落语种：LISTEN 6 FB=zh-CN
  for ($i = 2; $i -lt $parts.Count; $i++) {
    if ($parts[$i].StartsWith('FB=')) { $fb = $parts[$i].Substring(3).Trim() }
  }

  # 主识别（当前语种）
  $res = DoListen $r $t
  $text = $res[0]; $conf = $res[1]

  # v0.28.4：主识别空 或 置信度过低（多半是语种不匹配，比如用粤语引擎听普通话）
  # 且指定了回落语种（且与主语种不同）→ 换回落语种再听一次，取更可信的结果。
  if (($text -eq '' -or $conf -lt $CONF_MIN) -and $fb -ne '' -and $fb -ne $curCul) {
    try {
      $fbe = $null
      foreach ($x in [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers()) {
        if ($x.Culture.Name -eq $fb) { $fbe = $x; break }
      }
      if ($fbe) {
        try { $r.SetInputToNull() } catch {}
        $r2 = New-Engine $fbe.Culture
        $res2 = DoListen $r2 $t
        try { $r2.SetInputToNull() } catch {}
        try { $r2.Dispose() } catch {}
        if (($res2[0] -ne '') -and (($text -eq '') -or ($res2[1] -ge $conf))) {
          $text = $res2[0]; $conf = $res2[1]
        }
      }
    } catch {}
  }

  if ($text -ne '') { [Console]::Out.WriteLine('TEXT ' + $text + ' ||CONF=' + $conf.ToString('F2')) }
  else              { [Console]::Out.WriteLine('EMPTY') }
  [Console]::Out.Flush()
}

try { $r.SetInputToNull() } catch {}
try { $r.Dispose() } catch {}
"""


def _ps_path() -> str:
    root = os.environ.get("SystemRoot") or r"C:\Windows"
    p = os.path.join(root, "System32", "WindowsPowerShell", "v1.0",
                     "powershell.exe")
    return p if os.path.exists(p) else ""


def _ding():
    """开始录音的提示音（系统自带，无外部依赖）。"""
    try:
        import winsound
        winsound.Beep(880, 120)
    except Exception:
        pass


# ---------------------------------------------------------------- 常驻服务
_SVC_LOCK = threading.Lock()
_SVC = {
    "p": None,          # Popen
    "q": None,          # Queue[str|None]
    "cul": "",          # 当前识别器语种
    "warn": "",         # 非中文识别器的提醒
    "err": "",          # 启动失败原因
    "t0": 0.0,
    "prefer": "",       # 期望语种（跟随用户语言）
    "has_hk": False,    # 系统是否装了粤语识别包（zh-HK）
    "has_tw": False,    # 系统是否装了台湾识别包（zh-TW）
}
_SCRIPT_PATH = {"p": ""}
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def _script_file() -> str:
    if _SCRIPT_PATH["p"] and os.path.exists(_SCRIPT_PATH["p"]):
        return _SCRIPT_PATH["p"]
    try:
        tf = tempfile.NamedTemporaryFile("w", suffix=".ps1", delete=False,
                                         encoding="utf-8-sig")   # BOM：PS5.1 才认 utf-8
        tf.write(_PS_SVC)
        tf.close()
        _SCRIPT_PATH["p"] = tf.name
        return tf.name
    except Exception:
        return ""


def _reader(p, q: "queue.Queue"):
    try:
        for line in p.stdout:                     # type: ignore[union-attr]
            q.put((line or "").rstrip("\r\n"))
    except Exception:
        pass
    finally:
        q.put(None)                               # EOF 哨兵


def _next_line(q: "queue.Queue", timeout: float) -> Optional[str]:
    try:
        return q.get(timeout=max(0.05, timeout))
    except queue.Empty:
        return None


def _spawn(prefer: str = ""):
    """起一个热进程。

    成功返回 (proc, queue, culture, warn)；启动失败返回 (None, errmsg)。
    """
    exe = _ps_path()
    if not exe:
        return None, "找不到 PowerShell"
    script = _script_file()
    if not script:
        return None, "无法写出识别脚本（临时目录不可写）"
    args = [exe, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", script]
    if prefer:
        args += ["-Prefer", prefer]
    try:
        p = subprocess.Popen(
            args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            errors="replace", bufsize=1, creationflags=_CREATE_NO_WINDOW)
    except Exception as ex:
        return None, "起识别进程失败：%s" % ex
    q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_reader, args=(p, q), daemon=True).start()
    first = _next_line(q, 25.0)                   # 冷启动 + 建引擎 ≈ 2s
    if not first:
        try:
            p.kill()
        except Exception:
            pass
        return None, "识别进程启动超时（25 秒没就绪）"
    if first.startswith("READY_ERR"):
        try:
            p.kill()
        except Exception:
            pass
        return None, first[9:].strip() or "识别引擎初始化失败"
    warn, cul, has_hk, has_tw = "", "", False, False
    if first.startswith("READY_WARN"):
        head, _, tail = first.partition("||CUL=")
        warn, cul = head[10:].strip(), tail.strip()
    elif first.startswith("READY"):
        cul = first[5:].strip()
    # 解析 HAS_HK / HAS_TW（语种回落能力探测）
    _hk = first.find("||HAS_HK=")
    if _hk >= 0:
        has_hk = first[_hk + 9:_hk + 13].startswith("True")
    _tw = first.find("||HAS_TW=")
    if _tw >= 0:
        has_tw = first[_tw + 9:_tw + 13].startswith("True")
    return (p, q, cul, warn, has_hk, has_tw), ""


def _kill(p):
    try:
        if p.stdin:
            p.stdin.close()
    except Exception:
        pass
    try:
        p.kill()
    except Exception:
        pass


def _ensure_service() -> bool:
    """确保常驻热进程可用（调用方必须已持有 _SVC_LOCK）。"""
    p = _SVC.get("p")
    if p is not None and p.poll() is None:
        return True
    res, err = _spawn(_SVC.get("prefer") or "")
    if not res:
        _SVC.update(p=None, q=None, cul="", warn="", err=err)
        return False
    p2, q2, cul, warn, has_hk, has_tw = res
    _SVC.update(p=p2, q=q2, cul=cul, warn=warn, err="", t0=time.time(),
                has_hk=has_hk, has_tw=has_tw)
    return True


def warm_up() -> bool:
    """预热（程序启动时后台调一次）：把 PowerShell + 语音引擎先建好。

    预热后第一次点 🎤 也只需 ~0.2s 进入聆听，不再有 2 秒空窗。
    """
    with _SVC_LOCK:
        return _ensure_service()


def shutdown():
    """退出程序时收尾：让热进程自己退，避免留下孤儿进程。"""
    with _SVC_LOCK:
        p = _SVC.get("p")
        _SVC.update(p=None, q=None)
    if not p:
        return
    try:
        if p.stdin:
            p.stdin.write("QUIT\n")
            p.stdin.flush()
    except Exception:
        pass
    try:
        p.wait(timeout=2)
    except Exception:
        _kill(p)
    try:
        sp = _SCRIPT_PATH.get("p")
        if sp and os.path.exists(sp):
            os.remove(sp)
    except Exception:
        pass


atexit.register(shutdown)


def prefer_culture(cul: str) -> bool:
    """跟随语种切换识别器（如粤语 'zh-HK'）。成功返回 True。"""
    cul = (cul or "").strip()
    _SVC["prefer"] = cul
    if not cul:
        return False
    with _SVC_LOCK:
        if not _ensure_service() or not _SVC.get("q"):
            return False
        try:
            _SVC["p"].stdin.write("PREFER %s\n" % cul)
            _SVC["p"].stdin.flush()
        except Exception:
            return False
        line = _next_line(_SVC["q"], 8.0)
        if line and line.startswith("PREFER_OK"):
            _SVC["cul"] = cul
            return True
        return False


def service_info() -> dict:
    """当前热进程状态（自检/界面用）。"""
    p = _SVC.get("p")
    return {"alive": bool(p is not None and p.poll() is None),
            "culture": _SVC.get("cul") or "",
            "warn": _SVC.get("warn") or "",
            "err": _SVC.get("err") or "",
            "has_hk": _SVC.get("has_hk", False),
            "has_tw": _SVC.get("has_tw", False)}


# ---------------------------------------------------------------- 听一次
def _exchange(p, q: "queue.Queue", timeout: float, warn: str,
              on_listening: Optional[Callable[[], None]] = None,
              fallback: str = "") -> tuple:
    """向已就绪的识别进程发一条 LISTEN，收完结果。

    返回 (text, diag)。**只在这里**判定结果，不做静默重试。
    fallback：主识别空 / 置信度过低时，让引擎换该语种再听一次（如 'zh-CN'）。
    """
    _cmd = "LISTEN %d" % int(max(3, min(20, timeout)))
    if fallback:
        _cmd += " FB=%s" % fallback
    try:
        p.stdin.write(_cmd + "\n")
        p.stdin.flush()
    except Exception as ex:
        return ("", "识别进程失联：%s" % ex)
    heard = False
    deadline = time.time() + max(3.0, min(20.0, timeout)) + 10.0
    while True:
        left = deadline - time.time()
        if left <= 0:
            return ("", "识别超时（等了 %d 秒没有结果）" % int(timeout))
        line = _next_line(q, left)
        if line is None:
            return ("", "识别超时（等了 %d 秒没有结果）" % int(timeout))
        if line == "LISTENING":
            heard = True
            if on_listening:
                try:
                    on_listening()
                except Exception:
                    pass
            continue
        if line.startswith("TEXT "):
            body = line[5:].strip()
            if "||CONF=" in body:            # 去掉置信度尾缀，只留正文
                body = body.split("||CONF=")[0].rstrip()
            return (body, warn)
        if line.startswith("ERR "):
            return ("", line[4:].strip())
        if line == "EMPTY":
            if heard:
                return ("", "麦克风开着但没听清内容"
                           "（离麦远、声音轻，或环境太吵都会这样）")
            return ("", "没听到声音（麦克风可能没开或被别的程序占用）")
        # 其它输出忽略，继续等


def _listen_via_service(timeout: float,
                        on_listening: Optional[Callable[[], None]] = None,
                        fallback: str = "") -> Optional[tuple]:
    """用常驻热进程听一次。服务起不来返回 None。"""
    with _SVC_LOCK:
        if not _ensure_service():
            return None
        p, q = _SVC.get("p"), _SVC.get("q")
        if not p or not q:
            return None
        warn = _SVC.get("warn") or ""
        res = _exchange(p, q, timeout, warn, on_listening, fallback)
        if p.poll() is not None:                 # 进程挂了：下次调用会重启
            _SVC.update(p=None, q=None)
        return res


def _listen_oneshot(timeout: float,
                    on_listening: Optional[Callable[[], None]] = None,
                    fallback: str = "") -> tuple:
    """兜底：临时起一个进程听一次（慢 ~2s，但叮的时机同样正确）。"""
    res, err = _spawn(_SVC.get("prefer") or "")
    if not res:
        return ("", err or "语音识别服务启动失败")
    p, q, _cul, warn = res[0], res[1], res[2], res[3]
    try:
        return _exchange(p, q, timeout, warn, on_listening, fallback)
    finally:
        _kill(p)


def listen_once(timeout: float = 6.0, with_diag: bool = False,
                on_listening: Optional[Callable[[], None]] = None,
                fallback: str = "") -> object:
    """听一次（最长 timeout 秒）。

    `with_diag=True` 返回 (文本, 诊断)；诊断里是**真实原因**，不是"没听清"。
    `on_listening` 在引擎**确实已进入聆听**那一刻回调（在此响叮最准）。
    `fallback`：主识别空 / 置信度过低时，让引擎换该语种再听一次（'zh-CN' 等）。
    """
    res = _listen_via_service(timeout, on_listening, fallback)
    if res is None:
        res = _listen_oneshot(timeout, on_listening, fallback)
    text, diag = res
    return (text, diag) if with_diag else text


def listen_async(on_done: Callable[[tuple], None],
                 timeout: float = 6.0,
                 ding: bool = True,
                 on_state: Optional[Callable[[str], None]] = None,
                 lang_hint: str = "",
                 fallback: str = "") -> threading.Thread:
    """后台听一次，结果回调到 on_done（线程回调，UI 需切主线程）。

    回调参数是 **(文本, 诊断)** 二元组。
    on_state("preparing"/"listening") 给界面做状态提示。
    lang_hint：'zh-HK'/'zh-TW' 等 → 尽量切到对应识别器（需系统装了该语音包）。
    fallback：主识别空 / 置信度过低时换该语种再听一次（'zh-CN' 等）。
    """

    def _job():
        if lang_hint:
            prefer_culture(lang_hint)
        if on_state:
            try:
                on_state("preparing")
            except Exception:
                pass

        def _now_listening():
            if ding:
                _ding()
            if on_state:
                try:
                    on_state("listening")
                except Exception:
                    pass

        text, diag = listen_once(timeout, with_diag=True,
                                 on_listening=_now_listening, fallback=fallback)
        try:
            on_done((text, diag))
        except Exception:
            try:
                on_done((text or "", diag))
            except Exception:
                pass

    t = threading.Thread(target=_job, daemon=True)
    t.start()
    return t


def supported_cultures() -> dict:
    """系统实际装了哪些中文识别包（供麦克风界面选语种 / 回落判断）。"""
    p = _SVC.get("p")
    alive = bool(p is not None and p.poll() is None)
    if not alive:
        # 没预热也去探一次（便宜，不影响热进程）
        try:
            out = _run_ps("Add-Type -AssemblyName System.Speech;"
                          "foreach($x in [System.Speech.Recognition."
                          "SpeechRecognitionEngine]::InstalledRecognizers()){"
                          "$x.Culture.Name}")
            recs = [ln.strip() for ln in out.splitlines() if ln.strip()]
            return {"alive": False,
                    "culture": "",
                    "has_hk": any(c == "zh-HK" for c in recs),
                    "has_tw": any(c == "zh-TW" for c in recs),
                    "recs": recs}
        except Exception:
            return {"alive": False, "culture": "", "has_hk": False,
                    "has_tw": False, "recs": []}
    return {"alive": True,
            "culture": _SVC.get("cul") or "",
            "has_hk": _SVC.get("has_hk", False),
            "has_tw": _SVC.get("has_tw", False)}


# ---------------------------------------------------------------- 自检
def has_recognizer() -> bool:
    """快速探测是否存在中文识别器（热进程已就绪就直接信它）。"""
    if service_info()["alive"]:
        return True
    try:
        import winreg
    except Exception:
        return False
    for base in (r"SOFTWARE\Microsoft\Speech\Recognizers\Tokens",
                 r"SOFTWARE\WOW6432Node\Microsoft\Speech\Recognizers\Tokens"):
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as k:
                i = 0
                while True:
                    try:
                        sub = winreg.EnumKey(k, i)
                        i += 1
                        if "2052" in sub:
                            return True
                    except OSError:
                        break
        except Exception:
            continue
    return False


def _run_ps(ps: str, timeout: float = 40.0) -> str:
    exe = _ps_path()
    if not exe:
        return ""
    try:
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive",
                            "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                            "-Command", ps],
                           timeout=timeout, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           creationflags=_CREATE_NO_WINDOW)
        return (r.stdout or "").strip()
    except Exception:
        return ""


def list_recognizers() -> list:
    """系统里实际装了的识别器语种列表（自检用）。"""
    out = _run_ps("Add-Type -AssemblyName System.Speech;"
                  "foreach($x in [System.Speech.Recognition.SpeechRecognitionEngine]"
                  "::InstalledRecognizers()){ $x.Culture.Name + '|' + $x.Name }")
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _mic_probe() -> str:
    """真去开一次默认麦克风，确认权限/占用（比只读注册表实在）。"""
    out = _run_ps("Add-Type -AssemblyName System.Speech;"
                  "try{ $r=New-Object System.Speech.Recognition.SpeechRecognitionEngine;"
                  "$r.SetInputToDefaultAudioDevice(); $r.SetInputToNull();"
                  "'MIC=OK' }catch{ 'MIC=ERR ' + $_.Exception.Message }")
    if "MIC=OK" in out:
        return "✅ 麦克风可打开（权限正常）"
    for ln in out.splitlines():
        if ln.startswith("MIC=ERR"):
            return ("⚠️ 麦克风打不开：%s\n"
                    "   检查：设置 → 隐私和安全性 → 麦克风 → 打开「让桌面应用访问麦克风」；"
                    "并确认没被别的软件独占。" % ln[8:].strip())
    return "麦克风：状态未知（%s）" % (out[:120] or "无输出")


def diagnose() -> str:
    """返回一段**给人看**的语音输入自检结论（可以原样贴给用户）。"""
    if not _ps_path():
        return "❌ 找不到 PowerShell，语音输入不可用。"
    info = service_info()
    recs = list_recognizers()
    if not recs:
        return ("❌ 系统里**没有任何语音识别器**，语音输入用不了。\n"
                "去：设置 → 时间和语言 → 语音 → 安装「语音识别」；\n"
                "装完重启本应用（或说「语音自检」再看一次）。")
    zh = [c for c in recs if c.split("|")[0].lower().startswith("zh")]
    yue = [c for c in recs
           if c.split("|")[0].lower() in ("zh-hk", "zh-mo", "yue")]
    lines = ["识别器共 %d 个：%s" % (len(recs), "、".join(
        c.split("|")[0] for c in recs))]
    if not zh:
        lines.append("⚠️ 没有中文识别器 → 中文会听不准。去：设置 → 时间和语言 → "
                     "语言 → 中文(简体) → 语言选项 → 安装「语音识别」。")
    # v0.28.4：把"方言能听不能听"一次说清楚（SAPI 只有 zh-CN/zh-TW/zh-HK 三种）
    lines.append("📣 方言识别能力（SAPI 引擎限制：普通话 + 所有官话方言共用 zh-CN）：")
    lines.append("   · 普通话 / 四川 / 河南 / 北京 / 云南 / 东北 / 山东 → ✅ 走 zh-CN，"
                 "带口音也能听")
    if yue:
        lines.append("   · 粤语 → ✅ 走 %s，自动切过去（说粤语会先认粤语，"
                     "听不清时再回落普通话）" % yue[0].split("|")[0])
    else:
        lines.append("   · 粤语 → ⚠️ 没装 zh-HK 语音包，讲粤语会按普通话来认、"
                     "准确率一般；装了就能自动跟上")
    tw = [c for c in recs if c.split("|")[0].lower() in ("zh-tw", "zh-mo", "yue")]
    if yue or tw:
        lines.append("   · 台湾腔 → %s 走 zh-TW" % (
            "✅ 已装" if any(c.split("|")[0].lower() == "zh-tw" for c in recs)
            else "⚠️ 没装 zh-TW 包，按普通话认"))
    lines.append("   · SAPI 不能从声音判断方言，方言是「先识别文字、再反推」决定的；"
                 "若某方言老听错，多半是识别器语种被锁错，说「语音自检」我帮你查。")
    if info["alive"]:
        lines.append("✅ 语音热进程已就绪（当前识别器 %s）——点 🎤 听到「叮」就能说，"
                     "进入聆听约 0.2 秒。" % (info["culture"] or "?"))
    else:
        lines.append("⚠️ 热进程未就绪：%s（第一次点 🎤 会慢约 2 秒，属正常）"
                     % (info["err"] or "还没预热"))
    if info["warn"]:
        lines.append("⚠️ " + info["warn"])
    lines.append(_mic_probe())
    return "\n".join(lines)
