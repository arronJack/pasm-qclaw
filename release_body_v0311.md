# v0.31.1 —— 桌面端首次三平台同发（Windows / macOS / Linux）

这一版的核心不是新功能，而是**同一份代码第一次在三个系统上都产出可安装的包**。

---

## 三平台产物

| 平台 | 文件 | 说明 |
| --- | --- | --- |
| **Windows** | `PASMStudio-Setup-<ver>-gitee.exe`（+ `.bin` 切片） | 与以往一致：Inno Setup 安装包，支持增量升级通道 |
| **macOS** | `PASMStudio-<ver>-macos-<arch>.dmg` | 拖进「应用程序」即装。**未签名/未公证** → 首次打开需右键「打开」 |
| **Linux** | `PASMStudio-<ver>-linux-<arch>.tar.gz` 与 `pasm-studio_<ver>_amd64.deb` | `.deb` 双击或 `apt install ./xxx.deb`；`.tar.gz` 免安装 |

> **首次在 macOS / Linux 上运行，请先读安装指南**（就在本仓 `desktop/` 下）：
> - [`desktop/INSTALL-macos.md`](desktop/INSTALL-macos.md)
> - [`desktop/INSTALL-linux.md`](desktop/INSTALL-linux.md)
>
> 两篇都包含**下载损坏的判别方法**（大文件断线会让 zip「格式未知或数据损坏」——
> 那是没下完，不是包坏了）。

---

## 这一版为了让 Linux / macOS 能跑，改了什么

### 1. 修掉两个「导入期硬拦」（此前非 Windows 上启动即崩）

- `audio.py` 顶层 `import winreg` + `ctypes.OleDLL/HRESULT/WINFUNCTYPE` —— 这些**只在 Windows 存在**，
  而主程序顶层就 import 它 → Linux/macOS 上直接崩在启动前。
  现已改为：专有成员一律 `getattr` 取、`winreg` 包 `try/except`、
  四个公开函数在非 Windows 返回"无需处理"的默认值，并新增 `is_supported()` 供调用方判断。
- `sysops.py` 顶层 `import ctypes.wintypes` 并用于模块级结构体 —— 改用固定宽度等价类型表达。

### 2. 新增 `desktop/platform_ops.py`（跨平台系统操作层）

`os.startfile` / `explorer /select,` 这类 Windows 写法原本散落各处（共 **15 处**）。
现在统一收敛：

- `startfile(path)` —— **签名与异常语义与 `os.startfile` 完全一致**（成功返回 None、失败抛 `OSError`），
  所以既有调用点的 `try/except` 一处都不用改。Windows=`os.startfile` · macOS=`open` · Linux=`xdg-open`。
- `open_path()` 返回 `(ok, msg)` 不抛异常（给人看的文案）。
- `reveal_in_file_manager()` —— `explorer /select,` · `open -R` · `xdg-open <目录>`。
- `script_argv()` —— `.py` 用当前解释器；`.bat` 在非 Windows 上**明确返回 None**
  （不假装能跑，而是如实说"Windows 批处理无法在本系统运行"）。

### 3. 三平台构建脚本与 CI

- `desktop/build_linux.sh` —— PyInstaller onedir → tar.gz → 可选 `.deb`（缺 `dpkg-deb` 不算失败）。
- `desktop/build_macos.sh` —— → `.app` → `.dmg`；图标由 png 自动生成 `.icns`；
  支持 `TARGET_ARCH=universal2` 打出 **Intel + Apple Silicon 通用**包
  （若构建机的 Python 不是 universal2 版本，PyInstaller 会失败 → 脚本**如实回落为本机架构并告警**）。
- `desktop/requirements-linux-macos.txt` —— 去掉 Windows 专属的 `comtypes`。
- `.github/workflows/build-desktop.yml` —— 三平台矩阵；**三个独立 job**（可单独重跑某个平台）；
  Windows 侧带启动冒烟，Linux 侧预装 Qt 运行库。

---

## 关于 macOS 包的两个已知限制（请务必知情）

1. **未签名、未公证** —— 需要 Apple 开发者账号（每年 99 美元），目前没配。
   后果：别人机器上首次打开会被 Gatekeeper 拦。
   解法见 [`desktop/INSTALL-macos.md`](desktop/INSTALL-macos.md) §4（右键→打开 / 系统设置放行 /
   `xattr -dr com.apple.quarantine`）。**这不是程序坏了**。
2. **架构** —— CI 的 `macos-latest` 是 Apple Silicon。
   - 包里带 `universal2` → Intel 与 Apple Silicon 都能跑
   - 包里带 `arm64` → **Intel Mac 跑不了**（提示"不受支持"）
   
   本版已开启 `TARGET_ARCH=universal2`；**请以文件名里的架构字样为准**，
   装入前先对照 [`desktop/INSTALL-macos.md`](desktop/INSTALL-macos.md) §3.2 的表格。

---

## 自动更新说明

- **Windows**：沿用 Inno 增量升级通道（`latest.json`）。
- **macOS / Linux**：本版为**手动下载新版**。跨平台升级通道仍在规划中，
  现有 `latest.json` 只描述 Windows 安装包。

---

## 验证

- 桌面守门套件 **940 项通过 / 1 项失败**；该失败在改动前的同一提交上**同样存在**
  （改动前 `PASS=64 FAIL=3` → 改动后 `PASS=65 FAIL=2`，**未引入回归**）。
- `tools/check_undefined_names.py` 全过（26 个文件）· `tools/check_core_fork.py` 0 失败。
- 非 Windows 行为用**可证伪**方式验证：进程内屏蔽 `winreg` + 摘掉 Windows 专有 ctypes 成员
  + 把 `sys.platform` 改成 `linux` 再导入 `audio.py` —— 修前必崩、修后 8/8 通过；
  还原平台后 `is_supported()` 变回 `True`（证明判据确实在测平台分支）。
- `platform_ops` 三平台模拟 **17/17**。
- AST 扫描 `desktop/` 的导入期 Windows 依赖：修前 1 处未防护 → 修后 **0**。

---

## Windows 用户

功能与 v0.31.0 一致（本版主要动的是平台适配与构建链），可以按习惯升级；
若你只关心 Windows 功能变化，可留在 0.31.0，或直接升到本版（无行为差异）。

---

## ⚠️ 从 Gitee 下载的用户：分卷合并（macOS / Linux）

Gitee 单附件上限 100MB，因此 **macOS 的 `.dmg` 和 Linux 的 `.tar.gz` 被切成了多个 `<文件名>.00x` 分卷**
（`PASMStudio-0.31.1-macos-arm64.dmg.001~.004`、`PASMStudio-0.31.1-linux-x86_64.tar.gz.001~.004`）。
**必须先把分卷合并成完整文件才能使用**，单独一个分卷是打不开的。

合并命令（在下载目录里执行，4 个分卷要全部下完）：

**macOS（dmg）：**

```bash
cat PASMStudio-0.31.1-macos-arm64.dmg.001 \
    PASMStudio-0.31.1-macos-arm64.dmg.002 \
    PASMStudio-0.31.1-macos-arm64.dmg.003 \
    PASMStudio-0.31.1-macos-arm64.dmg.004 \
    > PASMStudio-0.31.1-macos-arm64.dmg
```

**Linux（tar.gz）：**

```bash
cat PASMStudio-0.31.1-linux-x86_64.tar.gz.001 \
    PASMStudio-0.31.1-linux-x86_64.tar.gz.002 \
    PASMStudio-0.31.1-linux-x86_64.tar.gz.003 \
    PASMStudio-0.31.1-linux-x86_64.tar.gz.004 \
    > PASMStudio-0.31.1-linux-x86_64.tar.gz
```

> - **Windows 安装器不用合并**：双击 `PASMStudio-Setup-0.31.1-gitee.exe` 即可，Inno Setup 会自动找同目录的 `.bin` 切片。
> - **GitHub 端下载的是完整文件，无需合并**（GitHub 无 100MB 附件限制）。
