"""``python -m runtime`` —— 跑运行时自检。"""
from __future__ import annotations

from . import selftest

if __name__ == "__main__":
    raise SystemExit(1 if selftest() else 0)
