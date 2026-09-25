@echo off
setlocal
cd /d "%~dp0"

REM One-click local build. ASCII only: Chinese Windows cmd is GBK.
REM Output: dist\polish-chat\polish-chat.exe

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtualenv .venv ...
    python -m venv .venv || goto :fail
)
call ".venv\Scripts\activate.bat" || goto :fail

REM A uv-made .venv ships without pip, so `python -m pip` would die here; prefer uv when it is around.
REM UV_CACHE_DIR goes next to the repo because the per-user cache can be unwritable.
set "PIPINST="
where uv >nul 2>nul && set "PIPINST=uv pip install --python .venv\Scripts\python.exe"
if not defined PIPINST (
    python -c "import pip" >nul 2>nul || python -m ensurepip >nul 2>nul
    set "PIPINST=python -m pip install"
)
if not defined UV_CACHE_DIR set "UV_CACHE_DIR=%cd%\.uv-cache"

echo Installing dependencies ...
%PIPINST% -r requirements.txt pyinstaller || goto :fail

echo Building ...
pyinstaller --noconfirm --clean polish-chat.spec || goto :fail

echo.
echo Build OK.
echo   %cd%\dist\polish-chat\polish-chat.exe
echo Ship the whole dist\polish-chat folder: the exe needs the files next to it.
pause
exit /b 0

:fail
echo.
echo Build FAILED. Scroll up for the error.
pause
exit /b 1
