@echo off
REM ============================================================
REM  PASM Studio - Windows build script (PyInstaller, onedir)
REM  Run from repo root:  desktop\build_windows.bat  [set APP_VERSION=x.y.z]
REM  Output: dist\PASMStudio\PASMStudio.exe  (closed-source binary)
REM  !!! 入口必须是 desktop\pasm_main.py（产品=桌面小人+对话窗）。
REM       desktop\pasm_desktop.py 是 v0.2 时代废弃的游戏脚手架，禁止打包它！
REM  Note: keep message ASCII-only to avoid cmd codepage issues.
REM ============================================================
setlocal
cd /d "%~dp0.."

if "%APP_VERSION%"=="" set "APP_VERSION=0.2.0"
echo [INFO] Building %APP_VERSION%

where python >nul 2>nul || (echo [ERR] python not found in PATH & exit /b 1)

echo [1/3] install build deps ...
python -m pip install -q -r desktop\requirements.txt pyinstaller pillow || goto :err

echo [2/3] pyinstaller ...
python -m PyInstaller --noconfirm --clean --onedir --windowed ^
  --name PASMStudio ^
  --icon desktop\assets\icon.ico ^
  --collect-all pasm ^
  --add-data "desktop\assets;assets" ^
  --exclude-module matplotlib ^
  --exclude-module pytest ^
  --exclude-module uvicorn ^
  --exclude-module fastapi ^
  desktop\pasm_main.py || goto :err

echo [3/3] done.
echo.
echo Output: %cd%\dist\PASMStudio\PASMStudio.exe
echo Version: %APP_VERSION%
echo.
echo Optional: set APP_VERSION=0.2.1 ^<cmd^> to embed a different version.
echo Then run Inno Setup:  iscc desktop\installer.iss
exit /b 0

:err
echo [ERR] build failed - see messages above.
exit /b 1
