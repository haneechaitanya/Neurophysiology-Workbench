@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "ERP Workbench" "" ".venv\Scripts\pythonw.exe" "app.py"
  exit /b 0
)
where pythonw >nul 2>nul
if %errorlevel%==0 (
  start "ERP Workbench" "" pythonw "app.py"
  exit /b 0
)
echo Could not find pythonw.exe. Use the packaged ERP Workbench installer for a terminal-free end-user launch.
pause
