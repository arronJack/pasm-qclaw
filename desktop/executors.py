# -*- coding: utf-8 -*-
"""executors.py —— 工作流执行器聚合层（v0.30.5）

`workflow_engine` 只负责调度（取下一步 → 找执行器 → 回写进度）；
**具体怎么干**集中在这里，避免调度模块沾染业务细节。

已接工种：

| kind      | 干什么 | 依赖 | 没有依赖时 |
|-----------|--------|------|-----------|
| `image`   | 真出图 | `creators` + 引擎配置 | skip + 如实说明 |
| `video`   | 图生视频 | `videoeng` + 引擎配置 | skip |
| `audio`   | 真配音 | `creators.synth_speech` | failed |
| `ad`      | 广告方案（另见 `ad_design`） | 无 | — |
| `browser` | 浏览器自动化取资料 | `playwright` | failed + 安装提示 |
| `kb`      | 知识库检索 → 落盘参考 | `knowledge` | failed |
| `doc`     | 结构化文档落盘 | 无 | — |
| `code`    | **真装框架**：官方脚手架 + 真装依赖 | `node`/`npm`/`python` | 回落离线骨架 + 如实说明 |
| `payment` | **收单适配层**（沙箱，真签名+状态机） | 无 | — |

## 三条诚实边界（与 media_job 一致）

1. **产出必须真落盘** —— 执行器返回的 artifacts 必须是磁盘上真实存在的文件。
2. **没依赖就说没依赖** —— 不假装跑过、不产出空壳文件冒充成果。
3. **失败带原因** —— 返回的 progress 文本要能让人看懂卡在哪。
"""
from __future__ import annotations

import os
import re
import logging         # v0.30.15 修：_outline_of 曾用未导入的 logging（异常被吞→静默降级）


# --------------------------------------------------------------------------
# v0.30.6：语言模型入口 + 产出质检
#
# 为什么放在这里而不是各执行器里各写一份：
#   ① "用哪个模型"是**产品配置**，不该由执行器自己决定 —— UI 注入一次即可；
#   ② 质检是**统一底线**（没过就不许标完成），散落各处必然有人漏。
# --------------------------------------------------------------------------
_LLM_FN = None


def set_llm_fn(fn):
    """UI 注入"用当前配置的模型写文案"的入口（幂等；传 None 清除）。"""
    global _LLM_FN
    _LLM_FN = fn if callable(fn) else None
    return _LLM_FN


def llm_fn(ctx: dict = None):
    """本次可用的 llm 入口：ctx 指定的优先，其次模块级注入；都没有返回 None。"""
    f = (ctx or {}).get("llm_fn") or _LLM_FN
    return f if callable(f) else None


def qa_check(text: str, files=None, kind: str = "generation", min_len: int = 30):
    """产出质检：**没过就不算完成**。返回 (ok, [问题...])。

    为什么必须做（v0.30.6 取证）：工作链路此前完全没有质检 ——
    `validator` 只在聊天路径被调用；执行器只要"返回了文件"就算成功，
    哪怕文件是空的、或文案是写死的模板句。
    规则：
      - 有产物文件 → 必须真实存在且非空；
      - 有文本 → 走 validator（空 / 拒绝话术 / 占位符 / 过短 / 代码未闭合）；
      - 两者都没有 → 直接不合格。
    """
    import os as _os
    probs = []
    fs = [f for f in (files or []) if f]
    for f in fs:
        if not _os.path.exists(f):
            probs.append("产物文件不存在：%s" % f)
        elif _os.path.getsize(f) == 0:
            probs.append("产物是空文件：%s" % _os.path.basename(f))
    txt = (text or "").strip()
    if txt:
        try:
            import validator as VAL
            ok, ps = VAL.check(kind, txt, min_len=min_len)
            if not ok:
                probs.extend(ps)
        except Exception as ex:  # noqa: BLE001
            probs.append("质检器不可用：%s" % ex)
    if not fs and not txt:
        probs.append("没有任何产出")
    return (not probs), probs


def _qa_tail(probs) -> str:
    """把质检问题拼成一句可读的说明（给面板/日志用）。"""
    return "；".join(probs[:3]) if probs else ""


def _safe_name(step: dict) -> str:
    t = (step or {}).get("title") or (step or {}).get("id") or "step"
    t = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(t)).strip("_ .")
    return (t or "step")[:40]


def _out_dir(ctx: dict, cat: str = "doc") -> str:
    """工作流步骤产物目录。

    v0.30.11：不再落 `DATA_DIR/works`（那是**第四套根**），改落**统一工作根**下的
    分类目录。默认 `doc` —— 浏览器资料 / 知识参考 / 文档骨架都属文书类；
    具体执行器按语义覆盖（媒体按 image/video、支付适配层按 project）。
    `ctx["out_dir"]` 显式给了就听它的（测试与特殊场景用）。
    """
    d = (ctx or {}).get("out_dir") or ""
    if not d:
        try:
            import workspace as WS
            d = WS.cat_dir(cat)
        except Exception:  # noqa: BLE001
            d = os.path.join(os.path.expanduser("~"), ".pasmstudio", "works")
    os.makedirs(d, exist_ok=True)
    return d


def _media_cat(kind: str) -> str:
    """媒体 kind → 分类。`audio`（配音 / 试听）与成片同族，归 video 不单开一类。"""
    if kind == "audio":
        return "video"
    import workspace as WS
    return WS.cat_of(kind)


def _write(dir_: str, name: str, text: str) -> str:
    p = os.path.join(dir_, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(text or "")
    return p


# --------------------------------------------------------------------------
# 媒体类（统一走 media_job，参数与降级链集中在那里）
# --------------------------------------------------------------------------
def _media(kind: str):
    def run(step: dict, ctx: dict = None):
        ctx = ctx or {}
        import media_job as MJ
        prompt = ((step or {}).get("next_step") or "").strip()
        prompt = re.sub(r"^待执行[：:]\s*", "", prompt) or (step or {}).get("title") or ""
        job = MJ.new(kind, prompt, out_dir=_out_dir(ctx, _media_cat(kind)),
                     dir=ctx.get("dir") or "", wid=(step or {}).get("id") or "")
        MJ.run_and_record(job, ctx)
        if job["status"] == "done":
            # v0.30.6：出活儿了也要过质检 —— 空文件/坏文件不许算完成
            _ok, _probs = qa_check("", job.get("artifacts") or [], kind="generation")
            if not _ok:
                return False, "⚠️ 产物未过质检（未标完成）：%s" % _qa_tail(_probs), []
            _eng = job.get("engine") or "?"
            _ch = job.get("chain_note") or ""
            return True, "✅ 已产出 %s（引擎：%s%s）" % (
                os.path.basename(job["artifacts"][0]), _eng,
                "，" + _ch if _ch else ""), job["artifacts"]
        if job["status"] == "skipped":
            return False, "⚠️ %s（未标完成）" % job["error"], []
        return False, "❌ %s" % job["error"], []
    return run


# --------------------------------------------------------------------------
# 浏览器：收集资料 → 落成 .md（这是工作流里"查资料"这类步骤的真执行器）
# --------------------------------------------------------------------------
def exec_browser(step: dict, ctx: dict = None):
    ctx = ctx or {}
    instr = (step or {}).get("title") or ""
    det = ((step or {}).get("next_step") or "").strip()
    det = re.sub(r"^待执行[：:]\s*", "", det)
    if det and det not in instr:
        instr = "%s（%s）" % (instr, det)
    try:
        import browser_agent as BA
    except Exception as e:  # noqa: BLE001
        return False, "❌ 浏览器模块不可用：%s" % e, []
    d = _out_dir(ctx)
    try:
        text = BA.run_browser_task(instr, headless=bool(ctx.get("headless", True)),
                                   shot_dir=os.path.join(d, "shots"))
    except ImportError as e:
        return False, ("❌ 未安装 playwright（pip install playwright && "
                       "playwright install chromium）：%s" % e), []
    except Exception as e:  # noqa: BLE001
        return False, "❌ 浏览器任务失败：%s" % e, []
    if not (text or "").strip():
        return False, "❌ 浏览器没有返回内容（未标完成）", []
    md = _write(d, "%s-浏览器资料.md" % _safe_name(step),
                "# %s\n\n> 由浏览器自动化执行\n\n%s\n" % (instr, text))
    return True, "✅ 已取回资料并落盘 %s" % os.path.basename(md), [md]


# --------------------------------------------------------------------------
# 知识库：检索 → 落盘参考（把"自学成果"真正带进工作流）
# --------------------------------------------------------------------------
def exec_kb(step: dict, ctx: dict = None):
    ctx = ctx or {}
    try:
        import kb_bridge as KB
    except Exception as e:  # noqa: BLE001
        return False, "❌ 知识桥不可用：%s" % e, []
    goal = (step or {}).get("title") or ""
    det = re.sub(r"^待执行[：:]\s*", "", ((step or {}).get("next_step") or "").strip())
    q = ("%s %s" % (goal, det)).strip() or goal
    d = KB.digest(q, limit=3, max_chars=1200)
    if not d:
        return False, "⚠️ 知识库里没有与该步相关的内容（未标完成）", []
    hs = KB.hits(q, limit=3)
    body = ["# 参考：%s" % goal, "", d, ""]
    if hs:
        body.append("## 命中的知识（可核对相关性）")
        for h in hs:
            body.append("- 《%s》 相关度 %d 分 · 来源 %s"
                        % (h["title"], h["score"], h.get("src") or "自学"))
    p = _write(_out_dir(ctx), "%s-参考要点.md" % _safe_name(step), "\n".join(body))
    return True, "✅ 用到 %d 条自学知识，已落盘 %s" % (len(hs), os.path.basename(p)), [p]


# --------------------------------------------------------------------------
# 文档：结构化落盘（离线可用，不依赖任何引擎）
# --------------------------------------------------------------------------
def exec_doc(step: dict, ctx: dict = None):
    ctx = ctx or {}
    title = (step or {}).get("title") or "文档"
    det = re.sub(r"^待执行[：:]\s*", "", ((step or {}).get("next_step") or "").strip())
    lines = ["# %s" % title, ""]
    if det:
        lines += ["## 要点", det, ""]
    lines += ["## 待补充", "（本步只产出骨架；正文由对话环节补全）", ""]
    p = _write(_out_dir(ctx), "%s.md" % _safe_name(step), "\n".join(lines))
    # v0.30.6：文件级质检（空文件/白写不算完成）。
    # 注意这里**不做文本质检** —— 本执行器产出的本来就是骨架（正文留到对话环节），
    # 拿"过短/占位符"去卡它会把正常流程全判失败。
    _ok, _probs = qa_check("", [p], kind="generation")
    if not _ok:
        return False, "⚠️ 文档未过质检（未标完成）：%s" % _qa_tail(_probs), []
    return True, "✅ 已落盘 %s（骨架；正文待对话补全）" % os.path.basename(p), [p]


# --------------------------------------------------------------------------
# 代码：真装框架（v0.30.11 #2-A）
#
# 为什么必须补这个工种：_MAP 里原来**没有 code**，于是「做个网站/开发一个后台」
# 这类步骤永远返回 no_executor，用户拿到手的只有对话里一段示例代码 ——
# 这就是他说的"只出通用模板，没真装框架"。
# --------------------------------------------------------------------------
def exec_code(step: dict, ctx: dict = None):
    """真跑官方脚手架 + 真装依赖，落盘一个**可运行**工程。

    诚实边界：依赖没装好就**不标完成**（ok=False + 返回文本写明卡在哪），
    绝不因为"文件写出来了"就报成功。
    """
    ctx = ctx or {}
    import scaffold as SC
    title = (step or {}).get("title") or ""
    det = re.sub(r"^待执行[：:]\s*", "", ((step or {}).get("next_step") or "").strip())
    p = SC.plan(text=(title + " " + det).strip(),
                name=ctx.get("name") or title, root=ctx.get("ws_dir") or "")
    res = SC.run(p, install=bool(ctx.get("install", True)))
    return res["ok"], res["log"], res["artifacts"]


# --------------------------------------------------------------------------
# 支付：给**用户自己的项目**接收单能力（v0.30.11 #2-B，默认沙箱）
#
# ⚠️ 与 cando.py 的安全底线不是一回事：那条挡的是"代替用户去付款"；
# 这条是"用户的网站/应用怎么收钱"。收单能力落在用户自己的项目里。
# --------------------------------------------------------------------------
def exec_payment(step: dict, ctx: dict = None):
    """把收单适配层（沙箱协议：真 HMAC 签名 + 状态机）落进产出目录。"""
    ctx = ctx or {}
    import payment as PAY
    import scaffold as SC
    title = (step or {}).get("title") or "支付"
    det = re.sub(r"^待执行[：:]\s*", "", ((step or {}).get("next_step") or "").strip())
    stack = SC.detect((title + " " + det).strip())
    d = os.path.join(_out_dir(ctx, "project"), _safe_name(step) + "_pay")
    os.makedirs(d, exist_ok=True)
    files = PAY.adapter_files(stack, PAY.load_config())
    arts = []
    for rel, body in files.items():
        rel = (rel or "").lstrip("/ ").replace("\\", "/")
        if not rel or ".." in rel.split("/"):
            continue
        arts.append(_write(d, rel, body))
    st = PAY.status()
    lines = ["💳 收单适配层已落盘（%s）：%s"
             % (stack or "通用", "、".join(sorted(files))),
             "· 当前渠道：%s ｜ 真实收款：%s"
             % (st["provider"], "已开启" if st["live"] else "未开启（沙箱，不收真钱）"),
             "· 配置文件：%s" % st["config_path"],
             "⚠️ 接真实渠道前：填齐凭据 → 把 live 改成 true → "
             "notify_url 换成公网可达地址（协议/验签/状态机不用改）。"]
    _ok, _probs = qa_check("", arts, kind="generation")
    if not _ok:
        return False, "⚠️ 适配层未过质检（未标完成）：%s" % _qa_tail(_probs), []
    return True, "\n".join(lines), arts


def exec_ad(step: dict, ctx: dict = None):
    """广告设计执行器（v0.30.13）。

    以前 `_MAP` 里**没有 ad** —— ad 只靠 `workflow_engine` 里那句
    `attach_executor("ad", AD.executor)` 兜着；而 `executors` 自检的 `need` 列表里
    也没有 ad，所以"装漏了"这件事从来没被抓住（小志真机反馈"广告设计和图像没区别"
    就是这么积下来的）。这里显式接进来，并同步加进自检 need。
    """
    import ad_design as AD
    return AD.executor(step, ctx)


def _outline_of(step: dict, ctx: dict) -> str:
    """把一步工作流变成 `make_pptx/docx/xlsx` 认的 Markdown 大纲。

    格式：`# 标题` + 若干 `## 小节` + `- 要点`。
    **有大脑就真写大纲，没有就只用步骤描述** —— 宁可少写也不编内容
    （编出来的"要点"比空着更糟：用户会当真）。
    """
    title = (step or {}).get("title") or "文档"
    det = re.sub(r"^待执行[：:]\s*", "", ((step or {}).get("next_step") or "").strip())
    llm = (ctx or {}).get("llm_fn")
    if callable(llm):
        try:
            txt = llm("请为下面这个任务写一份精简大纲（用于自动生成演示/文档）：\n"
                      "任务：%s\n补充：%s\n"
                      "格式要求：第一行是标题（不要加 #），之后每个小节一行「## 小节名」，"
                      "小节下写 3~6 条以「- 」开头的要点。只输出大纲，不要任何解释。"
                      % (title, det))
            if isinstance(txt, str) and len(txt.strip()) > 20:
                return txt.strip()
        except Exception:                       # noqa: BLE001
            logging.info("大纲生成失败，退回步骤描述")
    return "# %s\n\n## 要点\n\n- %s\n" % (title, det or "（待补充）")


def exec_ppt(step: dict, ctx: dict = None):
    """PPT 执行器（v0.30.13）：**真产出 .pptx**。

    以前 `_MAP` 里没有 ppt/xls/copy —— 工作流子步被 `_kind_of` 判成这些 kind 时
    永远返回 `no_executor`（"做个 PPT 的工作流"一步都跑不动）。而真产出能力
    （`agent_tools.make_pptx`，16:9 封面 + 每节一页）**早就有了**，只是没人把它接到工种上。
    """
    ctx = ctx or {}
    try:
        import agent_tools as AT
    except Exception as e:                      # noqa: BLE001
        return False, "文档产出模块不可用：%s" % e, []
    try:
        d = os.path.join(_out_dir(ctx, "doc"), _safe_name(step))
        p = AT.make_pptx(_outline_of(step, ctx), d + ".pptx")
    except Exception as e:                      # noqa: BLE001
        return False, "PPT 生成失败：%s" % e, []
    if not p or not os.path.isfile(p) or os.path.getsize(p) < 1024:
        return False, "⚠️ PPT 未产出（生成失败或文件为空）", []
    return True, "✅ 已产出 %s（%d KB）" % (os.path.basename(p),
                                          os.path.getsize(p) // 1024), [p]


def exec_xls(step: dict, ctx: dict = None):
    """Excel 执行器（v0.30.13）：**真产出 .xlsx**（有表格就照表格，没有就两列）。"""
    ctx = ctx or {}
    try:
        import agent_tools as AT
    except Exception as e:                      # noqa: BLE001
        return False, "文档产出模块不可用：%s" % e, []
    try:
        d = os.path.join(_out_dir(ctx, "doc"), _safe_name(step))
        p = AT.make_xlsx(_outline_of(step, ctx), d + ".xlsx")
    except Exception as e:                      # noqa: BLE001
        return False, "Excel 生成失败：%s" % e, []
    if not p or not os.path.isfile(p) or os.path.getsize(p) < 512:
        return False, "⚠️ Excel 未产出（生成失败或文件为空）", []
    return True, "✅ 已产出 %s" % os.path.basename(p), [p]


def exec_copy(step: dict, ctx: dict = None):
    """文案执行器（v0.30.13）：产出**有正文的**文案稿。

    与 `exec_doc` 的区别：doc 产出的是骨架（正文留对话补全），文案类**必须有正文**
    —— 所以这里让大脑真写一版，写完过质检（空/敷衍不算完成）。
    """
    ctx = ctx or {}
    title = (step or {}).get("title") or "文案"
    det = re.sub(r"^待执行[：:]\s*", "", ((step or {}).get("next_step") or "").strip())
    llm = ctx.get("llm_fn")
    body = ""
    if callable(llm):
        try:
            body = llm("请直接写出成稿（不要解释、不要前后缀）：\n任务：%s\n补充：%s"
                       % (title, det)) or ""
        except Exception:                       # noqa: BLE001
            body = ""
    if len(str(body).strip()) < 40:
        # 没有大脑就**不谎报**：写成骨架并明确标注
        body = ("（本步需要模型写正文，但当前没有可用的大脑，只落骨架）\n\n"
                "## 任务\n%s\n\n## 补充\n%s\n" % (title, det or "—"))
    p = _write(_out_dir(ctx, "copy"), "%s.md" % _safe_name(step), str(body))
    _ok, _probs = qa_check(str(body), [p], kind="generation", min_len=40)
    if not _ok:
        return False, "⚠️ 文案未过质检（未标完成）：%s" % _qa_tail(_probs), []
    return True, "✅ 已产出 %s" % os.path.basename(p), [p]


# --------------------------------------------------------------------------
# 安装
# --------------------------------------------------------------------------
# ⚠️ 这里每个值都必须是**工厂**（无参可调用，返回真正的执行器）。
# 曾把 browser/kb/doc 直接写成函数引用，结果 install() 里 factory() 一调
# 就报 "missing 1 required positional argument: 'step'" —— 而异常又被
# `except: pass` 吞掉，表现为"只注册了 3 个工种"，排查花了一轮。
_MAP = {
    "image": lambda: _media("image"),
    "video": lambda: _media("video"),
    "audio": lambda: _media("audio"),
    "manga": lambda: _media("image"),
    "browser": lambda: exec_browser,
    "kb": lambda: exec_kb,
    "doc": lambda: exec_doc,
    # v0.30.11 #2：真装框架 / 接入支付（沙箱）
    "code": lambda: exec_code,
    "payment": lambda: exec_payment,
    # v0.30.13：广告设计（以前只在 workflow_engine 里兜着，_MAP 缺它）
    "ad": lambda: exec_ad,
    # v0.30.13：文档三件套的真产出（这三个 kind 以前永远 no_executor）
    "ppt": lambda: exec_ppt,
    "xls": lambda: exec_xls,
    "copy": lambda: exec_copy,
}

#: install() 里装失败的工种 -> 原因（供自检/面板显示，避免静默丢失能力）
INSTALL_ERRORS: dict = {}


def install(engine=None) -> dict:
    """把所有执行器注册到 workflow_engine（重复调用安全）。"""
    if engine is None:
        try:
            import workflow_engine as engine  # type: ignore
        except Exception as e:  # noqa: BLE001
            INSTALL_ERRORS["_import"] = str(e)
            return {}
    INSTALL_ERRORS.clear()
    for kind, factory in _MAP.items():
        try:
            engine.attach_executor(kind, factory())
        except Exception as e:  # noqa: BLE001  单个装不上不影响其它，但要能看见
            INSTALL_ERRORS[kind] = "%s: %s" % (type(e).__name__, e)
    return dict(getattr(engine, "EXECUTORS", {}))


def _selftest() -> int:
    import shutil
    import tempfile
    fails = []
    n = {"v": 0}

    def check(name, cond, extra=""):
        n["v"] += 1
        print("  %s %s%s" % ("[OK]  " if cond else "[FAIL]", name,
                             ("  " + str(extra)) if (extra and not cond) else ""))
        if not cond:
            fails.append(name)

    print("== executors 自检 ==")
    saved = os.environ.get("PASM_STUDIO_DIR")
    tmp = tempfile.mkdtemp(prefix="ex_")
    os.environ["PASM_STUDIO_DIR"] = tmp
    try:
        ctx = {"out_dir": os.path.join(tmp, "out")}
        step = {"id": "s1", "title": "写一份开学季招生方案",
                "next_step": "待执行：先列大纲"}

        # doc 执行器：真落盘
        ok, msg, arts = exec_doc(step, ctx)
        check("doc 执行器返回成功", ok, msg)
        check("doc 产出文件真实存在", bool(arts) and os.path.exists(arts[0]), arts)
        check("doc 文件非空", os.path.getsize(arts[0]) > 0)

        # kb 执行器：空库时不谎报
        ok2, msg2, arts2 = exec_kb(step, ctx)
        check("空知识库时 kb 执行器不谎报成功", (not ok2) or bool(arts2),
              (ok2, msg2, arts2))
        check("kb 说明里带'未标完成'或真有产物",
              ("未标完成" in msg2) or bool(arts2), msg2)

        # browser 执行器：没 playwright / 没网络时必须是失败而不是假装成功
        ok3, msg3, arts3 = exec_browser(step, ctx)
        check("browser 失败时不返回产物", (ok3 and arts3) or (not ok3 and not arts3),
              (ok3, msg3))
        check("browser 失败说明可读", bool(msg3), msg3)

        # 文件名清洗：非法字符不进文件名
        dirty = {"id": "s2", "title": 'a/b\\c:d*e?f"g<h>i|j'}
        ok4, _, arts4 = exec_doc(dirty, ctx)
        base = os.path.basename(arts4[0]) if arts4 else ""
        check("非法字符已清洗", ok4 and not re.search(r'[\\/:*?"<>|]', base), base)

        # install()：注册进 workflow_engine
        import workflow_engine as WF
        WF.EXECUTORS.clear()
        got = install(WF)
        need = ["image", "video", "audio", "manga", "browser", "kb", "doc",
                "code", "payment", "ad", "ppt", "xls", "copy"]
        miss = [k for k in need if k not in got]
        check("install 注册 %d 个工种" % len(need), not miss, miss)
        check("install 无静默失败", not INSTALL_ERRORS, INSTALL_ERRORS)
        check("执行器可被调度取出", callable(WF.executor_for("browser")))
        check("未知工种仍无执行器（不误配）", WF.executor_for("不存在") is None)
        # v0.30.11 #2：这两条是"真装框架/接支付"能不能被工作流调到的前提。
        # 少了任何一条，「做个网站」类步骤就只会回 no_executor（老毛病复发）。
        check("code 工种已有真执行器（不再是 no_executor）",
              callable(WF.executor_for("code")))
        # v0.30.13：广告同理 —— 它以前只在 workflow_engine 里兜着，_MAP 缺它，
        # 自检 need 也缺它，于是"装漏了"一直没被抓住（真机反馈就是这么暴露的）。
        check("ad 工种已有真执行器（不再是 no_executor）",
              callable(WF.executor_for("ad")))
        # v0.30.13：文档三件套同理 —— 被判成 ppt/xls/copy 的子步以前永远
        # no_executor（"做个 PPT 的工作流"一步都跑不动）。
        for _k in ("ppt", "xls", "copy"):
            check("%s 工种已有真执行器（不再是 no_executor）" % _k,
                  callable(WF.executor_for(_k)))
        check("payment 工种已有真执行器",
              callable(WF.executor_for("payment")))

        # 端到端：跑一步真活，进度要真的动
        rid = WF.plan_workflow("整理一份资料清单", dir="测试")
        r = WF.run_next(rid, ctx)
        check("run_next 有明确结果", "ok" in r and ("reason" in r or "step" in r), r)
    finally:
        if saved is None:
            os.environ.pop("PASM_STUDIO_DIR", None)
        else:
            os.environ["PASM_STUDIO_DIR"] = saved
        shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 46)
    if fails:
        print("自检失败 %d/%d：%s" % (len(fails), n["v"], "；".join(fails)))
        return 1
    print("自检通过：%d 项全绿" % n["v"])
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())
