# -*- coding: utf-8 -*-
"""ad_design.py —— 广告设计工作版本（v0.30.4）

给定产品 / 主题 / 风格，产出：
  · 文案（标题 / 卖点 / 行动号召）—— 复用 LLM 网关（可选）；
  · 图像提示词（交给图像生成通道，由调用方拿去生图）；
并记入 worklog（kind="ad"），支持在右侧面板按目录复盘。
"""
from __future__ import annotations

import worklog as WL


def build_prompt(theme: str, product: str, style: str = "现代简约") -> str:
    return ("为【%s】设计一张【%s】风格广告主视觉。核心卖点：%s。"
            "画面要求：主体突出、留白克制、配色高级、有品牌记忆点。"
            ) % (product or theme, style, theme)


import logging


def copywrite(theme: str, product: str, llm_fn=None) -> dict:
    """产出广告文案。llm_fn(prompt)->str 可选；**没模型时走离线模板并如实标注**。

    v0.30.6 修正（真机问题）：以前调用方**不传 llm_fn**，界面照样显示"广告设计完成"，
    而用户看到的其实是写死的模板句（"X，就选Y"）—— 看起来就是"业余"。
    现在返回值带 `source`：
      - "llm"      ：真由模型写的；
      - "template" ：离线兜底（并附 note 说明），界面与方案文件里都要如实标出来。
    另外提示词改成要**结构化三行**（标题/卖点/号召），比原来"写一句广告文案"
    更接近真实工作产出。
    """
    if llm_fn:
        try:
            txt = llm_fn(
                "你是资深广告文案。主题「%s」、产品「%s」。只输出三行，不要解释：\n"
                "第1行 标题（≤20字，有冲击力）\n"
                "第2行 核心卖点（≤40字，说人话、别堆形容词）\n"
                "第3行 行动号召（≤12字）" % (theme, product))
            txt = (txt or "").strip()
            if txt:
                rows = [x.strip(" -•·*") for x in txt.splitlines() if x.strip()]
                if len(rows) >= 3:
                    return {"headline": rows[0][:40], "body": rows[1][:200],
                            "cta": rows[2][:30], "source": "llm"}
                if len(rows) == 1 and len(rows[0]) >= 10:
                    # 模型只给了一行：当作正文，别硬拆
                    return {"headline": rows[0][:40], "body": rows[0],
                            "cta": "立即了解 →", "source": "llm"}
        except Exception:  # noqa: BLE001
            pass
    return {
        "headline": "%s，就选%s" % (theme, product),
        "body": "把%s做到极致，让每一次选择都更安心。" % product,
        "cta": "立即了解 →",
        "source": "template",
        "note": "本条是离线模板稿（未接入模型或模型未返回），建议重跑以获得真正的文案",
    }


def creative_brief(theme: str, product: str, style: str = "现代简约",
                  audience: str = "", llm_fn=None) -> dict:
    """产出**创意说明（策略层）**：传播目标 / 目标人群 / 核心主张 / 调性 / 媒介建议 /
    主视觉构思。让「广告设计」像广告公司那样先给策略、再出设计（小志 #6 要求）。

    有 llm_fn 走模型，否则走结构化离线模板并标 source（与 copywrite 同一诚实原则）。
    """
    if llm_fn:
        try:
            txt = llm_fn(
                "你是资深广告策略。为【%s】（产品：%s，风格：%s%s）写一页创意说明，"
                "分点输出（每点≤40字，不要解释）：\n"
                "1. 传播目标\n2. 目标人群\n3. 核心主张（一句话）\n"
                "4. 调性\n5. 媒介/投放建议\n6. 主视觉构思"
                % (theme, product, style,
                   ("，人群：" + audience) if audience else ""))
            txt = (txt or "").strip()
            if txt:
                items = [x.strip(" -•·*0123456789.") for x in txt.splitlines() if x.strip()]
                if len(items) >= 4:
                    return {"goal": items[0][:60], "audience": items[1][:60],
                            "message": items[2][:120], "tone": items[3][:60],
                            "media": (items[4] if len(items) > 4 else "")[:80],
                            "visual": (items[5] if len(items) > 5 else "")[:120],
                            "source": "llm", "raw": txt}
        except Exception:  # noqa: BLE001
            pass
    return {
        "goal": "提升【%s】的认知与好感" % (product or theme),
        "audience": audience or "关注该品类的潜在客户",
        "message": "%s，就选%s" % (theme, product),
        "tone": style,
        "media": "社交媒体 / 信息流 / 官网首屏",
        "visual": "主体突出、留白克制、配色高级、有品牌记忆点",
        "source": "template",
        "note": "离线模板稿（未接入模型时的兜底，建议重跑获得真正的策略）",
    }


def design(theme: str, product: str, style: str = "现代简约",
           dir: str = "广告设计", llm_fn=None) -> dict:
    """建一条广告设计工作，返回 {task_id, copy, prompt}。

    ⚠️ 已从对话路径**弃用**（v0.30.13）：它**只建台账、不产出任何文件** ——
    以前对话里"广告设计"栏目就是调它，再转给图像分支出图，于是既和「图像」
    栏目一模一样，又在台账里留一条永远 running、恒 0% 的空任务。
    现在对话走 `_create_ad` → `materialize`（真出方案 .md + 图）。
    这里保留给旧调用/外部脚本，但**别拿它当"广告产出"入口**。
    """
    cw = copywrite(theme, product, llm_fn)
    tid = WL.create(title="广告：%s" % ((product or theme)[:40]),
                    kind="ad", dir=dir, level=0,
                    next_step="生成主视觉 + 文案排版")
    WL.add_artifact(tid, "(待生成) " + build_prompt(theme, product, style)[:80])
    return {"task_id": tid["id"], "copy": cw,
            "prompt": build_prompt(theme, product, style)}


# --------------------------------------------------------------------------
# v0.30.5：真交付物（可被 workflow_engine 当执行器调用）
# --------------------------------------------------------------------------
def materialize(theme: str, product: str, style: str = "现代简约",
                out_dir: str = "", image_fn=None,
                dir: str = "广告设计", llm_fn=None,
                audience: str = "", record: bool = True,
                prebuilt: dict = None) -> dict:
    """产出**真实文件**：广告方案 .md（创意说明 + 文案 + 主视觉 + 设计图）；
    若传入 image_fn，再真出图并把图片路径一并记为产物。

    v0.30.10（小志 #6）：先给**创意说明（策略层）**再给设计 —— 像广告公司那样交活。
    诚实边界：没给 image_fn 就**不谎称已出图** —— 方案里写明「待生成」；
    写盘失败就返回 ok=False（绝不假装成功）。

    v0.30.13 两个新参数（都是给**对话路径**用的）：
    · `prebuilt={"copy":…, "brief":…, "prompt":…, "image":…}` —— 调用方已经算好
      甚至已经出好图的部分，命中就照用、不重算（省一次模型调用，也避免"聊天里
      显示的是 A 稿、落盘的却是 B 稿"）。
    · `record=False` —— 调用方**自己**已经开了一条工作任务（对话路径就是这样：
      `worklog` 那条 kind="ad" 的任务由聊天侧统一开、统一收尾），此时不要再开第二条。
      以前没有这个开关，广告在台账里会挂成**两条**（一条永远 running）。
    """
    import os
    import time as _t
    pb = prebuilt or {}
    cw = pb.get("copy") or copywrite(theme, product, llm_fn)
    brief = pb.get("brief") or creative_brief(theme, product, style, audience, llm_fn)
    prompt = pb.get("prompt") or build_prompt(theme, product, style)
    files, img_path, md_path = [], pb.get("image") or "", ""
    ok = True

    # ① 真出图（只有真拿到 image_fn 才做；调用方已给图就别重复出）
    if image_fn and not img_path:
        try:
            img_path = image_fn(prompt) or ""
        except Exception as e:  # noqa: BLE001
            img_path = ""
            ok = False
            cw["note"] = "出图失败：%s" % e

    # ② 真落盘方案 .md
    try:
        base = out_dir or os.path.join(
            os.environ.get("PASM_STUDIO_DIR") or os.path.expanduser("~"),
            "PASMStudio", "works", "广告设计")
        os.makedirs(base, exist_ok=True)
        safe = "".join(c for c in (product or theme)
                       if c not in '\\/:*?"<>|')[:24]
        fp = os.path.join(base, "广告方案_%s_%s.md"
                          % (safe or "未命名", _t.strftime("%Y%m%d_%H%M%S")))
        with open(fp, "w", encoding="utf-8") as f:
            f.write("# 广告方案：%s\n\n" % (product or theme))
            f.write("- 主题：%s\n- 风格：%s\n- 生成时间：%s\n- 文案来源：%s\n\n"
                    % (theme, style, _t.strftime("%Y-%m-%d %H:%M:%S"),
                       "模型生成" if cw.get("source") == "llm"
                       else "离线模板（未接入模型时的兜底，建议重跑）"))
            f.write("## 创意说明（策略层）\n\n"
                    "- **传播目标**：%s\n- **目标人群**：%s\n"
                    "- **核心主张**：%s\n- **调性**：%s\n"
                    "- **媒介/投放建议**：%s\n- **主视觉构思**：%s\n"
                    "- 策略来源：%s\n\n"
                    % (brief.get("goal", ""), brief.get("audience", ""),
                       brief.get("message", ""), brief.get("tone", ""),
                       brief.get("media", ""), brief.get("visual", ""),
                       "模型生成" if brief.get("source") == "llm"
                       else "离线模板（未接入模型时的兜底，建议重跑）"))
            f.write("## 文案\n\n- **标题**：%s\n- **正文**：%s\n"
                    "- **行动号召**：%s\n\n"
                    % (cw.get("headline", ""), cw.get("body", ""),
                       cw.get("cta", "")))
            f.write("## 主视觉提示词\n\n```\n%s\n```\n\n" % prompt)
            f.write("## 主视觉\n\n%s\n"
                    % (img_path or "（待生成：尚未接入出图引擎）"))
        md_path = fp
    except Exception as e:  # noqa: BLE001
        ok = False
        cw["note"] = "方案写盘失败：%s" % e

    # 产物顺序：**方案排第一**（它是这次交活的正文，图是配套素材）
    files = ([md_path] if md_path else []) + ([img_path] if img_path else [])
    tid = {}
    if record:
        t = WL.create(title="广告：%s" % ((product or theme)[:40]), kind="ad",
                      dir=dir, level=0,
                      next_step="主视觉 + 文案排版" if not img_path else "挑选版本 / 交付")
        tid = t
        for fp in files:
            WL.add_artifact(t["id"], fp)
        if not files:
            WL.set_next(t["id"], "⚠️ 未产出文件（写盘失败），请检查工作目录")
    return {"task_id": tid.get("id", ""), "ok": ok and bool(files), "copy": cw,
            "brief": brief, "prompt": prompt, "image": img_path, "files": files,
            "plan": md_path, "source": cw.get("source", "template")}


def executor(step: dict, ctx: dict = None) -> tuple:
    """workflow_engine 的执行器：把一步「广告设计」真做成文件。

    从步骤标题里取主题/产品；ctx 可给 out_dir / image_fn / llm_fn。
    """
    ctx = ctx or {}
    title = (step or {}).get("title") or (step or {}).get("detail") or "广告设计"
    topic = title.replace("广告", "").replace("设计", "").strip("：: -—") or title
    r = materialize(topic, ctx.get("product") or topic,
                    ctx.get("style") or "现代简约",
                    out_dir=ctx.get("out_dir") or "",
                    audience=ctx.get("audience") or "",
                    image_fn=ctx.get("image_fn"), llm_fn=ctx.get("llm_fn"))
    if r["ok"]:
        # v0.30.6：过了质检才算完成 —— 文件必须真实非空、文案必须不空不敷衍
        try:
            import executors as _EX                       # 懒导入：避免模块级循环
            _txt = ""
            for _f in (r.get("files") or []):
                if _f.lower().endswith((".md", ".txt")):
                    try:
                        import io as _io
                        _txt += _io.open(_f, encoding="utf-8", errors="replace").read()
                    except Exception:
                        pass
            _qa_ok, _probs = _EX.qa_check(_txt, r.get("files"), kind="generation", min_len=60)
            if not _qa_ok:
                return False, "⚠️ 广告方案未过质检（未标完成）：%s" % "；".join(_probs[:3]), []
        except Exception as _ex:
            logging.info("ad qa skipped: %r", _ex)
        _src = "模型文案" if r.get("source") == "llm" else "⚠️ 离线模板稿（建议接入模型后重跑）"
        return True, "✅ 已产出 %d 个文件 · %s%s" % (
            len(r["files"]), _src,
            "" if r["image"] else " ·（主视觉待生成）"), r["files"]
    return False, "❌ 广告设计未产出文件，请检查工作目录", r["files"]


if __name__ == "__main__":
    r = design("开学季限时优惠", "霖云智学 AI 伴学", "清新校园")
    print(r["copy"])
    print("prompt:", r["prompt"])
    print("SELFTEST_OK")
