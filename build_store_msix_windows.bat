@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

set "STORE_LOG=store_build.log"
set "STORE_VERSION=1.0.0.0"
set "STORE_PACKAGE=store_package\ERP_Workbench_!STORE_VERSION!_x64.msix"
set "RESUME_PACKAGE="
if /I "%~1"=="--resume-package" set "RESUME_PACKAGE=1"

echo ============================================================
echo ERP Workbench - Microsoft Store MSIX staging build
echo ============================================================
echo Build started: %DATE% %TIME% > "%STORE_LOG%"

if not exist ".venv\Scripts\python.exe" (
  echo ERROR: The audited .venv was not found.
  goto :fail
)
set "PY=.venv\Scripts\python.exe"

for %%F in ("ERPWorkbench.spec" "store\AppxManifest.xml" "tools\prepare_msix_assets.py" "tools\audit_store_package.py" "tools\find_windows_sdk_tool.ps1") do (
  if not exist %%F (
    echo ERROR: %%~F was not found.
    goto :fail
  )
)

if defined RESUME_PACKAGE (
  echo Resuming from the completed Store frozen/layout build...
  if not exist "store_build\frozen\ERPWorkbenchStore\ERPWorkbench.exe" (
    echo ERROR: The completed Store frozen build is unavailable. Run without --resume-package.
    goto :fail
  )
  if not exist "store_build\layout\AppxManifest.xml" (
    echo ERROR: The completed Store package layout is unavailable. Run without --resume-package.
    goto :fail
  )
  if not exist "store_build\pyinstaller_archive_listing.txt" (
    echo ERROR: The Store archive listing is unavailable. Run without --resume-package.
    goto :fail
  )
  echo Refreshing the staged manifest from the current Store manifest...
  copy /Y "store\AppxManifest.xml" "store_build\layout\AppxManifest.xml" >nul
  if errorlevel 1 goto :fail
  if not exist store_package mkdir store_package
  goto :locate_makeappx
)

echo [1/7] Running the complete source verification suite...
call verify_v1_windows.bat --no-pause
if errorlevel 1 goto :fail

echo [2/7] Building the isolated Store application folder...
if exist store_build rmdir /s /q store_build
if exist store_package rmdir /s /q store_package
mkdir store_build
mkdir store_package

set "ERP_WORKBENCH_STORE_BUILD=1"
set "ERP_WORKBENCH_DISTRIBUTION=store"
"%PY%" -m PyInstaller --noconfirm --clean --log-level INFO --distpath "store_build\frozen" --workpath "store_build\work" ERPWorkbench.spec >> "%STORE_LOG%" 2>&1
set "PYINSTALLER_EXIT=!ERRORLEVEL!"
set "ERP_WORKBENCH_STORE_BUILD="
set "ERP_WORKBENCH_DISTRIBUTION="
if not "!PYINSTALLER_EXIT!"=="0" goto :pyinstaller_fail

if not exist "store_build\frozen\ERPWorkbenchStore\ERPWorkbench.exe" (
  echo ERROR: Store frozen executable was not created.
  goto :fail
)

echo [3/7] Confirming the GitHub updater module is absent...
"%PY%" -m PyInstaller.utils.cliutils.archive_viewer -r -b "store_build\frozen\ERPWorkbenchStore\ERPWorkbench.exe" > "store_build\pyinstaller_archive_listing.txt" 2>> "%STORE_LOG%"
if errorlevel 1 goto :fail
findstr /I /C:"erpworkbench.updater" "store_build\pyinstaller_archive_listing.txt" >nul
if not errorlevel 1 (
  echo ERROR: The Store executable still contains erpworkbench.updater.
  goto :fail
)

echo [4/7] Creating the MSIX package layout and visual assets...
mkdir "store_build\layout"
mkdir "store_build\layout\ERPWorkbench"
xcopy "store_build\frozen\ERPWorkbenchStore\*" "store_build\layout\ERPWorkbench\" /E /I /H /Y >nul
copy /Y "store\AppxManifest.xml" "store_build\layout\AppxManifest.xml" >nul
"%PY%" "tools\prepare_msix_assets.py" "assets\erp_workbench_icon.png" "store_build\layout\Assets" >> "%STORE_LOG%" 2>&1
if errorlevel 1 goto :fail

echo [5/7] Locating MakeAppx from the Windows SDK...
:locate_makeappx
set "MAKEAPPX="
powershell -NoProfile -ExecutionPolicy Bypass -File "tools\find_windows_sdk_tool.ps1" makeappx.exe > "store_build\makeappx_path.txt"
if not errorlevel 1 set /p MAKEAPPX=<"store_build\makeappx_path.txt"
if defined MAKEAPPX if not exist "!MAKEAPPX!" set "MAKEAPPX="
if not defined MAKEAPPX (
  echo ERROR: MakeAppx.exe was not found.
  echo Install the Windows 10 or Windows 11 SDK, then run this file again.
  goto :fail
)

echo [6/7] Packing the unsigned Store MSIX...
"!MAKEAPPX!" pack /d "store_build\layout" /p "!STORE_PACKAGE!" /o /v >> "%STORE_LOG%" 2>&1
if errorlevel 1 goto :makeappx_fail

echo [7/7] Creating the privacy-safe Store package audit...
"%PY%" "tools\audit_store_package.py" "!STORE_PACKAGE!" --archive-listing "store_build\pyinstaller_archive_listing.txt" --expected-version "!STORE_VERSION!"
if errorlevel 1 goto :fail

for /f "usebackq delims=" %%H in (`powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 '!STORE_PACKAGE!').Hash.ToLower()"`) do set "STORE_SHA256=%%H"
if defined STORE_SHA256 >"!STORE_PACKAGE!.sha256.txt" echo !STORE_SHA256!  ERP_Workbench_!STORE_VERSION!_x64.msix

echo.
echo STORE STAGING BUILD COMPLETED.
echo Package: %CD%\!STORE_PACKAGE!
echo This MSIX is intentionally unsigned and is not ready for submission or installation.
echo Upload the new ERP_Workbench_store_package_audit_*.zip from release_audit.
echo.
pause
exit /b 0

:pyinstaller_fail
echo PyInstaller failed. Last 80 log lines:
powershell -NoProfile -Command "Get-Content '%STORE_LOG%' -Tail 80"
goto :fail

:makeappx_fail
echo MakeAppx failed. Last 80 log lines:
powershell -NoProfile -Command "Get-Content '%STORE_LOG%' -Tail 80"
goto :fail

:fail
set "ERP_WORKBENCH_STORE_BUILD="
set "ERP_WORKBENCH_DISTRIBUTION="
echo.
echo STORE STAGING BUILD FAILED. Review %STORE_LOG% and the messages above.
pause
exit /b 1
