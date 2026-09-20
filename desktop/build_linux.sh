#!/usr/bin/env bash
# ============================================================
#  PASM Studio —— Linux 构建脚本（PyInstaller onedir + tar.gz + 可选 .deb）
#  用法：  bash desktop/build_linux.sh            （在仓库根执行）
#          APP_VERSION=0.31.1 bash desktop/build_linux.sh
#  产物：  dist/PASMStudio/            可直接运行（dist/PASMStudio/PASMStudio）
#          dist/PASMStudio-<ver>-linux-x86_64.tar.gz
#          dist/pasm-studio_<ver>_amd64.deb        （装了 dpkg-deb 才有）
#
#  ⚠️ 与 Windows 的差异（都在代码里做了平台守卫，不会崩）：
#     · 音频自愈 / SAPI 语音 / 开机自启 是 Windows 专有 → 自动降级
#     · 图标用 .png（Windows 用 .ico）；macOS 用 .icns 见 build_macos.sh
#     · `--add-data` 的分隔符是 `:` 而不是 Windows 的 `;`
#
#  ⚠️ 本脚本**尚未在真实 Linux 机器上跑过**（本机是 Windows，PyInstaller 不能交叉编译）。
#     首次运行请在 CI（.github/workflows/build-desktop.yml）或真机上执行，按报错微调。
# ============================================================
set -euo pipefail

cd "$(dirname "$0")/.."                     # 仓库根
APP_VERSION="${APP_VERSION:-0.31.1}"
APP_NAME="PASMStudio"
ARCH="$(uname -m)"
echo "[INFO] 构建 ${APP_NAME} ${APP_VERSION} for linux-${ARCH}"

command -v python3 >/dev/null || { echo "[ERR] 找不到 python3"; exit 1; }

echo "[1/5] 安装构建依赖 ..."
python3 -m pip install -q -r desktop/requirements-linux-macos.txt pyinstaller pillow

echo "[2/5] PyInstaller 打包（onedir）..."
python3 -m PyInstaller --noconfirm --clean --onedir --windowed \
  --name "${APP_NAME}" \
  --icon desktop/assets/icon.png \
  --collect-all pasm \
  --add-data "desktop/assets:assets" \
  --exclude-module matplotlib \
  --exclude-module pytest \
  --exclude-module uvicorn \
  --exclude-module fastapi \
  desktop/pasm_main.py

OUT="dist/${APP_NAME}"
[ -x "${OUT}/${APP_NAME}" ] || { echo "[ERR] 未生成可执行文件：${OUT}/${APP_NAME}"; ls -la "$OUT" || true; exit 1; }
echo "      -> ${OUT}/${APP_NAME}"

echo "[3/5] 写版本文件 ..."
printf '%s\n' "$APP_VERSION" > "${OUT}/VERSION"

echo "[4/5] 打 tar.gz ..."
TARBALL="dist/${APP_NAME}-${APP_VERSION}-linux-${ARCH}.tar.gz"
tar -C dist -czf "${TARBALL}" "${APP_NAME}"
echo "      -> ${TARBALL}  ($(du -h "${TARBALL}" | cut -f1))"

echo "[5/5] 尝试生成 .deb（没装 dpkg-deb 就跳过，不算失败）..."
if command -v dpkg-deb >/dev/null; then
  DEBROOT="build/deb/${APP_NAME}"
  rm -rf "$DEBROOT"
  mkdir -p "${DEBROOT}/DEBIAN" "${DEBROOT}/opt/${APP_NAME}" "${DEBROOT}/usr/bin"
  cp -r "${OUT}/." "${DEBROOT}/opt/${APP_NAME}/"
  cat > "${DEBROOT}/DEBIAN/control" <<EOF
Package: pasm-studio
Version: ${APP_VERSION}
Section: utils
Priority: optional
Architecture: amd64
Maintainer: arronzheng
Description: PASM Studio desktop (AI companion with local cognitive engine)
EOF
  ln -sf "/opt/${APP_NAME}/${APP_NAME}" "${DEBROOT}/usr/bin/pasm-studio"
  dpkg-deb --build --root-owner-group "$DEBROOT" "dist/pasm-studio_${APP_VERSION}_amd64.deb" >/dev/null
  echo "      -> dist/pasm-studio_${APP_VERSION}_amd64.deb"
else
  echo "      (跳过：本机无 dpkg-deb)"
fi

echo
echo "[DONE] 产物在 dist/"
ls -la dist/ | sed 's/^/       /'
echo
echo "提示：Linux 首次运行若报缺库，装这些（Debian/Ubuntu）："
echo "  sudo apt-get install -y libgl1 libegl1 libxkbcommon-x11-0 libxcb-cursor0 \\"
echo "                          libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 \\"
echo "                          libxcb-randr0 libxcb-render-util0 libxcb-xinerama0 \\"
echo "                          libdbus-1-3 libnss3 libasound2t64"
