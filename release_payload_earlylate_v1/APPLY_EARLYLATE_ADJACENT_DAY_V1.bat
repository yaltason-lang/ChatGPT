@echo off
setlocal
cd /d "%~dp0"
echo Riverwood Operations - EARLY/LATE ADJACENT HOTEL-DAY ALLOCATION V1
echo.
set "VENV_PY=C:\Riverwood_Operations_MVP0_Core_Employees\.venv\Scripts\python.exe"
if exist "%VENV_PY%" (
  "%VENV_PY%" "%~dp0patch_live_operations_earlylate_v1.py"
) else (
  py -3 "%~dp0patch_live_operations_earlylate_v1.py"
)
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
  echo.
  echo INSTALL FAILED
  pause
  exit /b %RC%
)
echo.
echo INSTALL OK
echo Restart ONLY Riverwood Operations dashboard via 1_START_DASHBOARD.cmd ^(local :5050^).
echo Do NOT restart or modify :8082. Writer :8085 is not changed by this package.
pause
exit /b 0
