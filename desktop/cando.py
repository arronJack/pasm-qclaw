# -*- coding: utf-8 -*-
"""cando —— v0.27.1 能力判定 broker（非预设机制）。

用户给出需求后，不再假装"什么都能做"，而是先判定：
  DO       → 有真实工具/能力直接做（由主路由接管执行）
  LEARNED  → 没有直接工具，但自学过相关知识 → 用学到的真做（写脚本跑真验证）
  LEARN    → 不会，但可上网学 → 学完再做（学不会就如实说学不会）
  NO       → 做不了（硬件控制/需要管理员特权/纯物理世界）→ 说明真实原因
  CHAT     → 不是执行类请求 → 普通聊天

判定规则全部可解释，判定结果附 reason，用户随时能问"为什么你说你不能做"。
"""
import re
import os

# 执行类请求的动作词（ imPrompt：真活才有资格进判定，其余回聊天）
_ACT = (r"(?:帮我|给我|替我|把|将|去|给我把|协助|执行|操作|运行|跑一下|弄|搞|处理)"
        r"[^，。！？\n]{0,40}")

# 真实工具已覆盖的能力（交给主路由，不走 broker）
_DONE_HINTS = (
    r"打开|读一下|看看|总结|分析|翻译|改写|删除|删掉|清理|缓存|垃圾|表格|数据|"
    r"截图|图片|视频|漫剧|脚本|代码|程序|网站|网页|xls|excel|doc|ppt|"
    r"浏览器|搜索|查一?下|病毒|杀毒|查杀|安全体检|体检|防火墙"
)

# 物理世界/特权边界（无论学不学都做不到，必须说实话）
_NO_HINTS = (
    (r"按下?物理|关机|重启电脑|重启系统|断网|连wifi|连wi-fi|蓝牙|打印出来|"
     r"扫描仪|摄像头拍|摸|拿|寄快递|打电话发语音给别人", "需要物理动作或硬件控制，我的世界只有这台电脑的软件层"),
    (r"支付|转账|付款|下单买|帮我买", "涉及你的钱，这类操作我不代做（安全底线），你可以告诉我链接我帮你读"),
    (r"破解|盗版|外挂|爬付费|绕过.{0,4}验证", "这属于违法或破坏性操作，我不会做"),
)


def assess(text: str, learned_search=None) -> dict:
    """返回 {verdict, reason, learned?}。
    learned_search: callable(query) -> list[dict]（knowledge.search_snippets）。"""
    t = (text or "").strip()
    if not t:
        return {"verdict": "CHAT", "reason": "空请求"}
    # 0) 明确做不到的硬边界 → 先说真话
    for pat, why in _NO_HINTS:
        if re.search(pat, t, re.I):
            return {"verdict": "NO", "reason": why}
    # 1) 主路由真实工具已覆盖 → 直接做
    if re.search(_DONE_HINTS, t, re.I):
        return {"verdict": "DO", "reason": "我有直接工具可以做这件事"}
    # 2) 是执行类请求吗？
    m = re.search(_ACT, t)
    if not m:
        return {"verdict": "CHAT", "reason": "不是执行类请求"}
    # 3) 自学过的知识能帮上吗？
    if learned_search is not None:
        try:
            hits = learned_search(t) or []
        except Exception:
            hits = []
        if hits:
            titles = "、".join(h.get("title", "") for h in hits[:2])
            return {"verdict": "LEARNED", "reason": f"我自学过相关内容（{titles}），"
                    "可以用学到的知识写脚本真跑一遍", "learned": hits}
    return {"verdict": "LEARN", "reason": "我现在还没有做这件事的直接能力，"
            "但这类事通常能靠写脚本实现——我可以先上网自学，学完真做；"
            "学不会或没把握我会直接告诉你，不会装做完了"}


def learned_context(hits: list, max_chars: int = 2400) -> str:
    """把学到的知识拼成脚本编写上下文（学以致用的真材实料，不是摆设）。"""
    parts = []
    for h in (hits or [])[:2]:
        title = h.get("title", "")
        snippet = h.get("snippet", "") or h.get("bullets", "")
        if isinstance(snippet, list):
            snippet = "\n".join(snippet)
        parts.append(f"《{title}》\n{str(snippet)[:max_chars // 2]}")
    return "\n\n".join(parts)


def is_scriptable(text: str) -> bool:
    """这类需求能否用"写脚本+运行"的方式真实完成
    （文件/批量/系统信息/自动化/浏览器/安全体检等不定性任务，v0.27.2 放宽）。"""
    return bool(re.search(
        r"文件|文件夹|批量|重命名|整理|归档|备份|复制|移动|排序|去重|统计.{0,6}(文件|行|字)|"
        r"监控|定时|自动化|列目录|磁盘|空间|进程|网络|IP|下载|转格式|压缩|解压|"
        r"拆分|合并|编码|转换|水印|缩略图|爬|抓取|签到|提醒|"
        r"浏览器|搜索|病毒|杀毒|查杀|木马|安全(检查|体检|状况|状态)|体检|防火墙|"
        r"系统信息|环境变量|剪贴板|壁纸|音量|亮度|启动项|服务|wifi|wi-fi",
        (text or ""), re.I))
