# -*- coding: utf-8 -*-
"""connector_hub.py —— 第三方接入中枢（v0.30.14）

一个入口管四个通道：**飞书 / Discord / 微信 / Webhook**，方向都是双向
（入站 → 交给同一个 handler → 结果回给同一个会话），对齐 BaiLongma 的
`src/social/index.js`（start / restart / status）。

    hub = ConnectorHub(cfg=self.cfg, handler=self._bridge_handle, on_log=log)
    hub.start_all()                 # 按配置自动起（没配的安静跳过，不报错）
    hub.statuses()                  # [{"platform": "飞书", "state": "connected", ...}]
    hub.push("跑完了", title="PASM")  # 往"所有已配好的出站通道"推一份
    hub.restart("feishu")           # 设置里改完凭据 → 热重启，不用重启应用

## 三条边界

1. **没配就安静跳过**，不抛、不弹窗 —— 用户没接的通道不该在启动日志里刷红。
2. **入站一定要验签/验 token**：收消息等于"远程指挥这台电脑"，比发消息危险得多。
   四个连接器都只绑 `127.0.0.1`（长连接除外，那是我们主动连出去）。
3. **状态必须可查**：`statuses()` 给出每个通道"现在到底通没通"，
   这是用户能信任多渠道路由的前提（否则失败时只看到一句笼统的"没发出去"）。
"""
from __future__ import annotations

import importlib
import logging
from typing import Callable, Optional

PLATFORMS = ("feishu", "discord", "wechat", "webhook")

#: 平台 → 中文名（界面上要用）
LABEL = {"feishu": "飞书", "discord": "Discord",
         "wechat": "微信", "webhook": "Webhook"}


def _mod(name: str):
    """懒加载连接器模块（省启动时间；漏在 spec 里会在 frozen 版静默失败 → 已声明）。"""
    return importlib.import_module("connector_" + name)


class ConnectorHub:
    def __init__(self, cfg: Optional[dict] = None,
                 handler: Optional[Callable[[str], str]] = None,
                 on_log: Optional[Callable[[str], None]] = None):
        self.cfg = cfg
        self.handler = handler
        self.on_log = on_log
        self.bridges: dict = {}
        self.started: dict = {}

    def log(self, m: str) -> None:
        try:
            logging.info("[connector] %s", m)
        except Exception:                                    # noqa: BLE001
            pass
        if self.on_log:
            try:
                self.on_log(m)
            except Exception:                                # noqa: BLE001
                pass

    # ---------------- 启停 ----------------
    def start_all(self) -> dict:
        for p in PLATFORMS:
            try:
                self._start_one(p)
            except Exception as ex:                          # noqa: BLE001
                self.started[p] = False
                self.log("启动 %s 通道失败：%s" % (LABEL.get(p, p), ex))
        return dict(self.started)

    def _start_one(self, platform: str) -> bool:
        m = _mod(platform)
        if hasattr(m, "configured") and not m.configured(self.cfg):
            self.started[platform] = False
            return False
        if platform == "feishu":
            b = m.FeishuBridge(self.cfg, handler=self.handler, on_log=self.on_log)
        elif platform == "discord":
            b = m.DiscordBridge(self.cfg, handler=self.handler, on_log=self.on_log)
        elif platform == "wechat":
            b = m.WechatBridge(self.cfg, handler=self.handler, on_log=self.on_log)
        else:
            b = m.WebhookBridge(self.cfg, handler=self.handler, on_log=self.on_log)
        self.bridges[platform] = b
        ok = bool(b.start())
        self.started[platform] = ok
        return ok

    def stop_all(self) -> None:
        for p, b in list(self.bridges.items()):
            try:
                b.stop()
            except Exception:                                # noqa: BLE001
                pass
            self.started[p] = False

    def restart(self, platform: str) -> bool:
        """设置里改完凭据 → 热重启该通道（BaiLongma 的 restartConnector 同款）。"""
        if platform not in PLATFORMS:
            return False
        old = self.bridges.pop(platform, None)
        if old is not None:
            try:
                old.stop()
            except Exception:                                # noqa: BLE001
                pass
        try:
            return self._start_one(platform)
        except Exception as ex:                              # noqa: BLE001
            self.log("热重启 %s 失败：%s" % (LABEL.get(platform, platform), ex))
            return False

    # ---------------- 状态 ----------------
    def statuses(self) -> list:
        """每个通道一行：是否配置、是否在跑、什么状态、报什么错。"""
        out = []
        for p in PLATFORMS:
            row = {"platform": p, "label": LABEL.get(p, p), "configured": False,
                   "running": False, "state": "idle", "note": ""}
            try:
                m = _mod(p)
                row["configured"] = bool(getattr(m, "configured")(self.cfg))
            except Exception as ex:                          # noqa: BLE001
                row["note"] = "模块不可用：%s" % ex
                out.append(row)
                continue
            b = self.bridges.get(p)
            if b is not None:
                try:
                    row["state"] = str(b.status())
                except Exception:                            # noqa: BLE001
                    row["state"] = "unknown"
            row["running"] = bool(self.started.get(p))
            if not row["configured"]:
                row["note"] = "未配置"
            elif not row["running"]:
                row["note"] = {"feishu": "接收方式为「关闭」或未够条件",
                               "discord": "接收方式为「关闭」",
                               "wechat": "接收方式为「关闭」",
                               "webhook": "入站未开启"}.get(p, "未启动")
            out.append(row)
        return out

    def status_text(self) -> str:
        icon = {"connected": "🟢", "connecting": "🟡", "reconnecting": "🟡",
                "error": "🔴", "idle": "⚪"}
        lines = ["🔗 **第三方接入状态**（入站 = 你在这里发消息，它就能指挥这台电脑）"]
        for r in self.statuses():
            mark = icon.get(r["state"], "⚪")
            lines.append("· %s **%s**：%s%s"
                         % (mark, r["label"], r["state"],
                            "（%s）" % r["note"] if r["note"] else ""))
        lines.append("")
        lines.append("· 四个通道都是**双向**的：入站消息统一交给主循环，结果回给同一个会话")
        lines.append("· 详细配置见 ⚙ 设置 → **🔗 接入**；每条通道里都有「🔎 测试连接」")
        lines.append("· ⚠️ 个人微信（ClawBot 那种非官方协议）**刻意不接**："
                     "违反微信条款、有封号风险 —— 用企业微信群机器人或公众号替代")
        return "\n".join(lines)

    # ---------------- 出站（主动推） ----------------
    def push(self, text: str, title: str = "PASM") -> list:
        """往**所有已配好的出站通道**推一份。返回成功渠道的中文名列表。

        每个通道单独 try：一条通道坏了不能拖累其它通道（多通道的意义就在这）。
        """
        sent, fails = [], []
        body = (text or "").strip()
        if not body:
            return []
        # ① 飞书应用（有默认会话才推；否则会不知道发给谁）
        try:
            import connector_feishu as F
            s = F.settings(self.cfg)
            if F.configured(self.cfg) and s["default_chat"]:
                F.send_text("%s\n%s" % (title, body), cfg=self.cfg)
                sent.append("飞书（应用）")
        except Exception as ex:                              # noqa: BLE001
            fails.append("飞书：%s" % ex)
        # ② Discord（有默认频道才推）
        try:
            import connector_discord as D
            s = D.settings(self.cfg)
            if D.configured(self.cfg) and s["default_channel"]:
                D.send_text(s["default_channel"], "%s\n%s" % (title, body), cfg=self.cfg)
                sent.append("Discord")
        except Exception as ex:                              # noqa: BLE001
            fails.append("Discord：%s" % ex)
        # ③ 微信公众号（客服消息；48h 窗口）
        try:
            import connector_wechat as W
            s = W.settings(self.cfg)
            if W.configured(self.cfg) and (s["default_openid"] or s["wecom_bot"]):
                if s["default_openid"]:
                    W.send_text(s["default_openid"], "%s\n%s" % (title, body), cfg=self.cfg)
                else:
                    W.send_wecom_bot("%s\n%s" % (title, body), cfg=self.cfg)
                sent.append("微信")
        except Exception as ex:                              # noqa: BLE001
            fails.append("微信：%s" % ex)
        # ④ 通用 Webhook 出站
        try:
            import connector_webhook as WH
            if WH.outbound_ready(self.cfg):
                r = WH.send(body, cfg=self.cfg, title=title)
                sent.append(r["label"])
        except Exception as ex:                              # noqa: BLE001
            fails.append("Webhook：%s" % ex)
        # ⑤ 老的 remote_bridge（Telegram / 群机器人 / Server酱 / Bark）
        #    注意：飞书群机器人 webhook 与飞书应用是**两条路**，都配了会收到两条
        #    → 飞书应用可用时，跳过飞书群里那条 webhook，避免重复推送。
        try:
            import remote_bridge as RB
            s = RB.settings(self.cfg)
            if s["webhook"] and RB._kind_of(s["webhook"]) == "feishu" and \
                    "飞书（应用）" in sent:
                pass
            else:
                r = RB.notify(body, title=title, cfg=self.cfg)
                sent.append(r.replace("📤 已推送到 ", "").split(" ✅")[0])
        except Exception as ex:                              # noqa: BLE001
            msg = str(ex)
            if "还没配置" not in msg:
                fails.append("远程桥：%s" % msg)
        if fails:
            self.log("推送部分失败：%s" % "；".join(fails))
        return sent

    def push_text(self, text: str, title: str = "PASM") -> str:
        """给用户看的一句话结果（失败也要说清哪条没成）。"""
        sent = self.push(text, title=title)
        if not sent:
            return ("📤 没有可用的推送通道。去 ⚙ 设置 → 🔗 接入 配一条"
                    "（飞书 / Discord / 微信 / Webhook 任选）。")
        return "📤 已推送到 %s ✅" % "、".join(sent)


def selftest() -> int:
    """离线自检：不联网、不真正起服务，只验"没配就安静跳过"与状态汇总。"""
    fails, ok = [], 0

    def chk(label, cond, extra=""):
        nonlocal ok
        if cond:
            ok += 1
        else:
            fails.append("%s %s" % (label, extra))

    hub = ConnectorHub(cfg={}, handler=lambda t: "ok", on_log=None)
    r = hub.start_all()
    chk("空配置下四个通道都安静跳过（不抛异常）",
        isinstance(r, dict) and len(r) == 4 and not any(r.values()), r)
    st = hub.statuses()
    chk("状态汇总给四行（界面/对话都要能显示）", len(st) == 4, st)
    chk("未配置的通道被标成「未配置」",
        all(x["note"] == "未配置" for x in st), [x["note"] for x in st])
    txt = hub.status_text()
    chk("状态文案含四个平台名",
        all(k in txt for k in ("飞书", "Discord", "微信", "Webhook")), txt[:80])
    chk("状态文案说清 ClawBot 为什么不接", "封号" in txt)
    chk("没配通道时推送返回空 + 明确指引",
        hub.push("x") == [] and "接入" in hub.push_text("x"))
    chk("热重启未知平台返回 False（反例）", hub.restart("telegram") is False)

    # 只配一个通道时，别的通道不许被牵连
    hub2 = ConnectorHub(cfg={"webhook_in_token": "t", "webhook_in_mode": "on"},
                        handler=lambda t: "ok")
    r2 = hub2.start_all()
    chk("只配 webhook 时：webhook 通道尝试启动、其余仍跳过",
        r2.get("feishu") is False and r2.get("discord") is False
        and r2.get("wechat") is False, r2)
    st2 = hub2.statuses()
    w = [x for x in st2 if x["platform"] == "webhook"][0]
    chk("webhook 已被认成「已配置」", w["configured"] is True, w)
    hub2.stop_all()
    chk("stop_all 后全部不在跑", not any(hub2.started.values()), hub2.started)

    if fails:
        print("connector_hub 自检失败 %d 项：" % len(fails))
        for f in fails:
            print("  ✗", f)
        return 1
    print("connector_hub 自检通过（%d 项）" % ok)
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(selftest())
