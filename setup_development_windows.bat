@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo ERP Workbench - development environment setup

echo ============================================================

if exist ".venv\Scripts\python.exe" (
  echo Existing .venv found. It will be reused.
) else (
  echo Creating local .venv with the default Python 3 installation...
  where py >nul 2>nul
  if not errorlevel 1 (
    py -3 -m venv .venv || goto :fail
  ) else (
    python -m venv .venv || goto :fail
  )
)

set "PY=.venv\Scripts\python.exe"

echo Installing/updating declared development dependencies...
"%PY%" -m pip install --upgrade pip || goto :fail
"%PY%" -m pip install -r requirements.txt pyinstaller || goto :fail

echo.
echo Environment ready.
echo Run ERP Workbench with:
echo   .venv\Scripts\python.exe app.py
exit /b 0

:fail
echo.
echo SETUP FAILED. Review the messages above.
exit /b 1
