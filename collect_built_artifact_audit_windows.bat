@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: .venv\Scripts\python.exe was not found.
  echo Run setup_development_windows.bat first.
  exit /b 1
)

if not exist "dist\ERPWorkbench\ERPWorkbench.exe" (
  echo ERROR: dist\ERPWorkbench\ERPWorkbench.exe was not found.
  echo Run build_windows.bat first.
  exit /b 1
)

".venv\Scripts\python.exe" "tools\audit_built_distribution.py" "dist\ERPWorkbench"
if errorlevel 1 exit /b 1

echo.
echo Built-artifact audit complete. See the new ZIP in release_audit.
echo You may now close this window.
pause
endlocal
