@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo Neurophysiology Workbench - private release audit collector
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: The project .venv was not found.
  echo Run setup_development_windows.bat first, then run this file again.
  echo.
  pause
  exit /b 1
)

if not exist "tools\export_release_environment.py" (
  echo ERROR: tools\export_release_environment.py was not found.
  echo.
  pause
  exit /b 1
)

echo Collecting package names, exact versions, licence metadata, and
echo non-identifying platform details from the local build environment...
echo.

".venv\Scripts\python.exe" "tools\export_release_environment.py"
if errorlevel 1 (
  echo.
  echo AUDIT COLLECTION FAILED. Review the error above.
  pause
  exit /b 1
)

echo.
echo AUDIT COLLECTION COMPLETED.
echo Upload the newly created ZIP from the release_audit folder for review.
echo The ZIP does not contain the virtual environment, EEG data, credentials,
echo Windows username, computer name, or participant files.
echo.
pause
exit /b 0
