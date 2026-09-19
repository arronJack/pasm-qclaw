"""browser_agent —— 浏览器自动化（Playwright）。

让 PASM 真正"控制浏览器"：打开网页、搜索、点击、填表、滚动、抽取信息、截图。
对标 QClaw/OpenClaw/WorkBuddy 的浏览器自动化能力。

设计要点
- **优先复用系统自带浏览器**（Windows 都有 Edge）→ 免下载 150MB Chromium；
  失败再退回 Playwright 自带 Chromium。
- 无头/有头可配（默认有头，让用户看到浏览器被操作）。
- 未安装 Playwright 时给出明确安装指引，而不是崩溃。
- 所有异常都包成中文可读的 RuntimeError 抛给上层，由聊天层如实告知用户。
- 敏感动作（登录/密码）绝不静默代入，会在结果里明确提示。
"""
import os
import re

try:
    from playwright.sync_api import sync_playwright
    _HAVE_PW = True
except Exception:
    _HAVE_PW = False

_TW = 30000          # 导航超时
_TW_ACT = 8000       # 单步动作超时


def _need_pw():
    if not _HAVE_PW:
        raise RuntimeError(
            "还没装浏览器自动化依赖（Playwright）。\n"
            "源码运行可执行：pip install playwright\n"
            "（Windows 一般无需再下载浏览器，会直接复用系统自带的 Edge。）")


def _q(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote((s or "").strip() or "news")


def _launch(p, headless: bool):
    """优先用系统自带浏览器（免下载），失败再退回自带 Chromium。"""
    last = None
    for _ch in ("msedge", "chrome"):
        try:
            return p.chromium.launch(channel=_ch, headless=headless)
        except Exception as ex:
            last = ex
    try:
        return p.chromium.launch(headless=headless)
    except Exception:
        raise RuntimeError("启动浏览器失败：%s" % (last or "未找到可用的浏览器"))


def _clean(t: str) -> str:
    return re.sub(r"\n{2,}", "\n", (t or "").strip())


# 常见站点中文名 → 网址（"打开百度 / 打开淘宝" 也能直接去对地方）
_SITE_ALIAS = {
    "百度": "https://www.baidu.com", "淘宝": "https://www.taobao.com",
    "京东": "https://www.jd.com", "知乎": "https://www.zhihu.com",
    "微博": "https://weibo.com", "豆瓣": "https://www.douban.com",
    "哔哩哔哩": "https://www.bilibili.com", "b站": "https://www.bilibili.com",
    "bilibili": "https://www.bilibili.com", "腾讯": "https://www.qq.com",
    "网易": "https://www.163.com", "新浪": "https://www.sina.com.cn",
    "头条": "https://www.toutiao.com", "高德": "https://www.amap.com",
    "github": "https://github.com",
}


def _pick_nav(instr: str):
    """从指令里解析出"先去哪"：返回 (url 或 "", 动作名)。

    优先级：显式网址 > 搜索意图 > 字母域名 > 站点中文别名。
    """
    url = re.search(r"https?://[^\s]+", instr)
    if url:
        return url.group(0), "打开网页"
    kw = re.search(r"(?:搜索|搜|查(?:一下)?|找(?:一下)?)\s*(.+?)(?:$|[，,。；;]|然后|再|并|顺便)", instr)
    site = re.search(r"(?:打开|访问|进|去|上)\s*(?:一下)?\s*([A-Za-z0-9.\-]+\.[A-Za-z]{2,})", instr)
    low = (instr or "").lower()
    alias = next(((n, u) for n, u in _SITE_ALIAS.items() if n in low), None)
    if kw and kw.group(1).strip():
        return "https://www.bing.com/search?q=" + _q(kw.group(1).strip()), "搜索"
    if site:
        return "https://" + site.group(1), "打开网站"
    if alias:
        return alias[1], "打开%s" % alias[0]
    return "", ""


def _click_text(page, text: str, notes: list):
    text = (text or "").strip()
    if not text:
        return
    for getter in (lambda: page.get_by_role("link", name=text).first,
                   lambda: page.get_by_role("button", name=text).first,
                   lambda: page.get_by_text(text, exact=False).first):
        try:
            getter().click(timeout=_TW_ACT)
            notes.append("已点击「%s」" % text)
            return
        except Exception:
            continue
    notes.append("没点到「%s」（页面上没找到这个可点元素）" % text)


def _fill(page, field: str, value: str, notes: list):
    field, value = (field or "").strip(), (value or "").strip()
    if not field:
        return
    getters = (lambda: page.get_by_label(field, exact=False).first,
               lambda: page.locator('[placeholder*="%s"]' % field).first,
               lambda: page.locator('[name*="%s"]' % field).first,
               lambda: page.locator('[aria-label*="%s"]' % field).first,
               lambda: page.get_by_placeholder(field, exact=False).first)
    for g in getters:
        try:
            el = g()
            el.fill(value, timeout=max(4000, _TW_ACT // 2))
            notes.append("已在「%s」填入内容" % field)
            if re.search(r"密码|password|passwd|pwd", field, re.I):
                notes.append("⚠ 涉及密码字段：请确认无误；我不会保存你的密码。")
            return
        except Exception:
            continue
    notes.append("没找到「%s」对应的输入框" % field)


def _scroll(page, notes: list):
    try:
        page.mouse.wheel(0, 3200)
        page.wait_for_timeout(400)
        notes.append("已向下滚动一屏")
    except Exception as ex:
        notes.append("滚动没成功：%s" % str(ex)[:60])


def run_browser_task(instruction: str, headless: bool = False, shot_dir: str = "") -> str:
    """按自然语言指令操作浏览器，返回结果文本（含截图路径）。

    支持：打开网页/网站、搜索、点击、填表/输入、滚动、截图、页面要点抽取。
    多个动作可写在一句里（如"打开某站，点击登录，在用户名输入 abc"）。
    """
    _need_pw()
    instr = (instruction or "").strip()
    if not instr:
        return "没收到要我在浏览器里做什么。"
    shot_dir = shot_dir or os.path.join(os.path.expanduser("~"), "Pictures", "pasm_browser")
    os.makedirs(shot_dir, exist_ok=True)
    shot_path = os.path.join(shot_dir, "browser_shot.png")
    notes: list = []
    try:
        with sync_playwright() as p:
            browser = _launch(p, headless)
            page = browser.new_page()
            target, action = _pick_nav(instr)
            if target:
                page.goto(target, timeout=_TW)
            else:
                page.goto("https://www.bing.com/search?q=" + _q(instr), timeout=_TW)
                action = "搜索"
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            # —— 后续动作（可在同一句里叠加）——
            for m in re.finditer(r"(?:点击|点开|点)\s*[「\"'“]?([^」\"'”\s,，。；;]+)", instr):
                _click_text(page, m.group(1), notes)
            fm = re.search(
                r"(?:在|把|给|向|往)?\s*[「\"'“]?([^「」\"'”\s,，。；;]{1,20}?)[」\"'”]?\s*"
                r"(?:里|中)?\s*(?:填入|填写|填|输入)\s*[「\"'“]?([^「」\"'”\s,，。；;]+)", instr)
            if fm:
                _fill(page, fm.group(1), fm.group(2), notes)
            if re.search(r"滚动|下拉|下滑|翻到|拉到底|到底部|往下看", instr):
                _scroll(page, notes)
            title = page.title()
            text = ""
            try:
                text = _clean(page.inner_text("body"))[:1500]
            except Exception:
                text = ""
            try:
                page.screenshot(path=shot_path, full_page=False)
                shot = "📸 截图已存：%s" % shot_path
            except Exception:
                shot = ""
            browser.close()
        out = ["✅ 已在浏览器完成「%s」：%s" % (action, title)]
        if notes:
            out.append("执行细节：" + "；".join(notes))
        if shot:
            out.append(shot)
        if text:
            out.append("页面要点：\n" + text)
        return "\n".join(out)
    except Exception as ex:
        raise RuntimeError("浏览器操作失败：" + str(ex))
