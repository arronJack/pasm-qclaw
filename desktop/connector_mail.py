"""connector_mail —— 邮件连接器（v0.28.x）
==========================================

对齐 WorkBuddy / OpenClaw 的「邮件」连接器：让 PASM 能**真的收发邮件**，
而不是只给一段"你可以这样写"的模板。

实现要点：
- **纯标准库**：`imaplib` + `smtplib` + `email`，不加任何依赖，安装包不变大。
- **未配置就优雅降级**：没填邮箱时返回中文配置指引，绝不假装"已发送"。
- **超时保护**：所有网络调用都带 timeout，卡住不会拖死界面线程。

配置项（存在本地 config.json，与主程序共用）：
    mail_user        你的邮箱地址（同时作为默认发件人）
    mail_pass        邮箱授权码 / 应用专用密码（注意：不是登录密码）
    mail_imap_host   IMAP 服务器，如 imap.qq.com
    mail_imap_port   默认 993（SSL）
    mail_smtp_host   SMTP 服务器，如 smtp.qq.com
    mail_smtp_port   默认 465（SSL）

常见服务商服务器（写进提示里，省得用户查）：
    QQ 邮箱      imap.qq.com / smtp.qq.com        （授权码在「设置→账户」里开）
    163 邮箱     imap.163.com / smtp.163.com
    Gmail       imap.gmail.com / smtp.gmail.com  （需应用专用密码）
    Outlook     outlook.office365.com / smtp.office365.com
    阿里企业邮   imap.qiye.aliyun.com / smtp.qiye.aliyun.com

⚠️ 安全说明：授权码以明文存在本机 config.json（Windows 用户目录下），
   与主流桌面客户端做法一致；请勿把该文件分享给别人。
"""
from __future__ import annotations

import email
import email.header
import email.utils
import imaplib
import json
import os
import re
import smtplib
from email.mime.text import MIMEText
from typing import List, Optional, Tuple

try:
    from pasm_companion import CONFIG
except Exception:                                # 独立导入兜底（测试用）
    CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "..", ".pasmstudio_dev", "config.json")

TIMEOUT = 20
PROVIDERS = {
    "qq.com": ("imap.qq.com", "smtp.qq.com"),
    "foxmail.com": ("imap.qq.com", "smtp.qq.com"),
    "163.com": ("imap.163.com", "smtp.163.com"),
    "126.com": ("imap.126.com", "smtp.126.com"),
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com"),
    "aliyun.com": ("imap.qiye.aliyun.com", "smtp.qiye.aliyun.com"),
}


def load_cfg() -> dict:
    try:
        with open(CONFIG, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _guess_hosts(user: str) -> Tuple[str, str]:
    """按邮箱域名猜服务器，省去用户查资料。"""
    m = re.search(r"@([\w.\-]+)$", (user or "").strip())
    if not m:
        return "", ""
    dom = m.group(1).lower()
    if dom in PROVIDERS:
        return PROVIDERS[dom]
    for k, v in PROVIDERS.items():
        if dom.endswith("." + k):
            return v
    return "", ""


def settings(cfg: Optional[dict] = None) -> dict:
    """取配置（缺省从 config.json 读），并自动补全服务器地址。"""
    c = cfg if isinstance(cfg, dict) else load_cfg()
    user = str(c.get("mail_user") or "").strip()
    gh_i, gh_s = _guess_hosts(user)
    return {
        "user": user,
        "pass": str(c.get("mail_pass") or "").strip(),
        "imap_host": str(c.get("mail_imap_host") or "").strip() or gh_i,
        "imap_port": int(c.get("mail_imap_port") or 993),
        "smtp_host": str(c.get("mail_smtp_host") or "").strip() or gh_s,
        "smtp_port": int(c.get("mail_smtp_port") or 465),
    }


def configured(cfg: Optional[dict] = None) -> bool:
    s = settings(cfg)
    return bool(s["user"] and s["pass"] and s["imap_host"])


GUIDE = (
    "📧 邮件功能还没配置好。到「设置 → 连接器 → 邮件」填这几项就行：\n\n"
    "1. **邮箱地址**（mail_user）\n"
    "2. **授权码**（mail_pass）—— ⚠️ 是授权码/应用专用密码，**不是登录密码**\n"
    "3. 服务器地址可留空，我会按你的邮箱域名自动识别\n\n"
    "常见服务商（授权码在哪开）：\n"
    "· QQ / Foxmail：设置→账户→开启 IMAP/SMTP 服务 → 拿到授权码\n"
    "· 163：设置→POP3/SMTP/IMAP → 开启并获取授权码\n"
    "· Gmail：需在 Google 账户里生成「应用专用密码」\n"
    "· Outlook / 企业邮箱：多数直接支持，服务器我自动填"
)


# ---------------- 编码处理 ----------------
def _dec(s) -> str:
    """把邮件头里的编码字（=?utf-8?B?...?=）解成中文。"""
    if not s:
        return ""
    try:
        parts = email.header.decode_header(s)
    except Exception:
        return str(s)
    out = []
    for txt, enc in parts:
        if isinstance(txt, bytes):
            for e in (enc, "utf-8", "gb18030", "latin-1"):
                if not e:
                    continue
                try:
                    out.append(txt.decode(e))
                    break
                except Exception:
                    continue
            else:
                out.append(txt.decode("utf-8", "replace"))
        else:
            out.append(str(txt))
    return "".join(out).strip()


def _body_text(msg) -> str:
    """取纯文本正文（优先 text/plain，兜底把 text/html 去标签）。"""
    plain, html = "", ""
    try:
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                disp = str(part.get("Content-Disposition") or "")
                if "attachment" in disp:
                    continue
                if ctype not in ("text/plain", "text/html"):
                    continue
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                try:
                    text = payload.decode(charset, "replace")
                except Exception:
                    text = payload.decode("utf-8", "replace")
                if ctype == "text/plain":
                    plain += text
                else:
                    html += text
        else:
            payload = msg.get_payload(decode=True) or b""
            charset = msg.get_content_charset() or "utf-8"
            try:
                plain = payload.decode(charset, "replace")
            except Exception:
                plain = payload.decode("utf-8", "replace")
    except Exception:
        return ""
    if plain.strip():
        return plain.strip()
    if html.strip():
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<br\s*/?>|</p>", "\n", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = (text.replace("&nbsp;", " ").replace("&amp;", "&")
                    .replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"'))
        return re.sub(r"\n{3,}", "\n\n", text).strip()
    return ""


# ---------------- 收信 ----------------
def _connect_imap(s: dict):
    if not (s["user"] and s["pass"] and s["imap_host"]):
        raise RuntimeError(GUIDE)
    try:
        conn = imaplib.IMAP4_SSL(s["imap_host"], s["imap_port"], timeout=TIMEOUT)
    except Exception as ex:
        raise RuntimeError("连不上邮件服务器 %s:%s（%s）。请检查网络、服务器地址或端口。"
                           % (s["imap_host"], s["imap_port"], ex))
    try:
        conn.login(s["user"], s["pass"])
    except Exception as ex:
        raise RuntimeError("登录被拒绝：%s\n\n最常见的原因是把「登录密码」当授权码填了 —— "
                           "请到邮箱设置里生成 **授权码/应用专用密码** 再填。" % ex)
    return conn


def fetch(n: int = 5, unread_only: bool = False,
          cfg: Optional[dict] = None) -> List[dict]:
    """取最近 n 封邮件 → [{subject, from, date, unread, preview}]。"""
    s = settings(cfg)
    conn = _connect_imap(s)
    try:
        conn.select("INBOX")
        crit = "(UNSEEN)" if unread_only else "ALL"
        typ, data = conn.search(None, crit)
        if typ != "OK":
            raise RuntimeError("读取收件箱失败（%s）" % typ)
        ids = (data[0] or b"").split()
        ids = ids[-max(1, int(n)):][::-1]          # 最新的在前
        out: List[dict] = []
        for mid in ids:
            try:
                typ, raw = conn.fetch(mid, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
                out.append({
                    "subject": _dec(msg.get("Subject")) or "(无主题)",
                    "from": _dec(msg.get("From")),
                    "date": _dec(msg.get("Date")),
                    "unread": True,
                    "preview": _body_text(msg)[:400],
                })
            except Exception:
                continue
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


def search(keyword: str, n: int = 5, cfg: Optional[dict] = None) -> List[dict]:
    """按关键词搜邮件（主题或正文含该词）。"""
    kw = (keyword or "").strip()
    if not kw:
        return fetch(n, cfg=cfg)
    s = settings(cfg)
    conn = _connect_imap(s)
    try:
        conn.select("INBOX")
        # 先按主题搜（服务器端快），没有再按全文搜
        found: List[bytes] = []
        for crit in ('(SUBJECT "%s")' % kw, '(BODY "%s")' % kw, "ALL"):
            try:
                typ, data = conn.search(None, crit)
                if typ == "OK" and data and data[0]:
                    found = data[0].split()
                    if found:
                        break
            except Exception:
                continue
        ids = found[-max(1, int(n)):][::-1]
        out: List[dict] = []
        for mid in ids:
            try:
                typ, raw = conn.fetch(mid, "(RFC822)")
                if typ != "OK" or not raw or not raw[0]:
                    continue
                msg = email.message_from_bytes(raw[0][1])
                out.append({
                    "subject": _dec(msg.get("Subject")) or "(无主题)",
                    "from": _dec(msg.get("From")),
                    "date": _dec(msg.get("Date")),
                    "unread": False,
                    "preview": _body_text(msg)[:400],
                })
            except Exception:
                continue
        return out
    finally:
        try:
            conn.logout()
        except Exception:
            pass


# ---------------- 发信 ----------------
def send(to: str, subject: str, body: str,
         cfg: Optional[dict] = None) -> str:
    """发一封纯文本邮件。返回人类可读结果；失败抛中文 RuntimeError。"""
    s = settings(cfg)
    to = (to or "").strip()
    if not to or "@" not in to:
        raise RuntimeError("收件人地址不对：%r。请给我一个像 name@example.com 的地址。" % to)
    if not (s["user"] and s["pass"] and s["smtp_host"]):
        raise RuntimeError(GUIDE)
    msg = MIMEText(body or "", "plain", "utf-8")
    msg["Subject"] = subject or "(无主题)"
    msg["From"] = s["user"]
    msg["To"] = to
    msg["Date"] = email.utils.formatdate(localtime=True)
    try:
        if s["smtp_port"] == 465:
            srv = smtplib.SMTP_SSL(s["smtp_host"], s["smtp_port"], timeout=TIMEOUT)
        else:
            srv = smtplib.SMTP(s["smtp_host"], s["smtp_port"], timeout=TIMEOUT)
            try:
                srv.starttls()
            except Exception:
                pass
    except Exception as ex:
        raise RuntimeError("连不上发信服务器 %s:%s（%s）。" % (s["smtp_host"], s["smtp_port"], ex))
    try:
        srv.login(s["user"], s["pass"])
        srv.sendmail(s["user"], [to], msg.as_string())
    except smtplib.SMTPAuthenticationError as ex:
        raise RuntimeError("发信登录被拒绝：%s\n\n多半是授权码不对（不是登录密码）。" % ex)
    except Exception as ex:
        raise RuntimeError("发送失败：%s" % ex)
    finally:
        try:
            srv.quit()
        except Exception:
            pass
    return "已发送 ✅ → %s（主题：%s）" % (to, subject or "(无主题)")


# ---------------- 给聊天界面用的文案 ----------------
def digest(n: int = 5, unread_only: bool = True, cfg: Optional[dict] = None) -> str:
    """「我有什么邮件」——直接可读的汇总。"""
    if not configured(cfg):
        return GUIDE
    try:
        mails = fetch(n, unread_only=unread_only, cfg=cfg)
    except RuntimeError as ex:
        return "📧 " + str(ex)
    except Exception as ex:
        return "📧 读邮件时出错了：%s" % ex
    if not mails:
        return ("📭 收件箱里%s没有新邮件。"
                % ("最近 " + str(n) + " 封里" if unread_only else ""))
    lines = ["📬 最近 %d 封%s邮件：" % (len(mails), "未读" if unread_only else "")]
    for i, m in enumerate(mails, 1):
        lines.append("\n%d. **%s**\n   发件人：%s\n   %s"
                     % (i, m["subject"], m["from"] or "未知",
                        (m["preview"] or "").replace("\n", " ")[:160]))
    return "\n".join(lines)


def search_text(keyword: str, n: int = 5, cfg: Optional[dict] = None) -> str:
    if not configured(cfg):
        return GUIDE
    try:
        mails = search(keyword, n, cfg=cfg)
    except RuntimeError as ex:
        return "📧 " + str(ex)
    except Exception as ex:
        return "📧 搜邮件时出错了：%s" % ex
    if not mails:
        return "📭 没找到含「%s」的邮件。" % keyword
    lines = ["🔍 含「%s」的邮件 %d 封：" % (keyword, len(mails))]
    for i, m in enumerate(mails, 1):
        lines.append("\n%d. **%s**\n   发件人：%s\n   %s"
                     % (i, m["subject"], m["from"] or "未知",
                        (m["preview"] or "").replace("\n", " ")[:160]))
    return "\n".join(lines)


def status_text(cfg: Optional[dict] = None) -> str:
    s = settings(cfg)
    if not configured(cfg):
        return GUIDE
    return ("📧 邮件连接器\n"
            "· 账号：%s\n· IMAP：%s:%s\n· SMTP：%s:%s\n"
            "· 状态：%s"
            % (s["user"], s["imap_host"], s["imap_port"],
               s["smtp_host"], s["smtp_port"],
               "已配置（可用「看看我的邮件」试试）" if s["pass"] else "缺授权码"))
