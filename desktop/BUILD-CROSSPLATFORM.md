# 跨平台构建说明（Windows / Linux / macOS）

> 2026-09-20 起，桌面端**代码层已可跨平台**；Linux / macOS 的**安装包尚未真正产出过**
> （本机是 Windows，PyInstaller 不能交叉编译）。本文说清楚：现在能到哪里、还差什么。

## 1. 现状一览

| 平台 | 代码就绪 | 构建脚本 | 产物是否已发布 |
| --- | --- | --- | --- |
| **Windows** | ✅ | `desktop/build_windows.bat` + `desktop/installer.iss`（Inno Setup） | ✅ 已发到 v0.31.0 |
| **Linux** | ✅ 已修好导入期阻塞 | `desktop/build_linux.sh`（tar.gz + 可选 .deb） | ❌ **从未构建** |
| **macOS** | ✅ 同上 | `desktop/build_macos.sh`（.app + .dmg） | ❌ **从未构建** |
| **手机** | — | 不做（PySide6 无移动端；正确形态是"框架当云端大脑 + 原生壳"） | ❌ |

三平台可一键在 CI 里跑：`.github/workflows/build-desktop.yml`（手动触发或推 `desktop-v*` tag）。

## 2. 本轮为跨平台改了什么（都在代码里，Windows 行为不变）

### 2.1 两个"启动即崩"的导入期阻塞

| 文件 | 问题 | 修法 |
| --- | --- | --- |
| `audio.py` | 顶层 `import winreg` + `ctypes.OleDLL/HRESULT/WINFUNCTYPE` —— 非 Windows 上这些**根本不存在**，而 `pasm_companion.py` 顶层 import 它 → **应用启动即崩** | Windows 专有成员一律 `getattr` 取；`winreg` 包 try/except；四个公开函数在非 Windows 上返回"无需处理"的默认值；新增 `is_supported()` |
| `sysops.py` | 顶层 `import ctypes.wintypes as wt` 并用于模块级结构体 | 显式用固定宽度类型等价表达（`c_uint32/c_uint16/c_ubyte`），缺失时回落到本地 shim |

> 验证方式（可证伪，脚本见 `tools/desktop_verify/` 同级思路）：
> 在进程内**屏蔽 `winreg` + 摘掉 Windows 专有 ctypes 成员 + 把 `sys.platform` 改成 `linux`**，
> 再导入 → 原本必崩，现在 8/8 通过；还原平台后 `is_supported()` 变回 True（证明判据真的在测平台分支）。

### 2.2 新增跨平台操作层 `desktop/platform_ops.py`

把散落各处的 Windows 专有调用收进一层，其余代码只写一次：

| 能力 | Windows | macOS | Linux |
| --- | --- | --- | --- |
| `startfile(path)`（默认程序打开，**签名/异常语义与 `os.startfile` 一致**） | `os.startfile` | `open` | `xdg-open` |
| `open_path(path)`（返回 `(ok, msg)`，不抛） | 同上 | 同上 | 同上 |
| `reveal_in_file_manager(path)`（在文件管理器中定位） | `explorer /select,` | `open -R` | `xdg-open <目录>` |
| `script_argv(path)`（按扩展名决定解释器） | `.py`→本解释器 · `.bat`→`cmd /c` | `.py` · `.sh`→`bash` | 同 macOS；**`.bat` 明确返回 `None`**（不假装能跑） |

- 全量替换了 **15 处** 裸 `os.startfile(`（agent_tools 4 + pasm_companion 11）；
- `knowledge.py` 原本只在 Windows 给 opener，非 Windows 直接"不支持自动打开" → 现在也走 `platform_ops`，三平台都能打开；
- `agent_tools` 里 `.bat` 分支在非 Windows 上会**如实说明**"这是 Windows 批处理，本系统无法运行"，而不是抛异常。

### 2.3 依赖拆分

- `desktop/requirements.txt` —— **Windows 权威清单**（含 `comtypes`，Windows 专用），**未改动**；
- `desktop/requirements-linux-macos.txt` —— 去掉 `comtypes` 的跨平台子集，供 Linux/macOS 构建用。

### 2.4 打包

- `PASMStudio.spec` 的 `hiddenimports` 补了 `platform_ops`
  （它在多处是函数内懒加载，PyInstaller 静态分析可能看不见 → 不声明会在 frozen 版里"打开文件没反应"）。

## 3. 还差什么才能真的发出安装包

1. **在真机或 CI 上跑一次**（本仓是私有仓，Actions 会消耗额度，所以流水线默认只手动/`desktop-v*` 触发）。
   首次大概率要按报错微调 `--add-data` 分隔符、缺的 Qt 系统库、`.deb` 的 control 字段等。
2. **Linux**：CI 里已 `apt-get` 装好 Qt/QtWebEngine 运行库；本机运行若报缺库，见 `build_linux.sh` 末尾的清单。
3. **macOS**：脚本能出 `.app` 与 `.dmg`，但**不做签名/公证** —— 未签名的包在别人机器上会被 Gatekeeper 拦
   （右键→打开 可绕过自测）。要正式分发需要你的 Apple 开发者证书（脚本末尾给了 `codesign`/`notarytool` 命令）。
4. **自动更新通道**：现有 `latest.json` 只描述 Windows 安装包；三平台要按平台分文件（字段建议 `platform`）。
5. **apt/brew 仓库**（可选）：暂不做，先以 tar.gz / deb / dmg 直链分发。

## 4. 已知的平台能力差异（已优雅降级，不是 bug）

| 能力 | Windows | Linux / macOS |
| --- | --- | --- |
| 音频自愈（取消静音/查设备） | ✅ | ❌ 返回"由系统自行管理音频，无需处理" |
| SAPI 语音（`comtypes`） | ✅ | ❌ 回落 edge-tts（联网）或无声 |
| 开机自启（HKCU Run 键） | ✅ | ❌ `autostart.is_supported()` 报不支持 |
| 桌面路径定位（SHGetKnownFolderPath） | ✅ | ❌ 回落 `~/Desktop` |
| 打开文件 / 定位文件 / 运行脚本 | ✅ | ✅ 走 `platform_ops` |
