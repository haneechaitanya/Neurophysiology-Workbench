@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "BUILD_LOG=build_release.log"
set "ERP_WORKBENCH_STORE_BUILD="
set "ERP_WORKBENCH_DISTRIBUTION="

echo ============================================================
echo ERP Workbench - Windows release builder
echo ============================================================
echo Build started: %DATE% %TIME% > "%BUILD_LOG%"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: Local .venv was not found.
  echo ERROR: Local .venv was not found. >> "%BUILD_LOG%"
  echo Run setup_development_windows.bat first.
  goto :fail
)
set "PY=.venv\Scripts\python.exe"

for %%F in ("assets\ERPWorkbench.ico" "assets\erp_workbench_icon.png" "version_info.txt" "ERPWorkbench.spec") do (
  if not exist %%F (
    echo ERROR: %%~F was not found.
    echo ERROR: %%~F was not found. >> "%BUILD_LOG%"
    goto :fail
  )
)

echo [0/4] Checking release dependencies...
"%PY%" -c "import PyInstaller, mne, mne_icalabel, onnxruntime, pyqtgraph, sklearn, scipy, matplotlib; print('Release imports OK')" >> "%BUILD_LOG%" 2>&1
if errorlevel 1 (
  echo ERROR: One or more release dependencies are missing.
  powershell -NoProfile -Command "Get-Content '%BUILD_LOG%' -Tail 40"
  goto :fail
)
echo     ONNX Runtime is the ICLabel inference backend for this build.
echo     PyTorch and PyOpenGL are not required.

echo [1/4] Verifying the exact environment that will be frozen...
call verify_v1_windows.bat --no-pause
if errorlevel 1 goto :fail

echo [2/4] Building self-contained application folder...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist

echo.>> "%BUILD_LOG%"
echo ===== PyInstaller =====>> "%BUILD_LOG%"
"%PY%" -m PyInstaller --noconfirm --clean --log-level INFO ERPWorkbench.spec >> "%BUILD_LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo PyInstaller failed. Last 80 log lines:
  echo ------------------------------------------------------------
  powershell -NoProfile -Command "Get-Content '%BUILD_LOG%' -Tail 80"
  goto :fail
)

if not exist "dist\ERPWorkbench\ERPWorkbench.exe" (
  echo ERROR: PyInstaller returned without creating ERPWorkbench.exe.
  echo ERROR: Expected dist\ERPWorkbench\ERPWorkbench.exe >> "%BUILD_LOG%"
  goto :fail
)

echo [3/4] Frozen application created:
echo       dist\ERPWorkbench\ERPWorkbench.exe

set "ISCC="
for %%V in (7 6) do (
  if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup %%V\ISCC.exe"
  if not defined ISCC if exist "%ProgramFiles%\Inno Setup %%V\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup %%V\ISCC.exe"
)

if not defined ISCC (
  echo.
  echo [4/4] Inno Setup was not found.
  echo The frozen application is ready under dist\ERPWorkbench.
  echo Install Inno Setup 6 or 7 on this BUILD PC to make Setup.exe.
  goto :done
)

echo [4/4] Compiling Windows installer...
if not exist release mkdir release
echo.>> "%BUILD_LOG%"
echo ===== Inno Setup =====>> "%BUILD_LOG%"
"%ISCC%" "installer\ERPWorkbench_v1.0.iss" >> "%BUILD_LOG%" 2>&1
if errorlevel 1 (
  echo.
  echo Inno Setup failed. Last 60 log lines:
  echo ------------------------------------------------------------
  powershell -NoProfile -Command "Get-Content '%BUILD_LOG%' -Tail 60"
  goto :fail
)

set "SETUP_EXE=release\ERP_Workbench_1.0.0rc4_Setup.exe"
if exist "%SETUP_EXE%" (
  set "HASH_FILE=release\ERP_Workbench_1.0.0rc4_Setup.sha256.txt"
  for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '%SETUP_EXE%').Hash.ToLower()"`) do set "SETUP_SHA256=%%H"
  if defined SETUP_SHA256 (
    >"!HASH_FILE!" echo !SETUP_SHA256!  ERP_Workbench_1.0.0rc4_Setup.exe
    echo SHA-256 written to !HASH_FILE!
  )
)

echo.
echo BUILD COMPLETED.
echo Full build log: %CD%\%BUILD_LOG%
goto :done

:fail
echo.
echo ============================================================
echo BUILD FAILED.
echo Full log: %CD%\%BUILD_LOG%
echo ============================================================
echo.
pause
exit /b 1

:done
echo.
pause
exit /b 0
