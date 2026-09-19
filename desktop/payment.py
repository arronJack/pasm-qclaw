# -*- coding: utf-8 -*-
"""payment.py —— 支付接入层（v0.30.11，#2-B「接支付」）

一个统一接口 + 三个 provider：

| provider | 状态 | 能干什么 |
|---|---|---|
| `sandbox`  | **默认，离线可跑** | 完整跑通「下单 → 支付 → 回调验签 → 查单 → 退款」，用内置测试凭据 |
| `wechat`   | **骨架** | 接口/配置位齐全；没填凭据一律 `NotConfigured`，不假装能收钱 |
| `alipay`   | **骨架** | 同上 |

## 为什么沙箱要"真签名"而不是 print 假数据

如果沙箱只是返回假的成功，那么**验签、幂等、状态机这些真正容易出事的地方
一行都没被跑过**（接真渠道那天才发现签名拼串顺序错了）。
所以沙箱也用 **HMAC-SHA256 真签真验**，只是密钥来自本地配置、渠道是本地模拟。
换真实 provider 时，签名/回调/状态机这套逻辑已经跑过千百遍。

## 四条铁律（钱的事，宁可多拦一次）

1. **默认拒绝真实收款**：`provider=sandbox` + `live=false` 是出厂默认。
   要开真实支付必须**显式**把 `live` 改成 true 且填齐凭据；缺一样就 `NotConfigured`。
2. **永不硬编码真实密钥**：凭据只从 `<数据目录>/payment.json` 读。
3. **日志脱敏**：`mask()` 把商户号/密钥只留首尾，任何出参、日志、界面都不回显全文。
4. **不代用户付钱**：`cando.py` 那条「涉及你的钱，这类操作我不代做」不动摇。
   本模块是**给应用/生成的项目用的收单能力**（"我的网站怎么收钱"），
   不是"帮我下单买这个东西"。

## 金额单位

**一律用「分」（int）**，不用 float。`12.30 元` = `1230`。
浮点金额是做支付最容易留下的坑（0.1+0.2 那套），这里从接口层就堵死。
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import time
import uuid


# ==========================================================================
# 数据目录 / 配置
# ==========================================================================
def data_dir() -> str:
    """统一数据目录。

    `PASM_STUDIO_DIR` **优先** —— 与 `knowledge.py` / `scaffold.py` 同一条约定：
    验证脚本与第二实例必须能隔离，否则会直接写用户真实配置
    （scaffold 第一版就因为没守这条，往真实目录丢了 4 个测试工程）。
    """
    env_dir = (os.environ.get("PASM_STUDIO_DIR") or "").strip()
    if env_dir:
        return env_dir
    try:
        import logsetup as LS
        return LS.data_dir()
    except Exception:  # noqa: BLE001
        return os.path.join(os.path.expanduser("~"), ".pasmstudio")


def config_path() -> str:
    return os.path.join(data_dir(), "payment.json")


def store_path() -> str:
    return os.path.join(data_dir(), "payment_sandbox.json")


def default_config() -> dict:
    """出厂默认：**沙箱 + 不真实收款**，凭据留空等用户填。"""
    return {
        "provider": "sandbox",
        "live": False,                     # ← 铁律 1：默认不允许真实收款
        "currency": "CNY",
        "sandbox": {
            "mch_id": "SANDBOX_MCH",
            "app_id": "SANDBOX_APP",
            "api_key": "sandbox-secret-please-change",
            "notify_url": "http://127.0.0.1:8000/pay/notify",
        },
        "wechat": {
            "mch_id": "", "app_id": "", "api_v3_key": "",
            "cert_serial": "", "private_key_path": "",
            "notify_url": "",
        },
        "alipay": {
            "app_id": "", "private_key_path": "",
            "alipay_public_key_path": "",
            "gateway": "https://openapi.alipay.com/gateway.do",
            "notify_url": "",
        },
    }


def load_config() -> dict:
    """读配置；不存在就落一份默认的（**幂等**，方便界面第一次打开就有东西）。"""
    p = config_path()
    cfg = default_config()
    try:
        if os.path.exists(p):
            with open(p, "r", encoding="utf-8") as f:
                got = json.load(f)
            if isinstance(got, dict):
                for k, v in got.items():
                    if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                        cfg[k].update(v)
                    else:
                        cfg[k] = v
            return cfg
    except Exception:  # noqa: BLE001  配置坏了也不能让应用起不来
        pass
    save_config(cfg)
    return cfg


def save_config(cfg: dict) -> str:
    """写配置（原子替换，避免半截文件）。返回路径。"""
    p = config_path()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, p)
    except Exception:  # noqa: BLE001
        pass
    return p


def mask(s: str, keep_head: int = 4, keep_tail: int = 2) -> str:
    """脱敏：日志/界面/报错里**绝不回显**完整密钥（铁律 3）。"""
    s = (s or "").strip()
    if not s:
        return ""
    if len(s) <= keep_head + keep_tail:
        return "*" * len(s)
    return s[:keep_head] + "*" * (len(s) - keep_head - keep_tail) + s[-keep_tail:]


class NotConfigured(Exception):
    """渠道没配齐 —— **明确拒绝**，不降级、不假装成功。"""


class SignatureError(Exception):
    """验签不过 —— 回调一律拒收（这是防伪造回调的唯一防线）。"""


# ==========================================================================
# 签名（沙箱与真实渠道共用同一套拼串规则）
# ==========================================================================
def _sign_payload(payload: dict, api_key: str) -> str:
    """按 key 字典序拼 `k=v&k=v`（去掉空值与 sign 本身）后 HMAC-SHA256。

    真渠道各有自己的拼串规则，接入时**只需替换这一个函数**；
    验签/状态机/幂等这些逻辑不用动 —— 这正是沙箱存在的意义。
    """
    items = [(k, str(v)) for k, v in sorted(payload.items())
             if k != "sign" and v is not None and str(v) != ""]
    msg = "&".join("%s=%s" % (k, v) for k, v in items)
    return hmac.new((api_key or "").encode("utf-8"),
                    msg.encode("utf-8"), hashlib.sha256).hexdigest()


def sign(payload: dict, api_key: str) -> dict:
    p = dict(payload)
    p["sign"] = _sign_payload(p, api_key)
    return p


def verify(payload: dict, api_key: str) -> bool:
    """常数时间比较，防时序侧信道。"""
    got = str((payload or {}).get("sign") or "")
    want = _sign_payload(payload or {}, api_key)
    return bool(got) and hmac.compare_digest(got, want)


# ==========================================================================
# 金额
# ==========================================================================
def yuan_to_fen(v) -> int:
    """元 → 分。字符串/整数/浮点都吃；**用 Decimal 式的整数运算**避免二进制浮点误差。"""
    if isinstance(v, bool):
        raise ValueError("金额不能是布尔值")
    if isinstance(v, int):
        return v * 100                      # 语义：整数元
    s = str(v).strip()
    if not re.fullmatch(r"\d+(\.\d{1,2})?", s):
        raise ValueError("金额格式不对（最多两位小数）：%r" % v)
    a, _, b = s.partition(".")
    return int(a) * 100 + int((b + "00")[:2])


def fen_to_yuan(fen: int) -> str:
    fen = int(fen or 0)
    return "%d.%02d" % (fen // 100, abs(fen) % 100)


# ==========================================================================
# Provider
# ==========================================================================
class Provider:
    """统一收单接口。子类只需实现这些方法，上层（工作流/生成的项目）不必关心渠道。"""

    name = "base"
    needs_live = True                    # 是否必须 live=true 才允许真实收款

    def __init__(self, cfg: dict):
        self.cfg = cfg or {}

    # -- 子类必须实现 --
    def _cred(self) -> dict:
        raise NotImplementedError

    def create_order(self, out_trade_no: str, amount_fen: int, subject: str,
                     notify_url: str = "") -> dict:
        raise NotImplementedError

    def query_order(self, out_trade_no: str) -> dict:
        raise NotImplementedError

    def close_order(self, out_trade_no: str) -> dict:
        raise NotImplementedError

    def refund(self, out_trade_no: str, refund_fen: int, reason: str = "") -> dict:
        raise NotImplementedError

    def verify_notify(self, payload: dict) -> bool:
        raise NotImplementedError

    # -- 公共 --
    def ready(self) -> tuple:
        """(能不能用, 原因)。**不能用就说为什么**，不静默降级。"""
        try:
            self._cred()
        except NotConfigured as e:
            return False, str(e)
        except Exception as e:  # noqa: BLE001
            return False, "%s: %s" % (type(e).__name__, e)
        return True, ""

    def status(self) -> dict:
        ok, why = self.ready()
        return {"provider": self.name, "live": bool(self.cfg.get("live")),
                "ready": ok, "reason": why,
                "currency": self.cfg.get("currency") or "CNY"}


class SandboxProvider(Provider):
    """本地模拟渠道：**离线可跑、真签名、有状态机**。

    状态流转：NOTPAY → SUCCESS →（退款）REFUNDED；未支付可 CLOSED。
    订单存 `<数据目录>/payment_sandbox.json`，重启不丢，可查历史。
    """

    name = "sandbox"
    needs_live = False                   # 沙箱本来就不该"真实收款"

    # ---------- 凭据 / 存储 ----------
    def _cred(self) -> dict:
        c = (self.cfg.get("sandbox") or {})
        if not c.get("api_key"):
            raise NotConfigured("沙箱密钥缺失（payment.json → sandbox.api_key）")
        return c

    def _load(self) -> dict:
        try:
            with open(store_path(), "r", encoding="utf-8") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception:  # noqa: BLE001
            return {}

    def _save(self, d: dict) -> None:
        p = store_path()
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(d, f, ensure_ascii=False, indent=1)
            os.replace(tmp, p)
        except Exception:  # noqa: BLE001
            pass

    # ---------- 下单 ----------
    def create_order(self, out_trade_no: str, amount_fen: int, subject: str,
                     notify_url: str = "") -> dict:
        c = self._cred()
        if not out_trade_no:
            out_trade_no = "SB" + uuid.uuid4().hex[:16].upper()
        if int(amount_fen) <= 0:
            raise ValueError("金额必须大于 0 分")
        db = self._load()
        if out_trade_no in db:
            # 幂等：同一单号重复下单返回原单，不新建
            return dict(db[out_trade_no]["order"], idempotent=True)
        order = {
            "out_trade_no": out_trade_no,
            "amount_fen": int(amount_fen),
            "amount_yuan": fen_to_yuan(amount_fen),
            "subject": subject or "未命名订单",
            "state": "NOTPAY",
            "mch_id": c.get("mch_id", ""),
            "app_id": c.get("app_id", ""),
            "notify_url": notify_url or c.get("notify_url", ""),
            "created": time.strftime("%Y-%m-%d %H:%M:%S"),
            # 沙箱专属：给前端一个"去付款"的本地链接
            "pay_url": "sandbox://pay?out_trade_no=%s" % out_trade_no,
        }
        db[out_trade_no] = {"order": order, "refunds": []}
        self._save(db)
        return sign(order, c["api_key"])

    # ---------- 模拟用户支付（沙箱专属，真实渠道没有这一步） ----------
    def pay(self, out_trade_no: str) -> dict:
        """模拟「用户扫码付掉了」——用于把回调链路跑通。"""
        c = self._cred()
        db = self._load()
        rec = db.get(out_trade_no)
        if not rec:
            raise KeyError("没有这笔单：%s" % out_trade_no)
        if rec["order"]["state"] == "NOTPAY":
            rec["order"]["state"] = "SUCCESS"
            rec["order"]["paid"] = time.strftime("%Y-%m-%d %H:%M:%S")
            rec["order"]["transaction_id"] = "SB%s" % uuid.uuid4().hex[:14].upper()
            self._save(db)
        body = dict(rec["order"])
        body["event"] = "TRANSACTION.SUCCESS"
        return sign(body, c["api_key"])

    def query_order(self, out_trade_no: str) -> dict:
        self._cred()
        rec = self._load().get(out_trade_no)
        if not rec:
            return {"out_trade_no": out_trade_no, "state": "NOTFOUND"}
        return dict(rec["order"])

    def close_order(self, out_trade_no: str) -> dict:
        self._cred()
        db = self._load()
        rec = db.get(out_trade_no)
        if not rec:
            return {"out_trade_no": out_trade_no, "state": "NOTFOUND"}
        if rec["order"]["state"] == "NOTPAY":
            rec["order"]["state"] = "CLOSED"
            self._save(db)
        return dict(rec["order"])

    # ---------- 退款 ----------
    def refund(self, out_trade_no: str, refund_fen: int, reason: str = "") -> dict:
        c = self._cred()
        db = self._load()
        rec = db.get(out_trade_no)
        if not rec:
            return {"out_trade_no": out_trade_no, "state": "NOTFOUND"}
        o = rec["order"]
        if o["state"] != "SUCCESS":
            # 没收到钱就退＝不可能，明确拒绝（别把"未支付"当"已退款"）
            return {"out_trade_no": out_trade_no, "state": o["state"],
                    "error": "订单不是已支付状态，不能退款"}
        already = sum(int(r["amount_fen"]) for r in rec.get("refunds") or [])
        if int(refund_fen) <= 0 or already + int(refund_fen) > o["amount_fen"]:
            return {"out_trade_no": out_trade_no, "state": o["state"],
                    "error": "退款金额越界（已退 %s，订单 %s）"
                             % (fen_to_yuan(already), o["amount_yuan"])}
        rec.setdefault("refunds", []).append({
            "refund_fen": int(refund_fen), "reason": reason or "",
            "t": time.strftime("%Y-%m-%d %H:%M:%S"),
            "refund_id": "SR%s" % uuid.uuid4().hex[:12].upper(),
        })
        if already + int(refund_fen) >= o["amount_fen"]:
            o["state"] = "REFUNDED"
        self._save(db)
        return {"out_trade_no": out_trade_no, "state": o["state"],
                "refunded_fen": already + int(refund_fen),
                "refunded_yuan": fen_to_yuan(already + int(refund_fen))}

    # ---------- 回调验签（防伪造回调的唯一防线） ----------
    def verify_notify(self, payload: dict) -> bool:
        c = self._cred()
        if not verify(payload or {}, c["api_key"]):
            raise SignatureError("回调签名不对，已拒收")
        return True


class WeChatProvider(Provider):
    """微信支付 v3 —— **骨架**：接口与配置位齐全，没配齐就明确拒绝。

    接入时需要补的三件事（都已在配置里留好位）：
      ① 商户号 `mch_id` + 应用 `app_id`；
      ② APIv3 密钥 `api_v3_key` + 商户证书序列号 `cert_serial` + 私钥文件；
      ③ `_sign_payload` 换成 v3 的「METHOD\\nURL\\nTIMESTAMP\\nNONCE\\nBODY」拼串 +
         RSA-SHA256 签名（**唯一要改的地方**），并接上真实的
         `POST /v3/pay/transactions/jsapi` 等四个端点。
    """

    name = "wechat"

    def _cred(self) -> dict:
        c = self.cfg.get("wechat") or {}
        need = ("mch_id", "app_id", "api_v3_key", "cert_serial",
                "private_key_path")
        missing = [k for k in need if not str(c.get(k) or "").strip()]
        if missing:
            raise NotConfigured("微信支付未配置：缺 %s（填好后把 live 改成 true）"
                                % "、".join(missing))
        if not self.cfg.get("live"):
            raise NotConfigured("微信支付已填凭据，但 live 仍是 false —— "
                                "为避免误收款，需要你显式打开")
        if not os.path.exists(str(c["private_key_path"])):
            raise NotConfigured("商户私钥文件不存在：%s" % c["private_key_path"])
        return c

    def create_order(self, out_trade_no, amount_fen, subject, notify_url=""):
        c = self._cred()
        raise NotConfigured(
            "微信支付下单接口尚未接真实 HTTP（凭据已就绪）。"
            "接入点：POST /v3/pay/transactions/jsapi；"
            "签名改为 RSA-SHA256（见类文档）。商户号=%s，密钥=%s"
            % (mask(c["mch_id"]), mask(c["api_v3_key"])))

    query_order = close_order = refund = verify_notify = create_order


class AlipayProvider(Provider):
    """支付宝 —— **骨架**：同上，配置位齐全，没配齐就明确拒绝。

    接入点：`POST {gateway}`（alipay.trade.precreate / query / refund），
    签名 RSA2（`_sign_payload` 换成支付宝的排序拼串 + 私钥签名），
    异步通知验签用 `alipay_public_key_path` 里的支付宝公钥。
    """

    name = "alipay"

    def _cred(self) -> dict:
        c = self.cfg.get("alipay") or {}
        need = ("app_id", "private_key_path", "alipay_public_key_path")
        missing = [k for k in need if not str(c.get(k) or "").strip()]
        if missing:
            raise NotConfigured("支付宝未配置：缺 %s（填好后把 live 改成 true）"
                                % "、".join(missing))
        if not self.cfg.get("live"):
            raise NotConfigured("支付宝已填凭据，但 live 仍是 false —— "
                                "为避免误收款，需要你显式打开")
        return c

    def create_order(self, out_trade_no, amount_fen, subject, notify_url=""):
        self._cred()
        raise NotConfigured(
            "支付宝下单接口尚未接真实 HTTP（凭据已就绪）。"
            "接入点：alipay.trade.precreate；签名改为 RSA2（见类文档）")

    query_order = close_order = refund = verify_notify = create_order


_PROVIDERS = {"sandbox": SandboxProvider, "wechat": WeChatProvider,
              "alipay": AlipayProvider}


def get_provider(name: str = "", cfg: dict = None) -> Provider:
    """取 provider。名字不认识 → **回落沙箱并在 status 里说明**（不让应用崩）。

    回落是安全的：沙箱不会收真钱。但要能看见，所以 `status()['fallback']`。
    """
    cfg = cfg or load_config()
    want = (name or cfg.get("provider") or "sandbox").strip().lower()
    cls = _PROVIDERS.get(want)
    if cls is None:
        cfg = dict(cfg)
        cfg["_fallback"] = want
        return SandboxProvider(cfg)
    return cls(cfg)


# ==========================================================================
# 给生成的项目用的收单适配层（"我的网站怎么收钱"）
# ==========================================================================
def adapter_files(stack: str, cfg: dict = None) -> dict:
    """按技术栈产出**可运行的收单适配层**文件 {相对路径: 内容}。

    刻意不依赖 PASM 本身 —— 生成的项目拷走也能跑（沙箱协议内联在文件里）。
    """
    cfg = cfg or load_config()
    sb = cfg.get("sandbox") or {}
    secret = sb.get("api_key") or "sandbox-secret-please-change"
    notify = sb.get("notify_url") or "http://127.0.0.1:8000/pay/notify"
    conf = json.dumps({"provider": cfg.get("provider"), "live": cfg.get("live"),
                       "currency": cfg.get("currency"),
                       "sandbox": {"mch_id": sb.get("mch_id"),
                                   "notify_url": notify,
                                   "api_key": "***填在服务端环境变量里***"}},
                      ensure_ascii=False, indent=2)

    common_py = '''# -*- coding: utf-8 -*-
"""payment_adapter.py —— 收单适配层（沙箱，可直接换成真实渠道）。

协议与 PASM 的 payment.py 完全一致（真 HMAC-SHA256 签名 + 状态机），
所以先用它在本地把「下单→支付→回调→查单→退款」跑通，
接真渠道时只需替换 `_sign` 与 `create_order` 里的 HTTP 调用。
"""
import hashlib, hmac, json, os, time, uuid

API_KEY = os.environ.get("PAY_API_KEY", "%(secret)s")
STORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_pay.json")


def _sign(payload):
    items = sorted((k, str(v)) for k, v in payload.items()
                   if k != "sign" and str(v) != "")
    msg = "&".join("%%s=%%s" %% kv for kv in items)
    return hmac.new(API_KEY.encode(), msg.encode(), hashlib.sha256).hexdigest()


def signed(payload):
    d = dict(payload); d["sign"] = _sign(d); return d


def verify(payload):
    got = str((payload or {}).get("sign") or "")
    return bool(got) and hmac.compare_digest(got, _sign(payload or {}))


def _load():
    try:
        return json.load(open(STORE, encoding="utf-8"))
    except Exception:
        return {}


def _save(d):
    json.dump(d, open(STORE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def create_order(amount_fen, subject="订单", out_trade_no=""):
    """下单。amount_fen 是**分**（整数），别传浮点。"""
    if int(amount_fen) <= 0:
        raise ValueError("金额必须大于 0 分")
    db = _load()
    oid = out_trade_no or "SB" + uuid.uuid4().hex[:16].upper()
    if oid in db:
        return signed(db[oid])
    order = {"out_trade_no": oid, "amount_fen": int(amount_fen),
             "subject": subject, "state": "NOTPAY",
             "created": time.strftime("%%Y-%%m-%%d %%H:%%M:%%S")}
    db[oid] = order; _save(db)
    return signed(order)


def pay(out_trade_no):
    """沙箱专用：模拟用户付掉（真实渠道由渠道回调代替）。"""
    db = _load()
    if out_trade_no not in db:
        raise KeyError("没有这笔单")
    if db[out_trade_no]["state"] == "NOTPAY":
        db[out_trade_no]["state"] = "SUCCESS"
        db[out_trade_no]["paid"] = time.strftime("%%Y-%%m-%%d %%H:%%M:%%S")
        _save(db)
    ev = dict(db[out_trade_no]); ev["event"] = "TRANSACTION.SUCCESS"
    return signed(ev)


def refund(out_trade_no, refund_fen, reason=""):
    db = _load(); o = db.get(out_trade_no)
    if not o:
        return {"state": "NOTFOUND"}
    if o["state"] != "SUCCESS":
        return {"state": o["state"], "error": "未支付不能退款"}
    if int(refund_fen) <= 0 or int(refund_fen) > o["amount_fen"]:
        return {"state": o["state"], "error": "退款金额越界"}
    o["state"] = "REFUNDED"; o["refund_fen"] = int(refund_fen)
    o["refund_reason"] = reason; _save(db)
    return {"state": "REFUNDED", "refund_fen": int(refund_fen)}


def query(out_trade_no):
    return _load().get(out_trade_no) or {"state": "NOTFOUND"}
'''

    common_js = '''// payment.js —— 收单适配层（沙箱，可换成真实渠道）
// 协议与后端 payment_adapter.py 一致：真 HMAC-SHA256 签名 + 状态机。
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const API_KEY = process.env.PAY_API_KEY || "%(secret)s";
const STORE = path.join(__dirname, "_pay.json");

function sign(payload) {
  const msg = Object.keys(payload).filter(k => k !== "sign" && String(payload[k]) !== "")
    .sort().map(k => `${k}=${payload[k]}`).join("&");
  return crypto.createHmac("sha256", API_KEY).update(msg).digest("hex");
}
function signed(payload) { return { ...payload, sign: sign(payload) }; }
function verify(payload) {
  const got = String(payload?.sign || "");
  const want = sign(payload || {});
  return !!got && got.length === want.length &&
    crypto.timingSafeEqual(Buffer.from(got), Buffer.from(want));
}
const load = () => { try { return JSON.parse(fs.readFileSync(STORE, "utf8")); }
                     catch { return {}; } };
const save = d => fs.writeFileSync(STORE, JSON.stringify(d, null, 1));

function createOrder(amountFen, subject = "订单", outTradeNo = "") {
  if (Number(amountFen) <= 0) throw new Error("金额必须大于 0 分");
  const db = load();
  const oid = outTradeNo || "SB" + crypto.randomBytes(8).toString("hex").toUpperCase();
  if (db[oid]) return signed(db[oid]);
  db[oid] = { out_trade_no: oid, amount_fen: Number(amountFen), subject,
              state: "NOTPAY", created: new Date().toLocaleString("zh-CN") };
  save(db);
  return signed(db[oid]);
}
function pay(outTradeNo) {
  const db = load();
  if (!db[outTradeNo]) throw new Error("没有这笔单");
  if (db[outTradeNo].state === "NOTPAY") {
    db[outTradeNo].state = "SUCCESS";
    db[outTradeNo].paid = new Date().toLocaleString("zh-CN");
    save(db);
  }
  return signed({ ...db[outTradeNo], event: "TRANSACTION.SUCCESS" });
}
function refund(outTradeNo, refundFen, reason = "") {
  const db = load(); const o = db[outTradeNo];
  if (!o) return { state: "NOTFOUND" };
  if (o.state !== "SUCCESS") return { state: o.state, error: "未支付不能退款" };
  if (refundFen <= 0 || refundFen > o.amount_fen)
    return { state: o.state, error: "退款金额越界" };
  o.state = "REFUNDED"; o.refund_fen = refundFen; o.refund_reason = reason;
  save(db);
  return { state: "REFUNDED", refund_fen: refundFen };
}
function query(outTradeNo) { return load()[outTradeNo] || { state: "NOTFOUND" }; }

module.exports = { createOrder, pay, refund, query, verify, signed };
''' % {"secret": secret}

    readme = '''# 收单接入（沙箱）

当前状态：**沙箱**（`provider = %(provider)s`，`live = %(live)s`）。
沙箱会用真 HMAC-SHA256 签名与状态机，把「下单 → 支付 → 回调 → 查单 → 退款」
整条链路在本地跑通；**不会收真钱**。

## 配置文件

`payment.json`（放在应用数据目录，或生成的项目根目录）：

```json
%(conf)s
```

真密钥**不要写进代码**，用环境变量 `PAY_API_KEY` 注入。

## 接真实渠道要改什么

只有两处（协议/状态机/验签不用动）：

1. `create_order()` 里加上渠道 HTTP 调用
   （微信 `POST /v3/pay/transactions/jsapi`；支付宝 `alipay.trade.precreate`）；
2. `_sign()` 换成渠道要求的拼串 + 签名算法
   （微信 RSA-SHA256、支付宝 RSA2），验签用渠道公钥。

⚠️ 上线前必须：把 `live` 改成 `true`、`api_key` 换成渠道密钥、
`notify_url` 换成公网可达地址，并**只用验签通过的回调**改订单状态。
''' % {"secret": secret, "provider": cfg.get("provider"), "live": cfg.get("live"),
       "conf": conf}

    s = (stack or "").lower()
    if s in ("fastapi", "flask", "python-script"):
        files = {"payment_adapter.py": common_py, "PAYMENT.md": readme}
        if s == "fastapi":
            files["payment_routes.py"] = '''# -*- coding: utf-8 -*-
"""把收单适配层挂到 FastAPI 上（沙箱可直接跑）。"""
from fastapi import FastAPI, HTTPException, Request

import payment_adapter as pay


def register(app: FastAPI) -> None:
    @app.post("/api/pay/order")
    async def create_order(body: dict):
        return pay.create_order(int(body.get("amount_fen") or 0),
                                body.get("subject") or "订单")

    @app.get("/api/pay/order/{oid}")
    async def query(oid: str):
        return pay.query(oid)

    @app.post("/api/pay/refund")
    async def refund(body: dict):
        return pay.refund(body["out_trade_no"], int(body["refund_fen"]),
                          body.get("reason") or "")

    @app.post("/api/pay/notify")
    async def notify(req: Request):
        payload = await req.json()
        if not pay.verify(payload):          # ← 防伪造回调的唯一防线
            raise HTTPException(status_code=400, detail="签名不对")
        return {"code": "SUCCESS", "message": "成功"}

    @app.post("/api/pay/sandbox/paid/{oid}")
    async def sandbox_paid(oid: str):
        """沙箱专用：模拟用户支付完成（真实渠道没有这个端点）。"""
        return pay.pay(oid)
'''
        elif s == "flask":
            files["payment_routes.py"] = '''# -*- coding: utf-8 -*-
"""把收单适配层挂到 Flask 上（沙箱可直接跑）。"""
from flask import Blueprint, jsonify, request

import payment_adapter as pay

bp = Blueprint("pay", __name__, url_prefix="/api/pay")


@bp.post("/order")
def create_order():
    b = request.get_json(silent=True) or {}
    return jsonify(pay.create_order(int(b.get("amount_fen") or 0),
                                    b.get("subject") or "订单"))


@bp.get("/order/<oid>")
def query(oid):
    return jsonify(pay.query(oid))


@bp.post("/refund")
def refund():
    b = request.get_json(silent=True) or {}
    return jsonify(pay.refund(b["out_trade_no"], int(b["refund_fen"]),
                              b.get("reason") or ""))


@bp.post("/notify")
def notify():
    payload = request.get_json(silent=True) or {}
    if not pay.verify(payload):              # ← 防伪造回调的唯一防线
        return jsonify(code="FAIL", message="签名不对"), 400
    return jsonify(code="SUCCESS", message="成功")


@bp.post("/sandbox/paid/<oid>")
def sandbox_paid(oid):
    """沙箱专用：模拟用户支付完成（真实渠道没有这个端点）。"""
    return jsonify(pay.pay(oid))
'''
        return files
    if s in ("node-express",):
        return {"payment.js": common_js,
                "payment_routes.js": '''// 把收单挂在 Express 上（沙箱可直接跑）
import express from "express";
import * as pay from "./payment.js";

export const payRouter = express.Router();

payRouter.post("/order", (req, res) =>
  res.json(pay.createOrder(Number(req.body?.amount_fen || 0), req.body?.subject)));

payRouter.get("/order/:oid", (req, res) => res.json(pay.query(req.params.oid)));

payRouter.post("/refund", (req, res) =>
  res.json(pay.refund(req.body.out_trade_no, Number(req.body.refund_fen),
                      req.body.reason)));

payRouter.post("/notify", (req, res) => {
  // ← 防伪造回调的唯一防线
  if (!pay.verify(req.body)) return res.status(400).json({ code: "FAIL" });
  return res.json({ code: "SUCCESS" });
});

// 沙箱专用：模拟用户支付完成
payRouter.post("/sandbox/paid/:oid", (req, res) =>
  res.json(pay.pay(req.params.oid)));
''',
                "PAYMENT.md": readme}
    return {"PAYMENT.md": readme,
            "payment.config.json": json.dumps(
                {"provider": cfg.get("provider"), "live": cfg.get("live"),
                 "currency": cfg.get("currency"), "sandbox": sb},
                ensure_ascii=False, indent=2) + "\n"}


def status() -> dict:
    """给界面/工作流看的整体状态（**不含任何密钥明文**）。"""
    cfg = load_config()
    p = get_provider("", cfg)
    st = p.status()
    st["config_path"] = config_path()
    st["fallback"] = cfg.get("_fallback") or ""
    sb = cfg.get("sandbox") or {}
    st["sandbox_mch"] = mask(sb.get("mch_id", ""))
    for k in ("wechat", "alipay"):
        c = cfg.get(k) or {}
        st["%s_configured" % k] = bool(str(c.get("app_id") or "").strip())
    return st


# ==========================================================================
# 自检（全离线；不碰真实渠道、不写真实目录）
# ==========================================================================
def selftest() -> int:
    import tempfile

    passed = failed = 0

    def ck(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  ok   %s" % name)
        else:
            failed += 1
            print("  FAIL %s %s" % (name, extra))

    print("== payment 自检 ==")
    old = os.environ.get("PASM_STUDIO_DIR")
    tmp = tempfile.mkdtemp(prefix="pasm_pay_")
    try:
        os.environ["PASM_STUDIO_DIR"] = tmp
        ck("数据目录被隔离（不写用户真实目录）", data_dir() == tmp)

        # 金额
        ck("元→分 12.30 = 1230", yuan_to_fen("12.30") == 1230)
        ck("元→分 0.01 = 1", yuan_to_fen("0.01") == 1)
        ck("元→分 3 = 300（整数按元）", yuan_to_fen(3) == 300)
        ck("分→元 1230 = 12.30", fen_to_yuan(1230) == "12.30")
        for bad in ("1.234", "abc", "-5", ""):
            try:
                yuan_to_fen(bad)
                ck("非法金额 %r 被拒" % bad, False)
            except ValueError:
                ck("非法金额 %r 被拒" % bad, True)

        # 脱敏
        ck("脱敏不回显全文", mask("SANDBOX_MCH") == "SAND*****CH", mask("SANDBOX_MCH"))
        ck("空值脱敏为空", mask("") == "")

        # 默认配置 = 沙箱 + 不真收钱
        cfg = load_config()
        ck("出厂默认 provider=sandbox", cfg["provider"] == "sandbox")
        ck("出厂默认 live=False（不允许真收钱）", cfg["live"] is False)
        ck("配置文件真落盘", os.path.exists(config_path()))

        # 状态机全链路
        p = get_provider()
        ck("默认取到沙箱 provider", p.name == "sandbox")
        o = p.create_order("", 1230, "测试订单")
        oid = o["out_trade_no"]
        ck("下单返回带签名的订单", bool(o.get("sign")) and o["state"] == "NOTPAY")
        ck("下单金额是分", o["amount_fen"] == 1230 and o["amount_yuan"] == "12.30")
        ck("幂等：同单号重复下单返回原单",
           p.create_order(oid, 1230, "x").get("idempotent") is True)
        ck("未支付不能退款（会明确拒绝）",
           "error" in p.refund(oid, 100))
        ev = p.pay(oid)
        ck("模拟支付后状态 SUCCESS", p.query_order(oid)["state"] == "SUCCESS")
        ck("回调验签通过（真 HMAC）", p.verify_notify(ev) is True)
        bad = dict(ev)
        bad["amount_fen"] = 1                     # 篡改金额
        try:
            p.verify_notify(bad)
            ck("篡改过的回调被拒收", False)
        except SignatureError:
            ck("篡改过的回调被拒收", True)
        try:
            p.verify_notify({})
            ck("没有签名的回调被拒收", False)
        except SignatureError:
            ck("没有签名的回调被拒收", True)
        r = p.refund(oid, 1230)
        ck("全额退款后状态 REFUNDED", r["state"] == "REFUNDED", r)
        ck("超退被拒（金额越界）", "error" in p.refund(oid, 1230))
        ck("订单重启不丢（真落盘）", os.path.exists(store_path()))

        # 明确拒绝：真实渠道没配齐
        for nm in ("wechat", "alipay"):
            q = get_provider(nm)
            ok, why = q.ready()
            ck("%s 未配凭据时 ready=False" % nm, ok is False and bool(why), why)
            try:
                q.create_order("X1", 100, "t")
                ck("%s 未配凭据时下单直接拒绝" % nm, False)
            except NotConfigured:
                ck("%s 未配凭据时下单直接拒绝" % nm, True)

        # 不许悄悄降级：填了凭据但 live=false 也要拒
        c2 = load_config()
        c2["wechat"].update({"mch_id": "1900000109", "app_id": "wx123",
                             "api_v3_key": "k" * 32, "cert_serial": "ABC",
                             "private_key_path": __file__})
        q2 = WeChatProvider(c2)
        ok2, why2 = q2.ready()
        ck("微信凭据齐但 live=false → 仍拒绝（防误收真钱）",
           ok2 is False and "live" in why2, why2)
        c2["live"] = True
        try:
            q2.create_order("X2", 100, "t")
            ck("live=true 且凭据齐 → 走到真接口缺口处报错（不是静默成功）", False)
        except NotConfigured as e:
            ck("live=true 且凭据齐 → 走到真接口缺口处报错（不是静默成功）",
               "尚未接真实 HTTP" in str(e))
            ck("真接口缺口报错里的密钥已脱敏（不回显全文）",
               ("k" * 20) not in str(e), str(e)[:120])
        ck("ready 的原因文本里没有密钥明文", ("k" * 20) not in str(why2))

        # 未知 provider 回落沙箱但要能看见
        c3 = dict(load_config())
        c3["provider"] = "paypal"
        pu = get_provider("", c3)
        ck("未知渠道回落沙箱（不会真收钱）", pu.name == "sandbox")

        # status 不含明文密钥
        st = status()
        ck("status 里没有密钥明文",
           "api_key" not in json.dumps(st) and st["provider"] == "sandbox")

        # 适配层文件
        for stack in ("fastapi", "flask", "node-express"):
            f = adapter_files(stack, cfg)
            ck("适配层给 %s 产出了文件" % stack, bool(f))
            body = "".join(f.values())
            ck("%s 适配层带真验签逻辑（不是空壳）" % stack, "verify" in body)
            ck("%s 适配层密钥走环境变量（可换真密钥，不写死）" % stack,
               "PAY_API_KEY" in body)
        fg = adapter_files("static", cfg)
        ck("无框架时也给接入说明 + 配置模板",
           "PAYMENT.md" in fg and "payment.config.json" in fg)
        ck("接入说明点明「接真渠道只需改两处」", "两处" in fg["PAYMENT.md"])
        ck("配置模板里 live 默认 false（明示沙箱）",
           json.loads(fg["payment.config.json"])["live"] is False)
    finally:
        os.environ.pop("PASM_STUDIO_DIR", None)
        if old is not None:
            os.environ["PASM_STUDIO_DIR"] = old
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)

    print("payment 自检：%d 通过 / %d 失败" % (passed, failed))
    return failed


if __name__ == "__main__":
    import sys
    sys.exit(1 if selftest() else 0)
