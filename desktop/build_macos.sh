#!/usr/bin/env bash
# ============================================================
#  PASM Studio —— macOS 构建脚本（PyInstaller onedir → .app → .dmg）
#  用法：  bash desktop/build_macos.sh            （在仓库根执行，需 macOS）
#          APP_VERSION=0.31.2 bash desktop/build_macos.sh
#  产物：  dist/PASMStudio.app
#          dist/PASMStudio-<ver>-macos-<arch>.dmg
#
#  ⚠️ 与 Windows 的差异（代码里已做平台守卫）：
#     · 音频自愈 / SAPI / 开机自启 是 Windows 专有 → 自动降级
#     · `--add-data` 分隔符是 `:`；图标优先用 .icns（由 assets/icon.png 自动生成）
#
#  ⚠️ 本脚本**尚未在真实 macOS 机器上跑过**（本机是 Windows）。首次请走 CI 或真机，按报错微调。
#  ⚠️ 签名/公证（codesign / notarytool）需要你的 Apple 开发者证书，本脚本**不做签名**；
#     未签名的 .app 在别人机器上会被 Gatekeeper 拦（右键→打开 可绕过测试）。
# ============================================================
set -euo pipefail

[ "$(uname -s)" = "Darwin" ] || { echo "[ERR] 本脚本只能在 macOS 上执行（当前 $(uname -s)）"; exit 1; }

cd "$(dirname "$0")/.."
APP_VERSION="${APP_VERSION:-0.31.2}"
APP_NAME="PASMStudio"
ARCH="$(uname -m)"                          # arm64 / x86_64
# TARGET_ARCH=universal2 → 同时兼容 Apple Silicon 与 Intel（推荐对外发布用）
# 留空 = 只打本机架构（CI 的 macos-latest 现在是 arm64 → Intel Mac 跑不了）
TARGET_ARCH="${TARGET_ARCH:-}"
echo "[INFO] 构建 ${APP_NAME} ${APP_VERSION} for macos-${ARCH}${TARGET_ARCH:+（目标架构 ${TARGET_ARCH}）}"

command -v python3 >/dev/null || { echo "[ERR] 找不到 python3"; exit 1; }

echo "[1/6] 安装构建依赖 ..."
python3 -m pip install -q -r desktop/requirements-linux-macos.txt pyinstaller pillow

echo "[2/6] 由 PNG 生成 .icns（macOS 自带 sips / iconutil）..."
ICNS="build/${APP_NAME}.icns"
if [ -f desktop/assets/icon.icns ]; then
  ICNS="desktop/assets/icon.icns"
else
  ICONSET="build/${APP_NAME}.iconset"
  rm -rf "$ICONSET"; mkdir -p "$ICONSET"
  for SZ in 16 32 64 128 256 512; do
    sips -z $SZ $SZ desktop/assets/icon.png --out "$ICONSET/icon_${SZ}x${SZ}.png" >/dev/null
    D=$((SZ * 2))
    sips -z $D $D desktop/assets/icon.png --out "$ICONSET/icon_${SZ}x${SZ}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$ICNS"
fi
echo "      -> ${ICNS}"

echo "[3/6] PyInstaller 打包 ..."
# 共同的构建参数全部来自 desktop/build_common.py（与 Windows 的 PASMStudio.spec 同一份）。
# ⚠️ 别再手写这份参数：曾经漏了 desktop/skills、115 个懒加载 hiddenimports 与 playwright 驱动，
#    冻结版里那些能力**静默失效**（冒烟照样 PASS）。
mapfile -t BUILD_EXTRA < <(python3 desktop/build_common.py lines)
echo "      build_common 提供 ${#BUILD_EXTRA[@]} 个额外参数"

PYI_ARGS=(--noconfirm --windowed --onedir
  --name "${APP_NAME}"
  --icon "${ICNS}"
  --osx-bundle-identifier "com.arronjack.pasmstudio"
  --collect-all pasm
  desktop/pasm_main.py
  "${BUILD_EXTRA[@]}")

if [ -n "$TARGET_ARCH" ]; then
  # universal2 需要 runner 的 Python 本身是 universal2 构建；不是就会失败 → 回落本机架构
  if python3 -m PyInstaller "${PYI_ARGS[@]}" --target-arch "$TARGET_ARCH"; then
    ARCH="$TARGET_ARCH"
    echo "      -> 成功打出 ${ARCH}（Intel + Apple Silicon 通用）"
  else
    echo "[WARN] --target-arch ${TARGET_ARCH} 失败（该 Python 可能不是 universal2 构建）"
    echo "[WARN] 回落为只打本机架构：$(uname -m)"
    rm -rf "build/${APP_NAME}" "dist/${APP_NAME}" "dist/${APP_NAME}.app" 2>/dev/null || true
    python3 -m PyInstaller "${PYI_ARGS[@]}"
    ARCH="$(uname -m)"
  fi
else
  python3 -m PyInstaller "${PYI_ARGS[@]}"
  ARCH="$(uname -m)"
fi

APP="dist/${APP_NAME}.app"
[ -d "$APP" ] || { echo "[ERR] 未生成 ${APP}"; ls -la dist/ || true; exit 1; }
echo "      -> ${APP}"

echo "[4/6] 写版本文件 + 基本信息 ..."
printf '%s\n' "$APP_VERSION" > "dist/${APP_NAME}/VERSION" 2>/dev/null || true
PLIST="${APP}/Contents/Info.plist"
if [ -f "$PLIST" ]; then
  /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString ${APP_VERSION}" "$PLIST" 2>/dev/null || true
  /usr/libexec/PlistBuddy -c "Set :CFBundleVersion ${APP_VERSION}" "$PLIST" 2>/dev/null || true
  # 中文名（Dock / 访达里显示）
  /usr/libexec/PlistBuddy -c "Set :CFBundleDisplayName PASM Studio" "$PLIST" 2>/dev/null || true
fi

echo "[5/6] 去掉隔离属性（本机自测免 Gatekeeper 拦截）..."
xattr -cr "$APP" 2>/dev/null || true

echo "[6/6] 打 DMG（hdiutil 是 macOS 自带）..."
DMG="dist/${APP_NAME}-${APP_VERSION}-macos-${ARCH}.dmg"
STAGE="build/dmg_stage"
rm -rf "$STAGE"; mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "PASM Studio" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
echo "      -> ${DMG}  ($(du -h "$DMG" | cut -f1))"

echo
echo "[DONE] 产物在 dist/"
ls -la dist/ | sed 's/^/       /'
echo
echo "提示：若要发给别人，需要签名 + 公证（本脚本不做）："
echo "  codesign --deep --force --options runtime --sign \"Developer ID Application: <你的名字>\" \"${APP}\""
echo "  xcrun notarytool submit \"${DMG}\" --apple-id <你的AppleID> --team-id <TEAMID> --wait"
echo "  xcrun stapler staple \"${DMG}\""
