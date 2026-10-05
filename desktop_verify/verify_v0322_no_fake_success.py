# -*- coding: utf-8 -*-
"""verify_v0322_no_fake_success.py —— 0.31.22「模型不可用不许假成功」守卫。

复盘 0.31.21 真机事故（2026-10-05）：DeepSeek Key 失效（401）时，
coder.gen_project 逐文件 except 把「# 生成失败（401 ...）」当正文写进
main.py，并记 ok=true、报「✅ 落盘 3 个文件」；update_project 更严重 ——
`except: pass` 吞掉故障后"回退全量重写"，把用户已有的正常代码覆写成报错。

本守卫锁死三条底线（任一被回退即红）：
  A. 模型不可用（401/无 key/网络故障）→ 立即中止、零写盘、如实报错；
  B. 错误文本冒充源码 → 绝不写盘；
  C. 正常路径不被误伤（模型可用时照常生成，续改照常生效）。

同时锁死 scaffold 标记精度（曾用 `rel in scaff` 导致真产出也被标骨架）。

跑法：python desktop_verify/verify_v0322_no_fake_success.py
"""
import os
import py_compile
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "desktop"))
sys.path.insert(0, ROOT)

E401 = ("Error code: 401 - {'error':{'message':'Authentication Fails, "
        "Your api key: ****84bd is invalid','code':'invalid_request_error'}}")

FAILS = []
CNT = [0]


def check(name, cond, extra=""):
    """自检断言。第一个参数必须是 str —— 机制上杜绝 check("x", "字符串") 这类恒真假绿。"""
    if not isinstance(name, str):
        raise TypeError("check() 第一个参数必须是 str（收到 %r）" % type(name))
    CNT[0] += 1
    print(("  [OK]   " if cond else "  [FAIL] ") + name
          + (("  " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        FAILS.append(name)


def nfiles(root):
    return sum(len(fs) for _, _, fs in os.walk(root))


def main():
    print("=== verify_v0322 no-fake-success ===")
    try:
        py_compile.compile(os.path.join(ROOT, "pasm", "cognitive", "coder.py"), doraise=True)
        check("coder.py 语法通过", True)
    except Exception as ex:
        check("coder.py 语法通过", False, ex)
        return 1

    import coder  # 与 pasm_companion 的 `import coder as CDR` 同路径
    check("执行模块为 pasm/cognitive/coder.py",
          os.path.normpath(coder.__file__).endswith(os.path.join("pasm", "cognitive", "coder.py")),
          coder.__file__)

    # ---------- A. 判据函数 ----------
    print("\n-- 判据函数 --")
    check("401 判为模型不可用", coder._is_model_down(E401))
    check("『没有可用模型』判为不可用", coder._is_model_down("没有可用模型（请配置云端 Key 或启动本地 Ollama）"))
    check("Traceback 判为不可用", coder._is_model_down("Traceback (most recent call last): x"))
    check("429 限流判为不可用", coder._is_model_down("429 Too Many Requests"))
    check("正常规划失败不误判", not coder._is_model_down("规划失败（模型没返回文件清单）。"))
    check("TypeError 不误判为模型故障",
          not coder._is_model_down("规划异常（unexpected keyword argument 'task'）"))
    check("错误文本判为坏内容", coder._is_bad_content("# 生成失败（Error code: 401）"))
    check("过短判为坏内容", coder._is_bad_content("# ok"))
    check("正常代码不误判", not coder._is_bad_content("def main():\n    print('x')\n"))
    check("『内容过短』不算错误文本（可降级）", not coder._looks_like_error("好"))

    # ---------- B. 模型 401：中止 + 零写盘 ----------
    print("\n-- 模型不可用必须中止且零写盘 --")
    def dead(s, h, task=""):
        raise RuntimeError(E401)

    t = tempfile.mkdtemp()
    r = coder.gen_project(dead, "geo spring-boot+vue", "python", "geo", t)
    check("gen_project 返回 ok=False", r.get("ok") is False, r.get("log"))
    check("gen_project 标记 model_down", r.get("model_down") is True)
    check("gen_project dir 为空（零写盘）", r.get("dir") == "", r.get("dir"))
    check("gen_project 磁盘零残留", nfiles(t) == 0, nfiles(t))
    check("gen_project log 含『模型不可用』", "模型不可用" in (r.get("log") or ""))
    shutil.rmtree(t, ignore_errors=True)

    # ---------- C. 模型不抛异常但把报错当正文 ----------
    print("\n-- 报错文本冒充源码必须中止 --")
    def leaky(s, h, task=""):
        if "JSON 数组" in s and "path" in s:
            return '[{"path":"main.py","purpose":"入口"}]'
        return "# 生成失败（Error code: 401 - Authentication Fails）"

    t = tempfile.mkdtemp()
    r = coder.gen_project(leaky, "x", "python", "geo", t)
    check("识别错误正文并中止", r.get("ok") is False and r.get("model_down") is True, r.get("log"))
    check("错误正文零残留", nfiles(t) == 0, nfiles(t))
    shutil.rmtree(t, ignore_errors=True)

    # ---------- D. 续改路径：中止且不毁现有文件（0.31.21 最严重的一处） ----------
    print("\n-- update_project 中止且不毁原文件 --")
    t = tempfile.mkdtemp()
    orig = "print('原有正常代码')"
    with open(os.path.join(t, "main.py"), "w", encoding="utf-8") as f:
        f.write(orig + "\n")
    ru = coder.update_project(dead, "spec", "加个登录功能", "python", t,
                              [{"path": "main.py", "ok": True}])
    check("update_project ok=False", ru.get("ok") is False, ru.get("log"))
    check("update_project model_down", ru.get("model_down") is True)
    with open(os.path.join(t, "main.py"), encoding="utf-8") as f:
        got = f.read().strip()
    check("原文件未被覆写", got == orig, got[:80])
    check("原文件不含报错文本", "生成失败" not in got)
    shutil.rmtree(t, ignore_errors=True)

    # ---------- E. 防误伤：模型可用时照常工作 ----------
    print("\n-- 正常路径不得被误伤 --")
    def good(s, h, task=""):
        if "JSON 数组" in s and "path" in s:
            return '[{"path":"main.py","purpose":"入口"},{"path":"geo_service.py","purpose":"服务"}]'
        if "main.py" in s:
            return "def main():\n    print('hello geo')\n"
        if "geo_service.py" in s:
            return "def serve():\n    return 'ready'\n"
        return "x"

    t = tempfile.mkdtemp()
    r = coder.gen_project(good, "geo", "python", "geo", t)
    check("模型可用 → ok=True", r.get("ok") is True, r.get("log"))
    planned = {f["path"]: f.get("scaffold") for f in r.get("files", [])
               if f["path"] in ("main.py", "geo_service.py")}
    check("计划内文件均标记为真产出（非 scaffold）",
          planned and all(v is False for v in planned.values()), planned)
    mp = os.path.join(r.get("dir", ""), "main.py")
    check("main.py 内容为模型真产出",
          os.path.isfile(mp) and "hello geo" in open(mp, encoding="utf-8").read())
    shutil.rmtree(t, ignore_errors=True)

    def upd(s, h, task=""):
        if "JSON 对象" in s:
            return '{"change":["main.py"]}'
        return "print('加了登录功能')\n"

    t = tempfile.mkdtemp()
    with open(os.path.join(t, "main.py"), "w", encoding="utf-8") as f:
        f.write("print('旧')\n")
    ru = coder.update_project(upd, "spec", "加个登录功能", "python", t,
                              [{"path": "main.py", "ok": True}])
    check("update_project 正常续改 ok=True", ru.get("ok") is True, ru.get("log"))
    with open(os.path.join(t, "main.py"), encoding="utf-8") as f:
        check("文件已正确更新", "加了登录功能" in f.read())
    shutil.rmtree(t, ignore_errors=True)

    # ---------- F. 内容过短 = 正常降级，不是故障 ----------
    print("\n-- 内容过短属正常降级 --")
    def lazy(s, h, task=""):
        if "JSON 数组" in s and "path" in s:
            return '[{"path":"main.py","purpose":"入口"}]'
        return "好"

    t = tempfile.mkdtemp()
    r = coder.gen_project(lazy, "x", "python", "geo", t)
    check("降级仍算成功交付", r.get("ok") is True, r.get("log"))
    check("降级如实标记 degraded=True", r.get("degraded") is True)
    shutil.rmtree(t, ignore_errors=True)

    # ---------- G. 代码围栏剥离（真机事故：main.py 首行 ```python → SyntaxError） ----------
    print("\n-- 代码围栏剥离（仅整段为代码时）--")
    S = coder._strip_fence
    check("成对围栏被剥", S("```python\nprint(1)\n```") == "print(1)", repr(S("```python\nprint(1)\n```")))
    check("无语言围栏被剥", S("```\nprint(1)\n```") == "print(1)", repr(S("```\nprint(1)\n```")))
    check("★被 max_tokens 截断(无闭合围栏)仍能剥",
          S("```python\nprint(1)\n") == "print(1)", repr(S("```python\nprint(1)\n")))
    check("无围栏纯代码原样保留", S("print(1)\n") == "print(1)")
    # 文档（README）绝不能被剥掉标题 —— 曾因贪心匹配把整篇文档吞成代码块
    md = "# 标题\n\n说明：\n\n```bash\npip install geo\n```\n\n结束"
    check("README 类文档完整保留", S(md) == md, repr(S(md)[:40]))
    check("README 标题未被吞", S(md).startswith("# 标题"))
    check("前置说明+围栏 保留原文", S("前面说明\n```python\nprint(1)\n```")
          == "前面说明\n```python\nprint(1)\n```")
    # 模型爱加的 XML 闭合尾巴（真机实测 report.py line 276 </final> → SyntaxError）
    check("</final> 尾巴被清（带围栏）",
          not S("```python\nprint(1)\n</final>\n```").rstrip().endswith("</final>"),
          repr(S("```python\nprint(1)\n</final>\n```")))
    check("★</final> 尾巴被清（无围栏路径也覆盖）",
          not S("print(1)\n</final>").rstrip().endswith("</final>"),
          repr(S("print(1)\n</final>")))
    check("</output> 尾巴被清",
          not S("```python\nx=1\n</output>```").rstrip().endswith("</output>"))
    # 剥出来的代码必须真能过语法检查
    t = tempfile.mkdtemp()
    for nm, raw in (("a.py", "```python\nprint(1)\n"),
                    ("b.py", "```python\nimport os\nprint(os.getcwd())\n```\n")):
        p = os.path.join(t, nm)
        with open(p, "w", encoding="utf-8") as f:
            f.write(S(raw))
        try:
            py_compile.compile(p, doraise=True)
            okc, errc = True, ""
        except Exception as ex:
            okc, errc = False, str(ex)[:60]
        check("剥离后 %s 语法通过" % nm, okc, errc)
    shutil.rmtree(t, ignore_errors=True)

    # ---------- H. 依赖一致性（真机实测：import typer 但 requirements 只写 fastapi） ----------
    print("\n-- 依赖一致性检查与自动补齐 --")
    M = coder._missing_deps
    t = tempfile.mkdtemp()
    with open(os.path.join(t, "main.py"), "w", encoding="utf-8") as f:
        f.write("import typer\nimport os\nprint(1)\n")
    with open(os.path.join(t, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("fastapi>=0.110\nuvicorn\n")
    miss = M(t, "python")
    check("抓出未声明的 typer", "typer" in miss, miss)
    check("已声明的 fastapi 不误报", "fastapi" not in miss, miss)
    check("标准库 os 不误报", "os" not in miss, miss)
    shutil.rmtree(t, ignore_errors=True)

    t = tempfile.mkdtemp()
    os.makedirs(os.path.join(t, "geo"))
    with open(os.path.join(t, "geo", "__init__.py"), "w", encoding="utf-8") as f:
        f.write("")
    with open(os.path.join(t, "main.py"), "w", encoding="utf-8") as f:
        f.write("import os\nfrom geo import x\nimport geo\n")
    with open(os.path.join(t, "requirements.txt"), "w", encoding="utf-8") as f:
        f.write("")
    check("本地包不误报", "geo" not in M(t, "python"), M(t, "python"))
    shutil.rmtree(t, ignore_errors=True)

    # 端到端：自动补齐写进 requirements
    t = tempfile.mkdtemp()
    def llm_dep(s, h, task=""):
        if "JSON 数组" in s and "path" in s:
            return '[{"path":"main.py","purpose":"入口"},{"path":"requirements.txt","purpose":"依赖"}]'
        if "main.py" in s:
            return "import typer\nimport os\n\ndef main():\n    print('ok')\n"
        return "fastapi>=0.110\n"
    r = coder.gen_project(llm_dep, "x", "python", "geo", t)
    check("返回 missing_deps 字段", "missing_deps" in r, list(r.keys()))
    check("检出 typer 缺失", "typer" in (r.get("missing_deps") or []), r.get("missing_deps"))
    reqp = os.path.join(r.get("dir", ""), "requirements.txt")
    with open(reqp, encoding="utf-8") as f:
        check("typer 已自动写入 requirements", "typer" in f.read())
    shutil.rmtree(t, ignore_errors=True)

    print("\n=== 结果：%d 项断言，失败 %d ===" % (CNT[0], len(FAILS)))
    if FAILS:
        print("FAILED: %s" % FAILS)
        return 1
    print("ALL GREEN")
    return 0


if __name__ == "__main__":
    sys.exit(main())
