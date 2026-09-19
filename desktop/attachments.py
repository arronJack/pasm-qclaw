# -*- coding: utf-8 -*-
"""附件内容提取 —— 把用户选中的文件转成「能塞进模型上下文」的文本。

为什么单独成模块：
  · 提文本这件事和界面无关，可以脱离 Qt 单独自检（``python attachments.py --selftest``）
  · 格式一多失败路径就多，集中在一处才能保证"任何失败都只是提示，
    绝不把异常冒到界面"

硬约束（防上下文被撑爆）：
  · 单文件提取上限 ``MAX_CHARS``（默认 6000 字符）
  · 单次最多 ``MAX_FILES``（默认 3）个文件
  · 任何异常都返回 ``⚠ ...`` 说明串，**绝不抛给调用方**

设计上刻意不引入新依赖：Word/PPT/Excel 用桌面端已有的 python-docx /
python-pptx / openpyxl；PDF 属于可选（没装 pypdf 就明确说"读不了"，
而不是装作读到了）。
"""
from __future__ import annotations

import os
import sys

MAX_CHARS = 6000
MAX_FILES = 3

TEXT_EXT = {
    ".txt", ".md", ".markdown", ".log", ".csv", ".tsv", ".json", ".yaml", ".yml",
    ".ini", ".cfg", ".conf", ".toml", ".xml", ".html", ".htm", ".css", ".scss",
    ".js", ".jsx", ".ts", ".tsx", ".py", ".java", ".c", ".h", ".cpp", ".hpp",
    ".cs", ".go", ".rs", ".php", ".rb", ".swift", ".kt", ".sh", ".bat", ".ps1",
    ".sql", ".vue", ".srt", ".ass", ".reg", ".env",
}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}

# 读文本时按顺序尝试的编码（中文环境最常见的就是 utf-8 / gbk 两种）
_ENCODINGS = ("utf-8-sig", "utf-8", "gbk", "gb18030", "utf-16", "latin-1")


def kind_of(path: str) -> str:
    """判断附件类型：image / text / docx / pptx / xlsx / pdf / binary。"""
    ext = os.path.splitext(str(path))[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in TEXT_EXT:
        return "text"
    if ext == ".docx":
        return "docx"
    if ext == ".pptx":
        return "pptx"
    if ext in (".xlsx", ".xlsm"):
        return "xlsx"
    if ext == ".pdf":
        return "pdf"
    return "binary"


def _clip(t: str, limit: int, tail_hint: bool = True) -> str:
    t = (t or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    # 尽量在换行处断开，避免截半个字
    nl = cut.rfind("\n")
    if nl > limit * 0.6:
        cut = cut[:nl]
    return cut + ("\n…（内容过长，已截断，原长 %d 字符）" % len(t) if tail_hint else "")


def _read_text(path: str, limit: int) -> str:
    raw = open(path, "rb").read()
    if b"\x00" in raw[:4096]:
        return "⚠ 该文件看起来是二进制（不是文本），无法提取文字"
    for enc in _ENCODINGS:
        try:
            return _clip(raw.decode(enc), limit)
        except (UnicodeDecodeError, LookupError):
            continue
    return "⚠ 编码无法识别，未能提取文字"


def _read_docx(path: str, limit: int) -> str:
    try:
        import docx
    except Exception:
        return "⚠ 未安装 python-docx，读不了 Word 文档"
    d = docx.Document(path)
    out = [p.text.strip() for p in d.paragraphs if p.text and p.text.strip()]
    tables = []
    for ti, tb in enumerate(d.tables[:4]):
        rows = []
        for row in tb.rows[:20]:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            tables.append("【表格 %d】\n%s" % (ti + 1, "\n".join(rows)))
    body = "\n".join(out)
    if tables:
        body += "\n\n" + "\n\n".join(tables)
    return _clip(body, limit)


def _read_pptx(path: str, limit: int) -> str:
    try:
        from pptx import Presentation
    except Exception:
        return "⚠ 未安装 python-pptx，读不了 PPT"
    prs = Presentation(path)
    pages = []
    for i, slide in enumerate(prs.slides, 1):
        texts = []
        for sh in slide.shapes:
            try:
                if sh.has_text_frame and sh.text_frame.text.strip():
                    texts.append(sh.text_frame.text.strip())
            except Exception:
                continue
        if texts:
            pages.append("【第 %d 页】%s" % (i, " / ".join(texts)))
    return _clip("\n".join(pages), limit)


def _read_xlsx(path: str, limit: int) -> str:
    try:
        import openpyxl
    except Exception:
        return "⚠ 未安装 openpyxl，读不了 Excel"
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    blocks = []
    try:
        for ws in wb.worksheets[:4]:
            rows = []
            for ri, row in enumerate(ws.iter_rows(max_row=30, values_only=True)):
                cells = ["" if c is None else str(c) for c in row]
                if any(c.strip() for c in cells):
                    rows.append(" | ".join(cells))
            if rows:
                blocks.append("【工作表 %s】\n%s" % (ws.title, "\n".join(rows)))
    finally:
        try:
            wb.close()
        except Exception:
            pass
    return _clip("\n\n".join(blocks), limit)


def _read_pdf(path: str, limit: int) -> str:
    for mod in ("pypdf", "PyPDF2"):
        try:
            m = __import__(mod)
            Reader = getattr(m, "PdfReader")
            rd = Reader(path)
            pages = []
            for pg in rd.pages[:20]:
                try:
                    pages.append(pg.extract_text() or "")
                except Exception:
                    continue
            txt = "\n".join(pages).strip()
            if not txt:
                return "⚠ 这个 PDF 没有文字层（可能是扫描件），需要 OCR 才能读取"
            return _clip(txt, limit)
        except ImportError:
            continue
        except Exception as e:
            return "⚠ 读取 PDF 失败：%s" % e
    return "⚠ 未安装 pypdf，暂不能读 PDF（可让助手先装：pip install pypdf）"


def extract(path: str, limit: int = MAX_CHARS) -> str:
    """提取单个附件的内容摘要；任何失败都返回 ``⚠ ...``，绝不抛异常。"""
    try:
        p = str(path)
        if not os.path.isfile(p):
            return "⚠ 文件不存在或已移动"
        k = kind_of(p)
        if k == "text":
            return _read_text(p, limit)
        if k == "docx":
            return _read_docx(p, limit)
        if k == "pptx":
            return _read_pptx(p, limit)
        if k == "xlsx":
            return _read_xlsx(p, limit)
        if k == "pdf":
            return _read_pdf(p, limit)
        if k == "image":
            return "（图片，走视觉理解通道）"
        size = os.path.getsize(p)
        return ("⚠ 二进制文件（%.1f KB），不支持提取文字 —— 已记录文件名，"
                "如需处理请说明想要什么" % (size / 1024.0))
    except Exception as e:
        return "⚠ 读取失败：%s: %s" % (type(e).__name__, e)


def summarize(items) -> str:
    """把 [{path, text}] 拼成一段可直接进模型上下文的说明块。"""
    items = list(items or [])
    if not items:
        return ""
    parts = ["【用户附上的文件 · 共 %d 个】" % len(items)]
    for it in items:
        try:
            nm = os.path.basename(str(it.get("path", "")))
            tx = str(it.get("text", ""))[:MAX_CHARS]
        except Exception:
            continue
        parts.append("- %s：\n%s" % (nm, tx))
    parts.append("（请结合上面的文件内容回答；若文件内容与问题无关，直接说明即可）")
    return "\n".join(parts)


# ——————————————————————————————————————————————————————
# 自检（全部在临时目录，不碰用户文件）
# ——————————————————————————————————————————————————————

def selftest() -> int:
    import tempfile

    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("PASS %s %s" % (name, extra))
        else:
            failed += 1
            print("FAIL %s %s" % (name, extra))

    tmp = tempfile.mkdtemp(prefix="pasm_att_")
    try:
        # 1) 类型判定
        check("kind_of txt", kind_of("a.txt") == "text")
        check("kind_of docx", kind_of("a.docx") == "docx")
        check("kind_of png", kind_of("a.png") == "image")
        check("kind_of 未知后缀", kind_of("a.xyz") == "binary")
        check("kind_of 大写后缀", kind_of("A.PDF") == "pdf")

        # 2) 文本读取（utf-8）
        p1 = os.path.join(tmp, "t1.md")
        open(p1, "w", encoding="utf-8").write("# 标题\n正文一行\n")
        r1 = extract(p1)
        check("读 utf-8 文本", "标题" in r1 and "正文一行" in r1, repr(r1[:24]))

        # 3) 文本读取（GBK —— 中文环境常见）
        p2 = os.path.join(tmp, "t2.txt")
        open(p2, "wb").write("中文内容测试".encode("gbk"))
        r2 = extract(p2)
        check("读 GBK 文本", "中文内容测试" in r2, repr(r2[:24]))

        # 4) 超长截断
        p3 = os.path.join(tmp, "big.txt")
        open(p3, "w", encoding="utf-8").write("行\n" * 9000)
        r3 = extract(p3, limit=500)
        check("超长被截断", len(r3) < 700 and "截断" in r3, "长度 %d" % len(r3))

        # 5) 二进制识别（不应吐乱码）
        p4 = os.path.join(tmp, "bin.dat")
        open(p4, "wb").write(b"\x00\x01\x02\xff" * 400)
        r4 = extract(p4)
        check("二进制被识别", r4.startswith("⚠"), r4[:30])

        # 6) 二进制文件（非文本后缀）
        p5 = os.path.join(tmp, "x.zip")
        open(p5, "wb").write(b"PK\x03\x04" + b"0" * 2000)
        r5 = extract(p5)
        check("未知二进制给提示", "二进制" in r5 or r5.startswith("⚠"), r5[:30])

        # 7) 不存在的文件
        r6 = extract(os.path.join(tmp, "nope.txt"))
        check("不存在的文件不抛异常", r6.startswith("⚠"), r6[:24])

        # 8) 目录当文件传（异常路径）
        r7 = extract(tmp)
        check("传目录不抛异常", isinstance(r7, str), r7[:24])

        # 9) summarize 拼接
        s = summarize([{"path": p1, "text": r1}])
        check("summarize 含文件名", "t1.md" in s and "共 1 个" in s, s[:30])
        check("summarize 空输入返回空", summarize([]) == "")

        # 10) 连续 3 次调用不残留状态
        a = extract(p1, limit=100)
        b = extract(p2, limit=100)
        check("连续调用互不干扰", ("标题" in a) and ("中文" in b))

        # 11) docx/pptx/xlsx 若依赖可用则真造一份验证
        try:
            import docx
            pd = os.path.join(tmp, "d.docx")
            doc = docx.Document()
            doc.add_heading("季度报告", 0)
            doc.add_paragraph("营收同比增长 18%")
            tb = doc.add_table(rows=2, cols=2)
            tb.cell(0, 0).text = "项目"
            tb.cell(0, 1).text = "金额"
            tb.cell(1, 0).text = "广告"
            tb.cell(1, 1).text = "12万"
            doc.save(pd)
            rd = extract(pd)
            check("读 docx 段落+表格",
                  "季度报告" in rd and "营收同比增长" in rd and "12万" in rd, rd[:40].replace("\n", "/"))
        except ImportError:
            print("SKIP docx（未安装 python-docx）")
        except Exception as e:
            check("读 docx", False, str(e)[:40])

        try:
            from pptx import Presentation
            pp = os.path.join(tmp, "p.pptx")
            prs = Presentation()
            sl = prs.slides.add_slide(prs.slide_layouts[5])
            sl.shapes.title.text = "产品路线图"
            prs.save(pp)
            rp = extract(pp)
            check("读 pptx", "产品路线图" in rp, rp[:40])
        except ImportError:
            print("SKIP pptx（未安装 python-pptx）")
        except Exception as e:
            check("读 pptx", False, str(e)[:40])

        try:
            import openpyxl
            px = os.path.join(tmp, "s.xlsx")
            wb = openpyxl.Workbook()
            ws = wb.active
            ws.title = "月报"
            ws.append(["月份", "销量"])
            ws.append(["1月", 1200])
            wb.save(px)
            rx = extract(px)
            check("读 xlsx", "月报" in rx and "1200" in rx, rx[:40].replace("\n", "/"))
        except ImportError:
            print("SKIP xlsx（未安装 openpyxl）")
        except Exception as e:
            check("读 xlsx", False, str(e)[:40])

    finally:
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass

    print("\n%d 项，%s" % (passed + failed, "全部通过" if failed == 0 else "%d 项失败" % failed))
    print("（自检全程在临时目录，未触碰你的任何文件）")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print(__doc__)
