@echo off
setlocal
cd /d "%~dp0"
echo Riverwood LIVE :8080 Operations capture - READ ONLY
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_live_8080_operations_v1.ps1"
if errorlevel 1 (
  echo.
  echo CAPTURE FAILED
  pause
  exit /b 1
)
echo.
echo CAPTURE OK
if exist "%~dp0CAPTURED_LIVE_8080_OPERATIONS" start "" "%~dp0CAPTURED_LIVE_8080_OPERATIONS"
pause
