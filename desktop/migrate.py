"""migrate —— 一次性数据迁移（v0.29 起）。

为什么需要这个模块：
    改「默认名」只对**新用户**生效。老用户只要打开过一次设置并保存，
    config.json 里就已经落了一个 name 字段（值就是当年的默认名）——
    此后无论默认值改成什么，读到的都是那个旧值。必须显式迁移一次。

迁移原则（保守，宁可少动不可错动）：
    只有 name **恰好等于旧默认名**时才改写 —— 那视为"用户从未自定义过"。
    用户若已改成别的名字，一律尊重不动（哪怕只差一个字）。

安全性：
    * 写盘用「写临时文件 + os.replace」原子替换，避免中途崩溃留下半个配置。
    * 任何异常都不抛给调用方（启动路径上不能因为迁移失败而打不开应用）。
    * 自检在临时目录上跑，**绝不触碰真实 config.json**。

用法：pasm_main.py 启动时调一次 run_once()；也可单跑自检 `python migrate.py --selftest`。
"""
from __future__ import annotations

import json
import os
import sys

# 旧版默认名（用拼接写：全局改名脚本会把字面量换掉，这里必须保留原值才能匹配老配置）
# 历代默认名：凡是"恰好等于某一代默认名"的配置，都视为用户从未自定义过。
# 用拼接写法保留原值 —— 全局改名脚本会替换字面量，这里必须原样留存才能匹配。
LEGACY_NAMES = ("小" + "伴", "小" + "霖")
NEW_DEFAULT_NAME = "小U"

_DATA_DIR_OVERRIDE = None      # 仅供自检注入临时目录


def _data_dir() -> str:
    if _DATA_DIR_OVERRIDE:
        return _DATA_DIR_OVERRIDE
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "PASMStudio")
    return os.path.expanduser("~/.pasmstudio")


def _config_path() -> str:
    return os.path.join(_data_dir(), "config.json")


def migrate_name_default() -> str:
    """把"恰好等于旧默认名"的角色名迁移为新默认名。返回动作描述（供日志）。"""
    p = _config_path()
    if not os.path.isfile(p):
        return "skip: 无 config.json"
    try:
        with open(p, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if not isinstance(cfg, dict):
            return "skip: 配置格式异常"
        cur = str(cfg.get("name") or "").strip()
        if cur not in LEGACY_NAMES:
            return "skip: name=%r 非旧默认名，尊重用户设置不动" % cur
        cfg["name"] = NEW_DEFAULT_NAME
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=1)
        os.replace(tmp, p)
        return "migrated: %s -> %s" % (cur, NEW_DEFAULT_NAME)
    except Exception as ex:                      # 迁移失败不能影响启动
        return "error: %s" % ex


def run_once() -> list:
    """启动时调用一次。返回 [(步骤, 结果)]，调用方可忽略。"""
    out = []
    try:
        out.append(("name_default", migrate_name_default()))
    except Exception as ex:
        out.append(("name_default", "error: %s" % ex))
    return out


def _selftest() -> int:
    """自检：临时目录 + 三种场景，真实配置不参与。"""
    global _DATA_DIR_OVERRIDE
    import tempfile
    from pathlib import Path

    ok = True
    n = 0
    with tempfile.TemporaryDirectory() as d:
        keep = _DATA_DIR_OVERRIDE
        _DATA_DIR_OVERRIDE = d
        try:
            p = Path(d) / "config.json"

            # ① 没有配置文件
            r = migrate_name_default()
            n += 1
            c1 = r.startswith("skip")
            ok &= c1
            print(("PASS " if c1 else "FAIL ") + "无配置文件时不报错、不改写 -> " + r)

            # ② name 恰好是旧默认名 → 应迁移
            p.write_text(json.dumps({"name": LEGACY_NAMES[0], "persona": "温和沉稳"},
                                    ensure_ascii=False), encoding="utf-8")
            r = migrate_name_default()
            got = json.loads(p.read_text(encoding="utf-8"))
            n += 1
            c2 = (got.get("name") == NEW_DEFAULT_NAME and r.startswith("migrated")
                  and got.get("persona") == "温和沉稳")   # 其它字段必须原样保留
            ok &= c2
            print(("PASS " if c2 else "FAIL ") + "旧默认名被迁移且其它字段保留 -> %s / %s"
                  % (r, got.get("name")))

            # ③ 用户自定义名 → 必须不动
            for custom in ("小雨", NEW_DEFAULT_NAME, "", "  "):
                p.write_text(json.dumps({"name": custom}, ensure_ascii=False),
                             encoding="utf-8")
                r = migrate_name_default()
                got = json.loads(p.read_text(encoding="utf-8"))
                n += 1
                c3 = (got.get("name") == custom) and r.startswith("skip")
                ok &= c3
                print(("PASS " if c3 else "FAIL ") + "name=%r 不被改写 -> %s"
                      % (custom, r))

            # ④ 幂等：迁移过的配置再跑一次不应再动
            p.write_text(json.dumps({"name": NEW_DEFAULT_NAME}, ensure_ascii=False),
                         encoding="utf-8")
            before = p.read_text(encoding="utf-8")
            migrate_name_default()
            n += 1
            c4 = p.read_text(encoding="utf-8") == before
            ok &= c4
            print(("PASS " if c4 else "FAIL ") + "重复运行幂等（文件字节不变）")

            # ⑤ 坏 JSON 不抛异常
            p.write_text("{ 这不是 json", encoding="utf-8")
            r = migrate_name_default()
            n += 1
            c5 = r.startswith("error")
            ok &= c5
            print(("PASS " if c5 else "FAIL ") + "配置损坏时仅返回错误、不抛出 -> " + r)
        finally:
            _DATA_DIR_OVERRIDE = keep

    print("\n%d 项，%s" % (n, "全部通过" if ok else "存在失败"))
    print("（自检全程在临时目录，真实 config.json 未被读取或修改）")
    return 0 if ok else 1


if __name__ == "__main__":
    if sys.argv[1:2] == ["--selftest"]:
        sys.exit(_selftest())
    for k, v in run_once():
        print(k, "->", v)
