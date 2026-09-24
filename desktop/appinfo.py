"""PASM Studio 应用元信息（版本号 / 升级源 / 官网）。"""
from __future__ import annotations

APP_NAME = "PASM Studio"
APP_SHORT = "PASM Studio"
APP_VERSION = "0.31.6"          # 同步修改此值即触发安装包与升级通道
APP_PUBLISHER = "arronZheng"
APP_COPYRIGHT = "Copyright (c) 2026 arronZheng"
APP_HOMEPAGE = "https://gitee.com/arronzheng/pasm-qclaw"   # 产品说明/下载页（公开仓库）
# 升级元信息源（latest.json 部署在公开仓库 pasm-qclaw，外网用户可访问）
APP_LATEST_URL = "https://gitee.com/arronzheng/pasm-qclaw/raw/master/latest.json"


def version_tuple(v: str) -> tuple:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def is_newer(remote: str) -> bool:
    return version_tuple(remote) > version_tuple(APP_VERSION)
