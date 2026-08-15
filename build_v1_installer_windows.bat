@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo ERP Workbench - Windows release builder

echo ============================================================

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Local .venv was not found.
  echo Run setup_development_windows.bat first.
  goto :fail
)
set "PY=.venv\Scripts\python.exe"

echo [1/4] Verifying the exact environment that will be frozen...
call verify_v1_windows.bat --no-pause
if errorlevel 1 goto :fail

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
  echo ERROR: PyInstaller is not installed in .venv.
  echo Run setup_development_windows.bat first.
  goto :fail
)

echo [2/4] Building self-contained application folder...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
"%PY%" -m PyInstaller --noconfirm --clean ERPWorkbench.spec
if errorlevel 1 goto :fail

if not exist "dist\ERPWorkbench\ERPWorkbench.exe" goto :fail
echo [3/4] Frozen application created: dist\ERPWorkbench\ERPWorkbench.exe

set "ISCC="
for %%V in (7 6) do (
  if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe"
  if not defined ISCC if exist "%ProgramFiles%\Inno Setup %%V\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup %%V\ISCC.exe"
)

if not defined ISCC (
  echo.
  echo [4/4] Inno Setup was not found.
  echo The self-contained application folder is ready under dist\ERPWorkbench.
  echo End users do not need Python, MNE, Qt, or pip.
  echo Install Inno Setup 6 or 7 on the BUILD PC, then run this script again
  echo to create the single Setup.exe installer.
  goto :done
)

echo [4/4] Compiling Windows installer...
if not exist release mkdir release
"%ISCC%" "installer\ERPWorkbench_v1.0.iss"
if errorlevel 1 goto :fail

echo.
echo Installer created under release\ERP_Workbench_1.0_Setup.exe
echo NOTE: Authenticode signing is separate and requires your own trusted
 echo code-signing certificate. This script never disables Windows security.
goto :done

:fail
echo.
echo BUILD FAILED. Review the messages above.
exit /b 1

:done
echo.
pause
