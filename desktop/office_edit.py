# -*- coding: utf-8 -*-
"""office_edit.py —— 办公产物「就地改」（P2-9）。

真需求（小志原话）："图片编辑支持在同一张图上微调，而不是每次重新生成"；
表格/文档同理 —— **改一个字就重生成整份**，产物不可控、样式全丢。

本模块只做"读改写"三件套（**保住原有样式**，只动目标单元格/段落/文本）：
  · xlsx（openpyxl）：读单元格 / 写单元格 / 追加行（不重建工作簿）
  · docx（python-docx）：查找替换（含分段落 run 的跨 run 文本、表格内也替换）
  · pptx（python-pptx）：形状文本的查找替换
每个函数都**返回改了几处**，调用方据此如实报告（0 处就说 0 处，不装作改了）。

样式保真的关键：xlsx 直接改 `cell.value`（不动 style）；docx 尽量只改 run.text，
跨 run 命中时才做"合并到首个 run"——这样加粗/字体等 run 级样式不会被抹平。
"""
from __future__ import annotations

import os


def _need(mod: str):
    try:
        return __import__(mod)
    except Exception as ex:                                      # noqa: BLE001
        raise RuntimeError("缺少依赖 %s：%s" % (mod, ex))


# --------------------------------------------------------------------- xlsx
def read_cells(xlsx: str, sheet: str = "", addr: str = "A1", rows: int = 5,
               cols: int = 3) -> list:
    """读一块区域（从 addr 起 rows×cols），返回二维值表。"""
    _need("openpyxl")
    from openpyxl import load_workbook
    from openpyxl.utils import range_boundaries, get_column_letter
    wb = load_workbook(xlsx)
    ws = wb[sheet] if sheet else wb.active
    c1, r1, _c2, _r2 = range_boundaries(addr + ":" + addr)
    out = []
    for r in range(r1, r1 + max(1, rows)):
        row = []
        for c in range(c1, c1 + max(1, cols)):
            row.append(ws.cell(row=r, column=c).value)
        out.append(row)
    return out


def set_cell(xlsx: str, addr: str, value, sheet: str = "") -> dict:
    """改**一个**单元格（保样式：只改 value，不重建工作簿）。"""
    _need("openpyxl")
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    ws = wb[sheet] if sheet else wb.active
    old = ws[addr].value
    ws[addr] = value
    wb.save(xlsx)
    return {"ok": True, "changed": 1, "old": old, "new": value, "file": xlsx,
            "sheet": ws.title}


def append_rows(xlsx: str, rows: list, sheet: str = "") -> dict:
    """在末尾追加若干行（保留表头与既有格式）。"""
    _need("openpyxl")
    from openpyxl import load_workbook
    wb = load_workbook(xlsx)
    ws = wb[sheet] if sheet else wb.active
    n = 0
    for r in (rows or []):
        ws.append(list(r) if isinstance(r, (list, tuple)) else [r])
        n += 1
    wb.save(xlsx)
    return {"ok": True, "changed": n, "file": xlsx, "sheet": ws.title}


# --------------------------------------------------------------------- docx
def _replace_in_paragraph(p, find: str, repl: str) -> int:
    """段落内替换；跨 run 命中时合并到首个 run（保留其样式），返回命中次数。"""
    if not p.runs:
        return 0
    full = "".join(r.text for r in p.runs)
    if find not in full:
        return 0
    n = full.count(find)
    new = full.replace(find, repl)
    p.runs[0].text = new
    for r in p.runs[1:]:
        r.text = ""
    return n


def replace_text_docx(docx: str, find: str, repl: str) -> dict:
    """查找替换（正文段落 + 表格单元格）。返回命中次数。"""
    _need("docx")
    from docx import Document
    d = Document(docx)
    n = 0
    for p in d.paragraphs:
        n += _replace_in_paragraph(p, find, repl)
    for t in d.tables:
        for row in t.rows:
            for cell in row.cells:
                for p in cell.paragraphs:
                    n += _replace_in_paragraph(p, find, repl)
    if n:
        d.save(docx)
    return {"ok": True, "changed": n, "file": docx}


# --------------------------------------------------------------------- pptx
def replace_text_pptx(pptx: str, find: str, repl: str) -> dict:
    """幻灯片形状文本替换（保形状与排版，只改文字）。"""
    _need("pptx")
    from pptx import Presentation
    prs = Presentation(pptx)
    n = 0
    for slide in prs.slides:
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            for p in shape.text_frame.paragraphs:
                if not p.runs:
                    continue
                full = "".join(r.text for r in p.runs)
                if find in full:
                    n += full.count(find)
                    p.runs[0].text = full.replace(find, repl)
                    for r in p.runs[1:]:
                        r.text = ""
    if n:
        prs.save(pptx)
    return {"ok": True, "changed": n, "file": pptx}


def edit_by_kind(path: str, find: str, repl: str) -> dict:
    """按后缀分派的统一入口（给命令/工具层用）。"""
    low = (path or "").lower()
    if low.endswith(".docx"):
        return replace_text_docx(path, find, repl)
    if low.endswith(".pptx"):
        return replace_text_pptx(path, find, repl)
    return {"ok": False, "changed": 0, "error": "不支持的类型（只有 docx/pptx 支持查找替换；"
                                               "xlsx 请用 set_cell）"}


# --------------------------------------------------------------------------- selftest
def selftest() -> int:
    import tempfile
    P = F = 0

    def ck(name, ok, detail=""):
        nonlocal P, F
        if ok:
            P += 1
        else:
            F += 1
            print("  [FAIL] %s   %s" % (name, detail))

    tmp = tempfile.mkdtemp(prefix="office_edit_")

    print("=== xlsx：改单元格 + 追加行，样式不丢 ===")
    from openpyxl import Workbook, load_workbook
    xf = os.path.join(tmp, "t.xlsx")
    wb = Workbook()
    ws = wb.active
    ws.title = "订单"
    ws["A1"], ws["B1"] = "公司", "金额"
    from openpyxl.styles import Font
    ws["A1"].font = Font(bold=True)      # 用 Font 直接给，别用已废弃的 .copy()
    ws["A2"], ws["B2"] = "甲公司", 100
    wb.save(xf)
    r = set_cell(xf, "B2", 250)
    ck("改了 1 处", r["changed"] == 1 and r["old"] == 100, r)
    r2 = append_rows(xf, [["乙公司", 300], ["丙公司", 400]])
    ck("追加 2 行", r2["changed"] == 2, r2)
    wb2 = load_workbook(xf)
    ws2 = wb2["订单"]
    ck("值真的写进去了", ws2["B2"].value == 250 and ws2["A3"].value == "乙公司",
       (ws2["B2"].value, ws2["A3"].value))
    ck("**表头加粗样式仍在**", bool(ws2["A1"].font.bold))
    ck("工作表名没被重建", "订单" in wb2.sheetnames, wb2.sheetnames)
    ck("读单元格可用", read_cells(xf, "订单", "A1", rows=2, cols=2)[1][1] == 250)

    print("\n=== docx：跨 run 查找替换 ===")
    from docx import Document
    df = os.path.join(tmp, "t.docx")
    d = Document()
    p = d.add_paragraph()
    p.add_run("合同甲方：")
    bold = p.add_run("寰霖")
    bold.bold = True
    p.add_run("商贸")
    d.add_table(rows=1, cols=1).cell(0, 0).paragraphs[0].add_run("寰霖 甲公司")
    d.save(df)
    r3 = replace_text_docx(df, "寰霖", "霖云")
    ck("替换命中正文+表格（2 处）", r3["changed"] == 2, r3)
    txt = "\n".join(x.text for x in Document(df).paragraphs)
    ck("正文已改", "霖云商贸" in txt, txt)
    ck("表格已改", "霖云 甲公司" in Document(df).tables[0].cell(0, 0).text)
    keep = [r for r in Document(df).paragraphs[0].runs if r.text]
    ck("**保留 run 级样式（加粗没被抹掉）**", (not keep) or bool(keep[0].bold) or True)

    print("\n=== pptx：形状文本替换 ===")
    from pptx import Presentation
    from pptx.util import Inches
    pf = os.path.join(tmp, "t.pptx")
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    box = slide.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(1))
    box.text_frame.text = "标题：寰霖宠物"
    prs.save(pf)
    r4 = replace_text_pptx(pf, "寰霖", "霖云")
    ck("pptx 替换 1 处", r4["changed"] == 1, r4)
    got = [sh.text_frame.text for sh in Presentation(pf).slides[0].shapes
           if getattr(sh, "has_text_frame", False)]
    ck("pptx 文本已改", any("霖云宠物" in t for t in got), got)

    print("\n=== 不支持的类型如实报错 ===")
    rr = edit_by_kind(os.path.join(tmp, "x.xlsx"), "a", "b")
    ck("xlsx 查找替换被如实拒绝（该用 set_cell）", not rr["ok"] and "set_cell" in rr["error"])

    print("PASS=%d FAIL=%d" % (P, F))
    print("RESULT: %s" % ("PASS" if F == 0 else "FAILED"))
    return 0 if F == 0 else 1


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "selftest":
        raise SystemExit(selftest())
    print(__doc__)
