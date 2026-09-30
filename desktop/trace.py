# -*- coding: utf-8 -*-
"""trace.py —— 轮次级观测 + 一键诊断包（P1-6）。

真问题：2026-09-30 定位那次事故，我人工翻了 `pasm.log` / `ops_ledger.jsonl` /
`chat_last.json` / `sessions/*.json` **四份**文件才对上因果。系统只记了"动作级"
台账（`ops_ledger`），**没有**"这一轮模型收到什么、回了什么、调了哪些工具、
各花了多久"——而这正是排障最需要的一层。

本模块提供：
  · `log(kind, **fields)` —— 追加一行 JSON 到 `trace.jsonl`（带时间戳；超限自动轮转，
    永不无限增长）；
  · `span(kind, **fields)` —— 上下文管理器，自动记 elapsed（含异常也要记，别把
    失败的调用痕迹丢掉）；
  · `recent(n)` —— 读最近 n 条（给界面"看看刚才发生了什么"）；
  · `bundle()` —— **一键诊断包**：打包日志+台账+trace+失败册+**脱敏后**的配置，
    用户点一下就能把排障材料交出来，不用教他去找文件。

脱敏规则：任何键名含 `key/token/secret/password/appid/appsecret` 的值一律替换为
`***`（保留长度提示）—— 诊断包会外发，绝不能带密钥。
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import time
import zipfile

#: 单文件上限（超过就轮转成 .1），免得 trace 把磁盘写爆
MAX_BYTES = 4 * 1024 * 1024
#: 脱敏键名特征
_SECRET_KEY = re.compile(r"(api_key|apikey|key|token|secret|password|passwd|pwd|"
                         r"appid|appsecret|cookie|authorization)", re.I)


def _dir() -> str:
    try:
        import logsetup
        d = logsetup.data_dir()
    except Exception:                                            # noqa: BLE001
        d = os.environ.get("PASMSTUDIO_DATA") or os.path.join(
            os.path.expanduser("~"), ".pasmstudio")
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass
    return d


def path() -> str:
    return os.path.join(_dir(), "trace.jsonl")


def _rotate_if_big() -> None:
    p = path()
    try:
        if os.path.isfile(p) and os.path.getsize(p) > MAX_BYTES:
            bak = p + ".1"
            if os.path.isfile(bak):
                os.unlink(bak)
            shutil.move(p, bak)
    except OSError:
        pass


def log(kind: str, **fields) -> None:
    """记一条 trace。**任何异常都不能影响主流程**（观测绝不该拖垮干活）。"""
    try:
        _rotate_if_big()
        row = {"t": time.strftime("%Y-%m-%d %H:%M:%S"), "kind": str(kind)[:40]}
        for k, v in (fields or {}).items():
            if isinstance(v, (int, float, bool)) or v is None:
                row[k] = v
            else:
                s = str(v)
                row[k] = s[:800]
        with open(path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:                                            # noqa: BLE001
        pass


class span:
    """`with trace.span("llm", model=m): ...` → 自动记 elapsed 与是否异常。"""

    def __init__(self, kind: str, **fields):
        self.kind = kind
        self.fields = fields or {}
        self.t0 = time.time()
        self.err = ""

    def __enter__(self):
        return self

    def __exit__(self, tp, val, tb):
        el = time.time() - self.t0
        f = dict(self.fields)
        f["elapsed"] = round(el, 3)
        if tp is not None:
            f["error"] = "%s: %s" % (getattr(tp, "__name__", "Exc"), str(val)[:200])
        log(self.kind, **f)
        return False


def recent(n: int = 50, kind: str = "") -> list:
    """读最近 n 条（可按 kind 过滤）。"""
    p = path()
    if not os.path.isfile(p):
        return []
    rows = []
    try:
        for ln in open(p, encoding="utf-8"):
            try:
                r = json.loads(ln)
            except Exception:                                    # noqa: BLE001
                continue
            if kind and r.get("kind") != kind:
                continue
            rows.append(r)
    except OSError:
        return []
    return rows[-max(1, int(n)):]


def _mask(obj, depth: int = 0):
    """递归脱敏：键名命中密钥特征 → 值换成 ***（保留"有没有填"的信息）。"""
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _SECRET_KEY.search(str(k)):
                s = "" if v in (None, "") else str(v)
                out[k] = "" if not s else "***（已脱敏，长度 %d）" % len(s)
            else:
                out[k] = _mask(v, depth + 1)
        return out
    if isinstance(obj, list):
        return [_mask(x, depth + 1) for x in obj[:200]]
    if isinstance(obj, str) and len(obj) > 2000:
        return obj[:2000] + "…（截断）"
    return obj


def mask_config(cfg: dict) -> dict:
    """给设置页/诊断包用的脱敏配置（**唯一出口**，别各处自己写）。"""
    return _mask(cfg or {})


def bundle(out_path: str = "", *, tail_lines: int = 400) -> dict:
    """打一个诊断包（zip）。返回 {"ok", "path", "items", "bytes"}。"""
    d = _dir()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = out_path or os.path.join(d, "诊断包_%s.zip" % stamp)
    items = []

    def _tail(fp: str, n: int) -> str:
        try:
            with open(fp, encoding="utf-8", errors="replace") as f:
                return "".join(f.readlines()[-n:])
        except OSError as ex:
            return "（读取失败：%s）" % ex

    try:
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("pasm.log", "trace.jsonl", "ops_ledger.jsonl",
                         "failbook.jsonl", "work_session.json"):
                fp = os.path.join(d, name)
                if os.path.isfile(fp):
                    z.writestr(name, _tail(fp, tail_lines))
                    items.append(name)
            cfg = os.path.join(d, "config.json")
            if os.path.isfile(cfg):
                try:
                    z.writestr("config.masked.json",
                               json.dumps(_mask(json.load(open(cfg, encoding="utf-8"))),
                                          ensure_ascii=False, indent=1))
                    items.append("config.masked.json")
                except Exception as ex:                          # noqa: BLE001
                    z.writestr("config.masked.json", "（解析失败：%s）" % ex)
            try:
                import failbook as FB
                z.writestr("failbook_stats.json",
                           json.dumps(FB.stats(), ensure_ascii=False, indent=1))
                items.append("failbook_stats.json")
            except Exception:                                    # noqa: BLE001
                pass
            z.writestr("README.txt",
                       "PASM Studio 诊断包\n生成时间：%s\n数据目录：%s\n"
                       "内容：日志尾部 / trace（轮次级观测）/ 操作台账 / 失败册 / "
                       "脱敏配置。\n密钥类字段已替换为 ***，可安全外发。\n"
                       % (time.strftime("%Y-%m-%d %H:%M:%S"), d))
            items.append("README.txt")
        return {"ok": True, "path": out, "items": items,
                "bytes": os.path.getsize(out) if os.path.isfile(out) else 0}
    except Exception as ex:                                      # noqa: BLE001
        return {"ok": False, "path": out, "items": items, "error": str(ex)[:200],
                "bytes": 0}


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    import tempfile
    os.environ["PASMSTUDIO_DATA"] = tempfile.mkdtemp(prefix="trace_")
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    print("=== 记录与读取 ===")
    log("llm", task="dev", model="deepseek-chat", prompt_chars=1234, out_chars=567)
    with span("tool", tool="write", target="a.py"):
        time.sleep(0.05)
    try:
        with span("tool_fail", tool="run"):
            raise RuntimeError("故意失败")
    except RuntimeError:
        pass                                  # span 不吞异常，调用方自己接（这是对的）
    rows = recent(10)
    ck("记进去了", len(rows) >= 3, len(rows))
    ck("字段保留", any(r.get("kind") == "llm" and r.get("task") == "dev" for r in rows))
    ck("span 记了耗时", any("elapsed" in r and r["elapsed"] >= 0.04 for r in rows))
    ck("**失败的调用也留痕**（不丢证据）",
      any(r.get("kind") == "tool_fail" and "RuntimeError" in (r.get("error") or "")
          for r in rows))
    ck("可按 kind 过滤", all(r["kind"] == "llm" for r in recent(10, kind="llm")))

    print("\n=== 脱敏（诊断包会外发，绝不能带密钥）===")
    m = mask_config({"api_key": "sk-abcdef123456", "base_url": "https://x",
                     "feishu_app_secret": "topsecret", "ark_key": "",
                     "nested": {"discord_bot_token": "abc.def"}})
    ck("api_key 被脱敏", "sk-abcdef123456" not in json.dumps(m, ensure_ascii=False))
    ck("空值保持空（信息不丢）", m["ark_key"] == "")
    ck("嵌套也脱敏", "abc.def" not in json.dumps(m, ensure_ascii=False))
    ck("非密钥字段原样", m["base_url"] == "https://x")

    print("\n=== 诊断包 ===")
    open(os.path.join(os.environ["PASMSTUDIO_DATA"], "pasm.log"), "w",
         encoding="utf-8").write("line1\nline2\n")
    open(os.path.join(os.environ["PASMSTUDIO_DATA"], "config.json"), "w",
         encoding="utf-8").write(json.dumps({"api_key": "sk-verysecret"}))
    b = bundle()
    ck("打包成功", b["ok"] and os.path.isfile(b["path"]), b)
    with zipfile.ZipFile(b["path"]) as z:
        names = z.namelist()
        cfg_txt = z.read("config.masked.json").decode("utf-8")
        ck("包含 trace 与日志", any("trace" in n for n in names) and
          any("pasm.log" in n for n in names), names)
        ck("包含脱敏配置", "config.masked.json" in names)
        ck("**包里没有明文密钥**", "sk-verysecret" not in cfg_txt, cfg_txt[:80])
        ck("含说明文件", "README.txt" in names)

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
