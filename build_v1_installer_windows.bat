@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo ============================================================
echo ERP Workbench 1.0 - Windows release builder
echo ============================================================

echo [1/4] Selecting Python environment...
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

%PY% -m pip install --upgrade pip
if errorlevel 1 goto :fail
%PY% -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :fail

echo [2/4] Building self-contained application folder...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
%PY% -m PyInstaller --noconfirm --clean ERPWorkbench.spec
if errorlevel 1 goto :fail

echo [3/4] Smoke-launch target created: dist\ERPWorkbench\ERPWorkbench.exe
if not exist "dist\ERPWorkbench\ERPWorkbench.exe" goto :fail

set "ISCC="
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"

if not defined ISCC (
  echo.
  echo [4/4] Inno Setup 6 was not found, so the distributable application folder is ready,
  echo       but ERP_Workbench_1.0_Setup.exe cannot be compiled on this machine yet.
  echo       Install Inno Setup 6 on the BUILD PC, then run this file again.
  echo.
  echo IMPORTANT: The app folder itself is self-contained; END USERS do not need Python/MNE.
  goto :done
)

echo [4/4] Compiling Windows installer...
if not exist release mkdir release
"%ISCC%" "installer\ERPWorkbench_v1.0.iss"
if errorlevel 1 goto :fail

echo.
echo Installer created under release\ERP_Workbench_1.0_Setup.exe

echo NOTE: This build is not Authenticode-signed unless you sign the EXE with your own
 echo trusted code-signing certificate after compilation. Signing/reputation cannot be
 echo fabricated by this script.

goto :done

:fail
echo.
echo BUILD FAILED. Review the messages above.
exit /b 1

:done
echo.
pause
