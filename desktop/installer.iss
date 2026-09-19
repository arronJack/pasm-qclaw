; PASM Studio - Inno Setup installer
; Build:  ① 打包（从仓库根，**用全新目录**——本机有删除守卫，--clean 会被拦）：
;            pyinstaller --noconfirm --distpath dist_v0.28.5 --workpath build_v0.28.5 PASMStudio.spec
;            产物落在 dist_v0.28.5\PASMStudio\
;         ② 打安装包（把源目录指过去）：
;            iscc /DAppVersion=0.28.5 /DSrcDir=..\dist_v0.28.5\PASMStudio desktop\installer.iss
;         （省略 /DSrcDir 时回落到默认的 ..\dist\PASMStudio）
; Output: installer\PASMStudio-Setup-<version>.exe
; Notes:  user data lives in %APPDATA%\PASMStudio -> uninstall never deletes it.
; v0.16: 最低 Windows 10（PySide6/Qt6 路线，不再兼容 Win7/8）——
;         旧系统会在安装阶段被明确提示，而不是装完双击后报启动错误。

#define AppIdGuid "PASMStudio_arronzheng_001"

; 打包产物目录（可用 /DSrcDir=... 覆盖，便于每次构建用全新 dist 目录）
#ifndef SrcDir
  #define SrcDir "..\dist\PASMStudio"
#endif

; v0.31.0：输出文件名可由 /DOutBase 覆盖（Gitee 分卷版用带 -gitee 后缀的名字，
;   以免覆盖 GitHub 那份单文件整包 —— 同版本两份产物必须能一眼分清）。
#ifndef OutBase
  #define OutBase "PASMStudio-Setup-" + AppVersion
#endif

[Setup]
AppId={#AppIdGuid}
AppName=PASM Studio
AppVersion={#AppVersion}
AppPublisher=arronZheng
AppCopyright=Copyright (c) 2026 arronZheng
VersionInfoVersion={#AppVersion}
VersionInfoCompany=arronZheng
VersionInfoDescription=PASM Studio - Desktop pet AI
; per-user install: no admin needed -> avoids MoveFile code5 on Program Files
DefaultDirName={localappdata}\Programs\PASMStudio
DefaultGroupName=PASM Studio
UninstallDisplayIcon={app}\PASMStudio.exe
SetupIconFile=assets\icon.ico
OutputDir=..\installer
OutputBaseFilename={#OutBase}
Compression=lzma2
SolidCompression=yes
; ── v0.31.0：内嵌 Chromium 后安装包涨到 190MB，**超过 Gitee 单附件 100MB 硬上限** ──
;   Gitee 对"单个附件"有 100MB 硬上限（哪怕 100.0MB 也会被拒），所以给 Gitee 出一份
;   "分卷版"：Inno 原生 DiskSpanning，用户把下载到的所有文件放在**同一个文件夹**里直接
;   运行 Setup 即可，**不需要任何解压工具、也不需要手动合并**。
;   GitHub 侧继续用单文件整包（一键下载，也是自动升级通道的来源）。
;   用法：ISCC /DGiteeSplit=1 /DOutBase=PASMStudio-Setup-0.31.0-gitee ...
;   ⚠️ DiskSliceSize 必须 <100MB（这里 99MB）。曾经试过 102MB 想凑成 3 个文件，但
;      102MB>100MB 会被 Gitee 拒传；199MB ÷ ≤99MB 最少也要 4 个文件（exe 启动器 + 3 个
;      .bin 分卷），这是 Gitee 上限下的底线，**别再调小想凑 3 个文件**。
#ifdef GiteeSplit
DiskSpanning=yes
DiskSliceSize=99000000
#else
; 单文件整包（GitHub 主源 / 本地安装都用它）
#endif
WizardStyle=modern
PrivilegesRequired=lowest
ShowLanguageDialog=no
CloseApplications=force
RestartApplications=no
; user data lives in %APPDATA%\PASMStudio - uninstall does NOT remove it

[Languages]
Name: "chinesesimp"; MessagesFile: "lang\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标:"; Flags: unchecked

[Files]
Source: "{#SrcDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\PASM Studio"; Filename: "{app}\PASMStudio.exe"
Name: "{group}\Uninstall PASM Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\PASM Studio"; Filename: "{app}\PASMStudio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\PASMStudio.exe"; Description: "Launch PASM Studio"; Flags: nowait postinstall skipifsilent

[Code]
// v0.24.3 釜底抽薪：不再"替换旧文件"（替换 = 重命名旧文件 → 被占用即 MoveFile 错误 5），
// 而是安装前先清空程序目录（保留便携 data 目录），再全新复制——不存在被替换对象，
// 错误 5 的触发路径被整体消除。杀进程 → 等 2s → 清空，最多 5 轮；仍失败则给出
// 指引（退出程序 / 关闭杀软实时防护 / 重启后再装）。
procedure KillPASM();
var
  Res: Integer;
begin
  Exec(ExpandConstant('{sys}\taskkill.exe'),
       '/F /IM PASMStudio.exe /T',
       '', SW_HIDE, ewWaitUntilTerminated, Res);
end;

// 清空 AppDir 下除 KeepName（便携 data）外的所有文件与子目录
function WipeExcept(const AppDir, KeepName: String): Boolean;
var
  SR: TFindRec;
  Path: String;
begin
  Result := True;
  if FindFirst(AppDir + '\*', SR) then
  try
    repeat
      if (SR.Name <> '.') and (SR.Name <> '..') and
         (CompareText(SR.Name, KeepName) <> 0) then
      begin
        Path := AppDir + '\' + SR.Name;
        if (SR.Attributes and FILE_ATTRIBUTE_DIRECTORY) <> 0 then
        begin
          if not DelTree(Path, True, True, True) then
            Result := False;
        end
        else
        begin
          if not DeleteFile(Path) then
            Result := False;
        end;
      end;
    until not FindNext(SR);
  finally
    FindClose(SR);
  end;
end;

function InitializeSetup(): Boolean;
begin
  Result := True;
  KillPASM();                               // 尽早杀一轮，给句柄释放留时间
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  AppDir: String;
  Round: Integer;
  Wiped: Boolean;
begin
  Result := '';
  AppDir := ExpandConstant('{app}');
  if not DirExists(AppDir) then
    Exit;                                   // 首次安装：无旧文件，直接装

  Wiped := False;
  for Round := 1 to 5 do
  begin
    KillPASM();
    Sleep(2000);
    if WipeExcept(AppDir, 'data') and not DirExists(AppDir + '\_internal') then
    begin
      Wiped := True;
      Break;
    end;
  end;

  if not Wiped then
  begin
    MsgBox('PASM Studio 的程序文件仍被其他程序占用，无法完成升级。'
           + #13#10 + #13#10 +
           '请任选其一后重新运行本安装程序：'
           + #13#10 + '  1. 右下角托盘图标 → 右键 → 退出 PASM Studio（或任务管理器结束 PASMStudio.exe）；'
           + #13#10 + '  2. 暂时退出杀毒软件/安全防护的实时防护（360、火绒、电脑管家、Windows 安全中心等可能短暂锁定文件）；'
           + #13#10 + '  3. 重启电脑后再运行本安装程序；'
           + #13#10 + '  4. 也可直接点「下一步」换一个安装路径安装（旧目录可事后手动删除）。',
           mbCriticalError, MB_OK);
    Result := '安装已取消：程序文件被占用，请按提示处理后重试。';
  end;
end;

function GetInstalledVersion(): String;
var
  S: String;
begin
  if RegKeyExists(HKCU, 'Software\PASMStudio\version') then
  begin
    RegQueryStringValue(HKCU, 'Software\PASMStudio\version', 'current', S);
    Result := S;
  end
  else
    Result := '';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RegWriteStringValue(HKCU, 'Software\PASMStudio\version', 'current', '{#AppVersion}');
end;
