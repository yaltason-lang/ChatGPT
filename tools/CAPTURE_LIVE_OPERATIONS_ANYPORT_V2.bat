@echo off
setlocal
cd /d "%~dp0"
echo Riverwood Operations live-source capture ANYPORT V2 - READ ONLY
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_live_operations_anyport_v2.ps1"
if errorlevel 1 (
  echo.
  echo CAPTURE FAILED
  pause
  exit /b 1
)
echo.
echo CAPTURE OK
if exist "%~dp0CAPTURED_LIVE_OPERATIONS_ANYPORT_V2.zip" explorer.exe /select,"%~dp0CAPTURED_LIVE_OPERATIONS_ANYPORT_V2.zip"
pause
