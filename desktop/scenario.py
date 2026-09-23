# -*- coding: utf-8 -*-
"""场景导入：把 pasm-customer-service 产出的场景配置一键载入 Studio。

背景与边界（诚实说明）
----------------------
`pasm-customer-service` 的 `pasm-cs studio` 会产出
``<数据目录>/scenarios/<agent_id>.json``（schema = ``pasm-studio-scenario/v1``），
内含人格（name/role/tone/temper/energy/play）与知识源（csv / sqlite / demo / inline）。

在本模块之前，**Studio 侧没有任何入口读它** —— 配置生成了也白生（很讽刺：
`pasm-cs studio` 的输出里还写着"Studio 当前需加「导入场景」入口方可一键加载"）。
这里把「列出 / 校验 / 读知识源 / 灌资料库」四件事做齐，设置面板「📦 场景」页消费它；
也可脱离 Qt 单独 ``--selftest``。

设计约束（为什么这么写）
------------------------
1. **零 Qt、零第三方依赖**：可无 GUI 自检；读 CSV / SQLite 只用标准库。
   **不要求装 `pasm-customer-service`** —— 它不是 Studio 的依赖，
   装不装都得能用，所以这里自带一份等价的最小读取实现。
2. **目录单一来源**：一律走 ``logsetup.data_dir()``（env ``PASM_STUDIO_DIR`` 优先）。
   自己拼 ``%APPDATA%`` 会让「隔离态 / 第二实例」下 Studio **找不到自己生成的场景**。
3. **失败即降级**：任何一步出错只返回 ``ok=False + reason``，绝不冒异常到界面层。
4. **坏文件要看得见**：解析失败的场景进 ``errors``，不假装"这里没有场景"。
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys

#: 场景配置的 schema 标识（与 pasm-customer-service/studio_loader.py 对齐）
SCHEMA_ID = "pasm-studio-scenario/v1"

#: 硬限额 —— 防止一个坏场景把内存 / 资料库撑爆
MAX_SCENARIOS = 200
MAX_SOURCES = 20
MAX_ITEMS_PER_SOURCE = 3000
MAX_CONTENT_CHARS = 40000          # 与 knowledge.record 的 text 上限保持一致

#: Studio 认识的人格字段（其余字段一律丢弃，别把陌生键塞进配置）
PERSONA_KEYS = ("name", "role", "tone", "temper", "energy", "play")

#: 内置演示知识 —— 等价于 pasm-customer-service 的 `demo` 源。
#: 之所以在 Studio 侧也留一份：`demo` 的语义就是"开箱即有东西可聊"，
#: 不该反过来依赖另一个仓库装没装。
DEMO_ROWS = (
    ("会员等级与权益", "黄金会员全年包邮、专属客服、生日礼券。", "会员"),
    ("退换货政策", "签收 7 天内无理由退货，生鲜除外。", "售后"),
    ("配送时效", "现货 24h 发货，偏远地区 3-5 天。", "物流"),
    ("电子发票", "发货次日发送电子发票至注册邮箱。", "财务"),
    ("会员积分", "消费 1 元积 1 分，100 分抵 1 元。", "会员"),
    ("售后服务", "支持 7×12 小时在线客服，紧急工单 2 小时内响应。", "售后"),
)


class ScenarioError(Exception):
    """场景文件不可用（文件缺失 / JSON 坏 / schema 不符）。"""


# ============================================================ 目录
def scenarios_dir() -> str:
    """场景目录：``<Studio 数据目录>/scenarios``。

    ⚠️ 必须与 Studio 的其它数据同源（``logsetup.data_dir()``）——
    否则隔离态（``PASM_STUDIO_DIR``）下，`pasm-cs studio` 写到 AppData、
    Studio 却去读隔离目录，表现为"生成的场景一个都看不见"。
    """
    try:
        import logsetup
        base = logsetup.data_dir()
    except Exception:
        # logsetup 缺席（脱离 Studio 单跑）时按同一优先级对齐，别自己发明第三套规则
        base = (os.environ.get("PASM_STUDIO_DIR")
                or os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                                "PASMStudio"))
    return os.path.join(base, "scenarios")


# ============================================================ 列出 / 读取
def list_scenarios(directory: str | None = None) -> dict:
    """列出可用场景。

    返回 ``{"dir": 目录, "items": [...], "errors": [...]}``。
    **坏文件进 errors 而不是被忽略** —— 否则用户会以为"我明明生成了却没有"。
    """
    d = directory or scenarios_dir()
    out = {"dir": d, "items": [], "errors": []}
    try:
        names = sorted(n for n in os.listdir(d) if n.lower().endswith(".json"))
    except FileNotFoundError:
        # 目录还不存在 = 没生成过场景，属正常状态，不算错误
        return out
    except Exception as ex:                                     # noqa: BLE001
        out["errors"].append("无法读取场景目录：%s" % ex)
        return out

    for name in names[:MAX_SCENARIOS]:
        path = os.path.join(d, name)
        try:
            spec = load_scenario(path)
        except ScenarioError as ex:
            out["errors"].append("%s：%s" % (name, ex))
            continue
        except Exception as ex:                                 # noqa: BLE001
            out["errors"].append("%s：读取失败（%s）" % (name, ex))
            continue
        out["items"].append({
            "file": name,
            "path": path,
            "agent_id": spec.get("agent_id") or os.path.splitext(name)[0],
            "display_name": spec.get("display_name") or spec.get("agent_id") or name,
            "persona": {k: spec.get("persona", {}).get(k) for k in PERSONA_KEYS},
            "source_count": len(spec.get("knowledge_sources") or []),
            "capabilities": [c.get("name") for c in (spec.get("capabilities") or [])
                             if isinstance(c, dict) and c.get("name")],
            "note": spec.get("note") or "",
        })
    if len(names) > MAX_SCENARIOS:
        out["errors"].append("场景数量超过 %d，只列出前 %d 个" % (MAX_SCENARIOS, MAX_SCENARIOS))
    return out


def load_scenario(path: str) -> dict:
    """读取并校验一个场景文件；不可用则抛 ``ScenarioError``（带可读原因）。"""
    if not os.path.isfile(path):
        raise ScenarioError("文件不存在")
    try:
        with open(path, "r", encoding="utf-8") as f:
            spec = json.load(f)
    except json.JSONDecodeError as ex:
        raise ScenarioError("JSON 解析失败（第 %d 行）：%s" % (ex.lineno, ex.msg))
    except Exception as ex:                                     # noqa: BLE001
        raise ScenarioError("读取失败：%s" % ex)
    if not isinstance(spec, dict):
        raise ScenarioError("顶层必须是对象，实际是 %s" % type(spec).__name__)
    schema = spec.get("schema")
    if schema != SCHEMA_ID:
        # 版本不符就明确拒绝 —— 沉默兼容陌生 schema 迟早读出半个错配置
        raise ScenarioError("schema 不符（期望 %s，实际 %r）" % (SCHEMA_ID, schema))
    return spec


# ============================================================ 人格
def persona_patch(spec: dict) -> dict:
    """取出 Studio 认识的人格字段（丢弃陌生键）。缺失/空值自动略过。"""
    src = spec.get("persona") or {}
    out = {}
    for k in PERSONA_KEYS:
        v = src.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        out[k] = v
    return out


# ============================================================ 知识源
def _pick(row: dict, keys) -> str:
    for k in keys:
        if k in row:
            v = row.get(k)
            if v is not None and str(v).strip():
                return str(v).strip()
    return ""


def _row_to_item(row: dict) -> dict:
    """把一行（CSV / SQLite / JSON）规整成 ``{title, content, tags}``。"""
    title = _pick(row, ("title", "name", "question", "q", "id"))
    content = _pick(row, ("content", "description", "desc", "answer", "a", "text"))
    tag = _pick(row, ("tags", "category", "cat", "tag"))
    tags = [t.strip() for t in tag.replace("|", ",").replace("，", ",").split(",") if t.strip()]
    return {"title": title, "content": content, "tags": tags}


def _src_path(src: dict) -> str:
    """取知识源的文件路径：生成方给的 ``abs_path`` 优先（本机直接命中），
    否则回退相对 ``path``（由 ``resolve_asset`` 按场景目录 / 上一层去找）。

    为什么两者都要：``path`` 是相对于**生成方仓库**的（换台机器就不存在），
    单靠它会让「导入时 CSV/SQLite 全线失败」；``abs_path`` 是本机绝对路径，
    单靠它又不可移植。生成方两个都写，这里按"能命中优先"读。
    """
    return str(src.get("abs_path") or src.get("path") or "")


def resolve_asset(path: str, base_dir: str) -> str | None:
    """解析场景里声明的资源路径。找不到返回 ``None``（**不抛异常**，由调用方记因）。

    场景里的 ``path`` 通常是**生成方仓库**的相对路径（如 ``examples/products.csv``），
    换台机器就不存在了。所以按「绝对 → 场景同目录 → 上一层」依次试，
    全都落空时如实返回 None，而不是拿一个错路径去 open。
    """
    if not path:
        return None
    if os.path.isabs(path):
        return path if os.path.exists(path) else None
    cands = []
    if base_dir:
        cands.append(os.path.join(base_dir, path))
        cands.append(os.path.join(os.path.dirname(base_dir.rstrip("\\/")), path))
    cands.append(os.path.abspath(path))
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def read_source(src: dict, base_dir: str = "", max_items: int = MAX_ITEMS_PER_SOURCE) -> list:
    """把一个知识源读成 ``[{title, content, tags}]``。

    支持 ``inline`` / ``csv`` / ``sqlite`` / ``jsonl`` / ``json`` / ``demo``。
    读不到就抛 ``ScenarioError``（由 ``ingest`` 收进 errors，不影响其它源）。
    """
    if not isinstance(src, dict):
        raise ScenarioError("知识源必须是对象")
    kind = str(src.get("type") or "demo").lower()
    where = src.get("source_name") or kind

    if kind == "inline":
        items = src.get("items") or []
        if not isinstance(items, list):
            raise ScenarioError("inline 源的 items 必须是数组")
        return [_row_to_item(it) for it in items[:max_items] if isinstance(it, dict)]

    if kind == "demo":
        return [{"title": t, "content": c, "tags": [g]} for t, c, g in DEMO_ROWS[:max_items]]

    if kind == "csv":
        p = resolve_asset(_src_path(src), base_dir)
        if not p:
            raise ScenarioError("CSV 文件找不到：%s" % (_src_path(src) or "(未填)"))
        # utf-8-sig：兼容 Excel 另存的带 BOM 文件（本项目演示 CSV 就是这种）
        with open(p, "r", encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        return [_row_to_item(r) for r in rows[:max_items]]

    if kind == "sqlite":
        p = resolve_asset(_src_path(src), base_dir)
        if not p:
            raise ScenarioError("SQLite 文件找不到：%s" % (_src_path(src) or "(未填)"))
        table = src.get("table") or "faq"
        con = sqlite3.connect(p)
        con.row_factory = sqlite3.Row
        try:
            cur = con.execute("SELECT * FROM %s LIMIT %d" % (table, int(max_items)))
            return [_row_to_item(dict(r)) for r in cur.fetchall()]
        except sqlite3.Error as ex:
            raise ScenarioError("读 SQLite 表 %s 失败：%s" % (table, ex))
        finally:
            con.close()

    if kind in ("jsonl", "json"):
        p = resolve_asset(_src_path(src), base_dir)
        if not p:
            raise ScenarioError("JSON 文件找不到：%s" % (_src_path(src) or "(未填)"))
        with open(p, "r", encoding="utf-8-sig") as f:
            raw = f.read()
        if kind == "jsonl":
            rows = [json.loads(ln) for ln in raw.splitlines() if ln.strip()]
        else:
            rows = json.loads(raw)
            if isinstance(rows, dict):
                rows = rows.get("items") or rows.get("data") or []
        if not isinstance(rows, list):
            raise ScenarioError("JSON 内容不是数组")
        return [_row_to_item(r) for r in rows[:max_items] if isinstance(r, dict)]

    if kind in ("sql", "rest"):
        # 需要外部凭据 / 网络的源不在导入期跑 —— 如实拒绝而不是假装成功
        raise ScenarioError("%s 源需要在生成方执行（本机导入不支持）" % kind)

    raise ScenarioError("不支持的源类型：%s" % kind)


def ingest(spec: dict, base_dir: str, recorder) -> dict:
    """把场景声明的知识源灌进资料库。

    ``recorder`` 由调用方注入（依赖倒置 —— 自检时给个假的就能验全流程）：
    签名 ``recorder(title, bullets, topic, text, src) -> int``，与
    ``knowledge.record`` 一致，直接传 ``knowledge.record`` 即可。

    ``ok`` 的口径：**至少一个源成功导入**。全军覆没就是 False，
    界面据此如实提示，绝不"就算导入了吧"。
    """
    result = {"ok": False, "imported": 0, "sources": [], "errors": [],
              "agent_id": spec.get("agent_id") or "", "base_dir": base_dir}
    srcs = spec.get("knowledge_sources") or []
    if not isinstance(srcs, list):
        result["errors"].append("knowledge_sources 必须是数组")
        return result
    if len(srcs) > MAX_SOURCES:
        result["errors"].append("知识源超过 %d 个，只导入前 %d 个" % (MAX_SOURCES, MAX_SOURCES))
        srcs = srcs[:MAX_SOURCES]

    for s in srcs:
        label = (s or {}).get("source_name") or (s or {}).get("type") or "?"
        try:
            items = read_source(s, base_dir)
        except ScenarioError as ex:
            result["errors"].append("%s：%s" % (label, ex))
            continue
        except Exception as ex:                                 # noqa: BLE001
            result["errors"].append("%s：读取失败（%s）" % (label, ex))
            continue

        n = 0
        for it in items:
            title = (it.get("title") or "").strip()
            if not title:
                continue
            body = it.get("content") or ""
            try:
                recorder(title=title, bullets=[], topic=title,
                         text=body[:MAX_CONTENT_CHARS], src="场景导入·%s" % label)
                n += 1
            except Exception as ex:                             # noqa: BLE001
                result["errors"].append("%s／%s：写入失败（%s）" % (label, title, ex))
        result["sources"].append({"name": label, "type": (s or {}).get("type"), "count": n})
        result["imported"] += n

    result["ok"] = any(x["count"] > 0 for x in result["sources"])
    if not result["sources"] and not result["errors"]:
        result["errors"].append("该场景没有声明任何知识源")
    return result


def apply_persona(spec: dict, cfg: dict) -> list:
    """把场景人格写进 Studio 配置字典（就地修改）。返回被改动的键名列表。"""
    patch = persona_patch(spec)
    changed = []
    for k, v in patch.items():
        if cfg.get(k) != v:
            cfg[k] = v
            changed.append(k)
    return changed


# ============================================================ 自检
def selftest() -> int:
    import shutil
    import tempfile

    passed = failed = 0

    def check(name, cond, extra=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print("  [OK]   %s" % name)
        else:
            failed += 1
            print("  [FAIL] %s %s" % (name, extra))

    tmp = tempfile.mkdtemp(prefix="pasm_scenario_")
    try:
        print("== 场景导入自检 ==")
        sc_dir = os.path.join(tmp, "scenarios")
        os.makedirs(sc_dir, exist_ok=True)

        good = {
            "schema": SCHEMA_ID, "agent_id": "shop-cs", "display_name": "小智·专业客服",
            "persona": {"name": "小智", "role": "智能客服", "tone": "温暖",
                        "temper": 0.6, "energy": 0.5, "play": 0.4, "unknown_key": "忽略我"},
            "knowledge_sources": [
                {"type": "inline", "source_name": "内联",
                 "items": [{"title": "退货政策", "content": "7 天无理由退货。", "category": "售后"},
                           {"title": "配送时效", "content": "24h 发货。", "tags": "物流,时效"}]},
                {"type": "demo", "source_name": "演示"},
            ],
            "capabilities": [{"name": "query_order", "description": "查订单"}],
        }
        with open(os.path.join(sc_dir, "shop-cs.json"), "w", encoding="utf-8") as f:
            json.dump(good, f, ensure_ascii=False)

        # --- 坏文件必须被当作错误报出来，而不是静默消失（反例对照）---
        with open(os.path.join(sc_dir, "bad-json.json"), "w", encoding="utf-8") as f:
            f.write("{ 这不是 json")
        with open(os.path.join(sc_dir, "bad-schema.json"), "w", encoding="utf-8") as f:
            json.dump({"schema": "someone-else/v9", "agent_id": "x"}, f)

        listing = list_scenarios(sc_dir)
        check("列出 1 个可用场景", len(listing["items"]) == 1, listing)
        check("★ 反例：2 个坏文件被记进 errors（不是静默忽略）",
              len(listing["errors"]) == 2, listing["errors"])
        check("坏 JSON 的原因可读", any("JSON 解析失败" in e for e in listing["errors"]),
              listing["errors"])
        check("schema 不符被明确拒绝", any("schema 不符" in e for e in listing["errors"]),
              listing["errors"])
        it = listing["items"][0]
        check("读到 display_name", it["display_name"] == "小智·专业客服", it)
        check("读到 2 个知识源", it["source_count"] == 2, it)

        # 目录不存在 = 正常状态（还没生成过），不该报错
        empty = list_scenarios(os.path.join(tmp, "nope"))
        check("目录不存在时返回空表且无错误",
              empty["items"] == [] and empty["errors"] == [], empty)

        # --- 人格：只取认识的键 ---
        spec = load_scenario(os.path.join(sc_dir, "shop-cs.json"))
        patch = persona_patch(spec)
        check("人格取到 6 个字段", len(patch) == 6, patch)
        check("★ 反例：陌生键 unknown_key 被丢弃", "unknown_key" not in patch, patch)
        cfg = {"name": "小U"}
        changed = apply_persona(spec, cfg)
        check("人格写进配置：name 变了", cfg["name"] == "小智", cfg)
        check("人格写进配置：tone 变了", cfg.get("tone") == "温暖", cfg)
        check("★ 反例：陌生键 unknown_key 未被写进配置", "unknown_key" not in cfg, cfg)
        check("changed 报告全部 6 个人格键（原本只有 name）",
              set(changed) == {"name", "role", "tone", "temper", "energy", "play"}, changed)
        check("重复应用是幂等的", apply_persona(spec, cfg) == [], cfg)

        # --- 灌库：用假 recorder 验全流程 ---
        sink = []

        def fake_recorder(title, bullets, topic, text, src):
            sink.append({"title": title, "text": text, "src": src})
            return len(sink)

        res = ingest(spec, sc_dir, fake_recorder)
        check("导入成功（ok=True）", res["ok"] is True, res)
        check("内联 2 条 + 演示 %d 条" % len(DEMO_ROWS),
              res["imported"] == 2 + len(DEMO_ROWS), res)
        check("写入内容与原文一致", any(x["text"] == "7 天无理由退货。" for x in sink), sink[:3])
        check("来源标注了具体源名", any("内联" in x["src"] for x in sink), sink[:3])
        check("每个源都记了条数", len(res["sources"]) == 2, res["sources"])

        # --- 反向对照：源失效时不许谎报成功 ---
        spec_bad = dict(spec)
        spec_bad["knowledge_sources"] = [{"type": "csv", "path": "does/not/exist.csv",
                                          "source_name": "坏源"}]
        res_bad = ingest(spec_bad, sc_dir, fake_recorder)
        check("★ 反例：源文件缺失 → 记错误且 ok=False", res_bad["ok"] is False, res_bad)
        check("★ 反例：错误信息含源名与原因",
              any("坏源" in e and "找不到" in e for e in res_bad["errors"]), res_bad["errors"])

        # 一个源坏、一个源好 → 整体仍算成功，但错误要留痕
        spec_mix = dict(spec)
        spec_mix["knowledge_sources"] = [
            {"type": "csv", "path": "nope.csv", "source_name": "坏源"},
            {"type": "inline", "source_name": "好源",
             "items": [{"title": "混合源", "content": "内容"}]},
        ]
        res_mix = ingest(spec_mix, sc_dir, fake_recorder)
        check("★ 部分失败不拖垮整体（ok=True 且 errors 留痕）",
              res_mix["ok"] is True and len(res_mix["errors"]) == 1, res_mix)

        # --- 真实 CSV / SQLite 读取（造真文件，不打桩）---
        csv_p = os.path.join(tmp, "products.csv")
        with open(csv_p, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "title", "description", "category"])
            w.writerow(["P001", "会员权益", "全年包邮", "会员"])
        rows = read_source({"type": "csv", "path": csv_p}, tmp)
        check("真读 CSV：1 条", len(rows) == 1, rows)
        check("真读 CSV：标题与内容正确",
              rows[0]["title"] == "会员权益" and rows[0]["content"] == "全年包邮", rows)
        check("真读 CSV：标签来自 category", rows[0]["tags"] == ["会员"], rows)

        db_p = os.path.join(tmp, "cs.db")
        con = sqlite3.connect(db_p)
        con.execute("CREATE TABLE faq(id TEXT, title TEXT, description TEXT, category TEXT)")
        con.execute("INSERT INTO faq VALUES('F1','退货','7天无理由','售后')")
        con.commit()
        con.close()
        rows = read_source({"type": "sqlite", "path": db_p, "table": "faq"}, tmp)
        check("真读 SQLite：1 条且字段对",
              len(rows) == 1 and rows[0]["title"] == "退货"
              and rows[0]["content"] == "7天无理由", rows)

        # --- 路径解析：相对路径按场景目录解析，找不到就老实说 ---
        rel = resolve_asset("products.csv", tmp)
        check("相对路径能按场景目录解析", rel == csv_p, rel)
        check("★ 反例：不存在的路径返回 None（不乱猜）",
              resolve_asset("ghost.csv", tmp) is None)
        check("★ 反例：csv 源路径不存在时抛可读错",
              _raises(lambda: read_source({"type": "csv", "path": "ghost.csv"}, tmp)),
              "")

        # --- abs_path：生成方额外给的绝对路径，本机应优先命中 ---
        rows = read_source({"type": "csv", "path": "examples/nowhere.csv",
                            "abs_path": csv_p}, tmp)
        check("★ abs_path 优先：path 根本不存在也能读到（本机直命中）",
              len(rows) == 1 and rows[0]["title"] == "会员权益", rows)
        check("★ 反例：abs_path 与 path 都不存在时照样失败（不瞎猜）",
              _raises(lambda: read_source(
                  {"type": "csv", "path": "ghost.csv", "abs_path": "ghost2.csv"}, tmp)))

        # --- 上限：写入层把超长正文截断（防资料库被撑爆）---
        lens = []
        ingest({"agent_id": "t", "knowledge_sources": [
            {"type": "inline", "source_name": "长文",
             "items": [{"title": "超长", "content": "x" * (MAX_CONTENT_CHARS + 500)}]}]},
            sc_dir,
            lambda title, bullets, topic, text, src: lens.append(len(text)))
        check("★ 超长正文在**写入层**被截到 %d 字" % MAX_CONTENT_CHARS,
              lens == [MAX_CONTENT_CHARS], lens)
        check("★ 反例：正常长度正文一丝不动",
              read_source({"type": "inline",
                           "items": [{"title": "短文", "content": "正常"}]},
                          tmp)[0]["content"] == "正常")

        print("\n结果：%d 项，%s" % (passed + failed,
                                   "全部通过" if not failed else "%d 项失败" % failed))
        return 1 if failed else 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except ScenarioError:
        return True
    except Exception:                                           # noqa: BLE001
        return False


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        raise SystemExit(selftest())
    print("这是 PASM Studio 的场景导入模块；用法：python scenario.py --selftest")
    print("场景目录：%s" % scenarios_dir())
