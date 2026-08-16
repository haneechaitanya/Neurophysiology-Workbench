@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo Neurophysiology Workbench - isolated security tools setup
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: The audited project .venv was not found.
  echo Run setup_development_windows.bat first.
  pause
  exit /b 1
)

if not exist ".security-venv\Scripts\python.exe" (
  echo Creating a separate environment for audit tools...
  py -3.13 -m venv .security-venv
  if errorlevel 1 (
    echo ERROR: Could not create .security-venv with Python 3.13.
    pause
    exit /b 1
  )
)

echo Installing or updating audit tools only in .security-venv...
".security-venv\Scripts\python.exe" -m pip install --upgrade pip pip-audit bandit detect-secrets
if errorlevel 1 (
  echo.
  echo SECURITY TOOL SETUP FAILED. Check the network/error above.
  pause
  exit /b 1
)

echo.
echo SECURITY TOOLS READY.
echo The audited release .venv was not modified.
pause
exit /b 0
