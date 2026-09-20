# PASM Studio —— Linux 安装指南

> 面向拿到 `PASMStudio-<版本>-linux-<架构>.tar.gz` 或 `.deb` 的人。
> 若你拿到的是 `.zip` 且解压失败，先看 §1。

---

## 1. 先确认 zip / tar.gz 是不是坏的

**下载中断会让压缩包只下一半**，表现是「格式未知或数据已损坏」。
这不是产物的问题，是文件没下完。

```bash
# 看大小（与发布页标注的对比）
ls -l PASMStudio-Linux-0.31.1.zip

# 校验 zip（必须 "No errors detected"）
unzip -t PASMStudio-Linux-0.31.1.zip

# 校验 tar.gz（无输出即正常）
tar -tzf PASMStudio-0.31.1-linux-x86_64.tar.gz > /dev/null && echo OK
```

**重新下载用断点续传**（别用浏览器「另存为」）：

```bash
curl -L -C - -o PASMStudio-Linux-0.31.1.zip "<下载地址>"
```

> ⚠️ 上一次下的是半截文件时，**先删掉再下** —— 拿损坏文件续传，续出来还是坏的。

---

## 2. 选哪种安装方式

| 方式 | 适合 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **`.deb`**（Debian/Ubuntu 系） | Ubuntu / Debian / Deepin / UOS | 双击或用一条命令装；自动建桌面快捷方式 | 仅 Debian 系 |
| **`.tar.gz`** | 所有发行版（含 Fedora/openSUSE/Arch） | 免安装、免 root；换机只需拷目录 | 需自己建快捷方式 |

**先确认架构**：

```bash
uname -m        # x86_64 = 常规 PC；aarch64 = ARM（本包通常不带 ARM，需另行构建）
```

---

## 3. 安装

### 3.1 Debian / Ubuntu（.deb）

```bash
sudo apt install ./PASMStudio-<版本>-linux-amd64.deb
```

> 用 `apt install ./xxx.deb`（**带 `./`**）而不是 `dpkg -i`：
> 前者会自动补上缺失的依赖，后者会留下「依赖未满足」的烂摊子。

装完在应用菜单里找到 **PASM Studio**，或直接：

```bash
pasmstudio
```

卸载：

```bash
sudo apt remove pasmstudio
rm -rf ~/.config/PASMStudio ~/.local/share/PASMStudio
```

### 3.2 通用（.tar.gz）

```bash
# 1) 解压到你想放的位置
mkdir -p ~/apps && tar -xzf PASMStudio-<版本>-linux-x86_64.tar.gz -C ~/apps

# 2) 直接跑（先确认能起来）
~/apps/PASMStudio/PASMStudio

# 3) 建到应用菜单（可选）
mkdir -p ~/.local/share/applications
cat > ~/.local/share/applications/pasmstudio.desktop <<EOF
[Desktop Entry]
Type=Application
Name=PASM Studio
Comment=PASM 认知智能体桌面工作台
Exec=$HOME/apps/PASMStudio/PASMStudio
Path=$HOME/apps/PASMStudio
Icon=$HOME/apps/PASMStudio/assets/icon.png
Terminal=false
Categories=Development;Utility;
EOF
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

卸载：删掉 `~/apps/PASMStudio` 与那个 `.desktop` 文件，再删配置目录即可。

---

## 4. 装了却起不来？按这个顺序排查

### 4.1 缺系统库（最常见）

PySide6 依赖一堆 Qt 运行库，精简安装的发行版上会缺。**直接跑一次，看报什么**：

```bash
~/apps/PASMStudio/PASMStudio           # 或 pasmstudio
```

若报 `error while loading shared libraries: libxxx.so.x`，按下表装：

```bash
# Debian / Ubuntu
sudo apt install -y libgl1 libegl1 libxkbcommon-x11-0 libdbus-1-3 \
  libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 \
  libxcb-shape0 libxcb-xinerama0 libxcb-xinput0 libfontconfig1 libnss3 \
  libasound2t64 libxdamage1 libxcomposite1 libxrandr2 libxtst6

# Fedora
sudo dnf install -y mesa-libGL mesa-libEGL libxkbcommon-x11 xcb-util-cursor \
  xcb-util-keysyms xcb-util-renderutil xcb-util-wm fontconfig nss alsa-lib \
  libXdamage libXcomposite libXrandr libXtst

# Arch
sudo pacman -S --needed mesa libxkbcommon-x11 xcb-util-cursor xcb-util-keysyms \
  xcb-util-renderutil xcb-util-wm fontconfig nss alsa-lib libxdamage libxcomposite \
  libxrandr libxtst
```

> 构建脚本在产物末尾附了这份 apt 清单，忘记时可以直接翻 `build_linux.sh` 的注释。

### 4.2 是 Wayland 会话时白屏 / 闪退

Qt 在部分 Wayland 合成器上仍不稳，**走 X11 后端**通常立刻可用：

```bash
QT_QPA_PLATFORM=xcb ~/apps/PASMStudio/PASMStudio
```

要长期生效，把它写进那个 `.desktop` 的 `Exec=` 前：
`Exec=env QT_QPA_PLATFORM=xcb /home/你/apps/PASMStudio/PASMStudio`

### 4.3 无头 / 远程（SSH）环境下启动

没有显示服务时用离屏后端验证「程序本身能不能起来」：

```bash
QT_QPA_PLATFORM=offscreen timeout 20 ~/apps/PASMStudio/PASMStudio
# 退出码非 0 属正常；关键看有没有 "error while loading shared libraries"
```

要真的看到界面，用 `ssh -X` / `ssh -Y` 开 X 转发。

### 4.4 内嵌浏览器（产出预览）不工作

产出页的「真浏览器」用 QtWebEngine，**部分系统上需要额外内核参数**：

```bash
# 常见两种报错对应的处理
sudo sysctl -w kernel.unprivileged_userns_clone=1     # 沙箱报错
# 或在启动前关闭 Chromium 沙箱
QTWEBENGINE_CHROMIUM_FLAGS="--no-sandbox" ~/apps/PASMStudio/PASMStudio
```

> ⚠️ `--no-sandbox` 会降低浏览器的安全隔离，**只在确实需要时用**，
> 且不要用它去打开不受信任的网页。

### 4.5 中文显示成方块

```bash
sudo apt install -y fonts-noto-cjk          # Debian/Ubuntu
sudo dnf install -y google-noto-sans-cjk-fonts   # Fedora
```

---

## 5. 常见问题速查

| 症状 | 原因 | 解决 |
| --- | --- | --- |
| `unzip` 报「格式未知或数据损坏」 | **下载没完成** | 删掉重下（`curl -L -C -`），下完 `unzip -t` 校验 |
| `error while loading shared libraries` | 缺 Qt/GL 系统库 | §4.1 的安装命令 |
| 白屏 / 一闪就退（Wayland） | Qt Wayland 后端问题 | `QT_QPA_PLATFORM=xcb` |
| 双击 .deb 装不上 | 依赖没自动补 | 命令行 `sudo apt install ./xxx.deb` |
| 启动无任何提示就退 | 异常进日志 | 看 `~/.local/share/PASMStudio/pasm.log`（或 `%` 配置目录下的 log） |
| 产出预览空白，提示原生渲染 | QtWebEngine 不可用 | §4.4 |
| 界面正常但没声音 | 音频自愈是 Windows 专有 | Linux 上播报依赖系统音频，无需额外处理 |

---

## 6. 已知边界（诚实说明）

- **未做签名/公证这类机制** —— Linux 侧不影响安装，但 .deb 未进官方仓库，
  `apt upgrade` 不会自动更新它；升级请重新下载新版。
- **音频自愈 / 开机自启 / SAPI 念读** 是 Windows 专有功能，在 Linux 上**自动降级**，
  界面上不会假装可用。
- **ARM（aarch64）**：当前 CI 只构建 x86_64；ARM 设备需自行在真机跑
  `bash desktop/build_linux.sh` 产出。
- **升级通道**：现有自动更新是为 Windows（Inno）设计的；Linux/macOS 目前**手动下载新版**。
