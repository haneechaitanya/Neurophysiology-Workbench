@echo off
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (set "PY=.venv\Scripts\python.exe") else (set "PY=python")
set "PYTHONPATH=%CD%"

echo Checking Python package consistency...
%PY% -m pip check || goto :fail

echo Compiling source...
%PY% -m compileall -q erpworkbench app.py || goto :fail

echo Running backend smoke tests...
for %%T in (
  smoke_core.py
  smoke_annotations.py
  smoke_event_groups.py
  smoke_epoching_v05.py
  smoke_review_workflow_v06.py
  smoke_averaging_v07.py
  smoke_grand_average_v08.py
  smoke_grand_export_v08_refined.py
  smoke_ica_beta_v09.py
  smoke_ica_blink_aid_v10.py
  smoke_ica_chunked_reconstruction_v10.py
  smoke_ica_gui_completion_v10.py
  smoke_p2p_v10.py
  smoke_protocol_exclusions_v10.py
  smoke_protocol_display_components_v10.py
) do (
  echo   %%T
  %PY% "tests\%%T" || goto :fail
)

echo.
echo ALL V1.0 BACKEND CHECKS PASSED.
pause
exit /b 0

:fail
echo.
echo VERIFICATION FAILED. Review the error above.
pause
exit /b 1
