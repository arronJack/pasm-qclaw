# PASM Studio —— macOS 安装指南

> 面向拿到 `PASMStudio-<版本>-macos-<架构>.dmg` 的人。
> 如果你拿到的是 `.zip` 并解压失败，先看 §1。

---

## 1. 先确认一件事：zip 到底是不是坏的

**下载 GitHub Actions 产物时，网络中断会让 zip 只下一半** —— 表现就是
「压缩文件格式未知或者数据已经损坏」。这不是产物的问题，是文件没下完。

**怎么判断**（三条任一条命中，就是没下完）：

| 判据 | 正常 | 没下完 |
| --- | --- | --- |
| 文件大小 | 与 GitHub 上标注的一致 | 明显偏小 |
| `unzip -t xxx.zip` | `No errors detected` | `unexpected end of file` |
| Windows 双击 | 能打开并看到 `.dmg` | 报「格式未知或已损坏」 |

**Windows 上一条命令验证**（把路径换成你的）：

```powershell
# PowerShell：看大小
(Get-Item "E:\path\PASMStudio-macOS-0.31.1.zip").Length
```

**重新下载的正确姿势**（断点续传，别用浏览器「另存为」）：

```bash
curl -L -C - -o PASMStudio-macOS-0.31.1.zip \
  "https://github.com/arronJack/PASM/actions/artifacts/<artifact-id>/zip"
```

> ⚠️ `-C -` 是断点续传。**如果上一次下的是半截文件，先删掉再下** ——
> 拿一个损坏的半截文件去续传，续出来的还是坏的。

**下载后必做**（在能解压的机器上）：

```bash
unzip -t PASMStudio-macOS-0.31.1.zip     # 必须 "No errors detected"
```

---

## 2. zip 里面是什么

```
PASMStudio-<版本>-macos-<架构>.dmg      ← 安装盘（要点开的就它）
PASMStudio-<版本>-macos-app.tar.gz     ← 同样的 .app，给命令行/脚本用的
```

**`.dmg` 必须在 macOS 上打开** —— Windows 上的解压软件能把它解出来，
但里面的 `.app` 在 Windows 上就是一堆文件，**不能运行**。

---

## 3. 安装（Apple Silicon / Intel 都要先看架构）

### 3.1 确认你的 Mac 是什么芯片

左上角  → 「关于本机」：

- **芯片 Apple M1/M2/M3/M4** → 需要 **arm64**（或 universal2）
- **处理器 Intel Core i5/i7…** → 需要 **x86_64**（或 universal2）

### 3.2 对照 dmg 文件名里的架构

| dmg 里的字样 | Apple Silicon | Intel |
| --- | --- | --- |
| `universal2` | ✅ 能跑 | ✅ 能跑 |
| `arm64` | ✅ 能跑 | ❌ **跑不了**（会提示「无法打开，因为它不受支持」） |
| `x86_64` | ✅ 能跑（走 Rosetta 2） | ✅ 能跑 |

> ⚠️ **已知限制**：CI 的 `macos-latest` 目前是 Apple Silicon，所以早期构建出的是
> **arm64-only** 的包 —— **Intel Mac 上装不了**。
> 构建脚本已加 `TARGET_ARCH=universal2`（一次打出两种芯片都能用的包）；
> 若该构建机的 Python 不是 universal2 版本，脚本会**如实回落为本机架构并打印告警**，
> 不会假装成功。请以 dmg 文件名里的架构字样为准。

### 3.3 安装步骤

1. 把 `.dmg` 拷到 Mac 上，**双击挂载**（会弹出一个磁盘窗口）
2. 窗口里把 **PASM Studio** 拖到 **Applications**（应用程序）文件夹
3. 在「启动台」或「应用程序」里打开它
4. 第一次打开会被 Gatekeeper 拦（见 §4）

**从 tar.gz 装**（等价做法）：

```bash
tar -xzf PASMStudio-<版本>-macos-app.tar.gz
mv PASMStudio.app /Applications/
open /Applications/PASMStudio.app
```

---

## 4. 第一次打不开？这是正常的（未签名应用）

当前 macOS 包**没有代码签名，也没有公证（notarization）** ——
需要 Apple 开发者账号（每年 99 美元），目前还没配。
未签名应用在别人机器上会被 Gatekeeper 拦下，**这是 macOS 的保护机制，不是程序坏了**。

### 方式 A：右键打开（最简单，推荐）

1. 在「应用程序」里 **右键点（或按住 Control 点）** PASM Studio
2. 选「**打开**」
3. 弹窗里再点「**打开**」

> 关键：**必须用右键→打开**。直接双击只会看到「无法打开」而没有「打开」按钮。
> 这一步只需要做**一次**，之后双击就能正常启动。

### 方式 B：系统设置里放行（较新系统上 A 无效时用）

1. 双击应用，看到被拦的提示后**先关掉它**
2. 打开「系统设置」→「隐私与安全性」
3. 往下滚到「安全性」区域，会看到
   「已阻止使用 PASM Studio，因为来自身份不明的开发者」
4. 点「**仍要打开**」→ 输入密码确认

### 方式 C：命令行去掉隔离属性（提示「已损坏，无法打开」时用）

有时 macOS 会直接说「**PASM Studio 已损坏，无法打开。你应该将它移到废纸篓**」——
**这不是文件真损坏**，而是它带有「来自网络下载」的隔离标记（quarantine）。
去掉标记即可：

```bash
xattr -dr com.apple.quarantine /Applications/PASMStudio.app
```

然后再打开。**这一条也是「zip 解压出来的 app 打不开」的最常见解**。

### 方式 D：确认它到底能不能在这台机器上跑

```bash
# 看它支持哪些架构
lipo -archs /Applications/PASMStudio.app/Contents/MacOS/PASMStudio
#   → 输出 "arm64 x86_64" = 通用包（都能跑）
#   → 输出 "arm64"        = 你的 Intel Mac 跑不了，需要 universal2 版本

# 绕过 Gatekeeper 直接从命令行启动，看真实报错
/Applications/PASMStudio.app/Contents/MacOS/PASMStudio
```

---

## 5. 常见问题

| 症状 | 原因 | 解决 |
| --- | --- | --- |
| `unzip` 报「格式未知或数据损坏」 | **下载没完成** | 删掉重下，用 `curl -L -C -`；下完 `unzip -t` 校验 |
| 双击 dmg 提示「已损坏」 | 未签名 + 隔离标记 | `xattr -dr com.apple.quarantine <dmg>` 后再双击 |
| 装好后打开提示「已损坏」 | 同上（拖进 Applications 后仍带标记） | `xattr -dr com.apple.quarantine /Applications/PASMStudio.app` |
| 「无法打开，因为 Apple 无法检查其是否包含恶意软件」 | 未公证 | 右键→打开，或系统设置→隐私与安全性→仍要打开 |
| 「无法打开，因为它不受支持」/ 图标带禁止符号 | **架构不匹配**（Intel Mac 装了 arm64 包） | 换 `universal2` 或 `x86_64` 的包；或装 Rosetta 2：`softwareupdate --install-rosetta` |
| 打开后闪一下就退 | 缺系统库 / 首次初始化 | 从终端启动看报错：`/Applications/PASMStudio.app/Contents/MacOS/PASMStudio` |

---

## 6. 卸载

```bash
rm -rf /Applications/PASMStudio.app
rm -rf ~/Library/Application\ Support/PASMStudio   # 配置与数据
rm -rf ~/Library/Saved\ Application\ State/com.arronjack.pasmstudio.savedState
```

---

## 7. 给维护者：要做出「谁都能装」的包还差什么

1. **universal2**（已在构建脚本里支持，CI 已开启）—— 一次覆盖 Intel + Apple Silicon
2. **签名 + 公证**（需 Apple 开发者账号）：
   ```bash
   codesign --deep --force --options runtime --sign "Developer ID Application: <你的名字>" /Applications/PASMStudio.app
   xcrun notarytool submit PASMStudio.dmg --apple-id <AppleID> --team-id <TEAMID> --wait
   xcrun stapler staple PASMStudio.dmg
   ```
   公证过的包 **双击即可打开**，不需要 §4 的任何绕行。
3. 把 dmg 作为 Release 附件发布（注意 Gitee 单附件 100MB 上限 → 需要切片）

> 未签名/未公证之前，请**始终在下载页写明 §4 的打开方式** ——
> 否则每个用户都会以为「文件坏了」。
