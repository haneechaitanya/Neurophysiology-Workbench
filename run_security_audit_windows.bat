@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo Neurophysiology Workbench - private security audit
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: The audited project .venv was not found.
  pause
  exit /b 1
)

if not exist ".security-venv\Scripts\python.exe" (
  echo ERROR: The isolated security-tool environment was not found.
  echo Run setup_security_tools_windows.bat first.
  pause
  exit /b 1
)

if not exist "dist\ERPWorkbench\ERPWorkbench.exe" (
  echo ERROR: The accepted dist\ERPWorkbench build was not found.
  pause
  exit /b 1
)

".venv\Scripts\python.exe" "tools\run_security_audit.py"
if errorlevel 1 (
  echo.
  echo SECURITY AUDIT COLLECTION FAILED. Review the error above.
  pause
  exit /b 1
)

echo.
echo SECURITY AUDIT COLLECTION COMPLETED.
echo Upload the new ZIP from security_audit for review.
echo Findings are not automatically treated as vulnerabilities; they require review.
pause
exit /b 0
