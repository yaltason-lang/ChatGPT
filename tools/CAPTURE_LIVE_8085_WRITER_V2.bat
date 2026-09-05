@echo off
setlocal
cd /d "%~dp0"
echo Riverwood LIVE :8085 writer capture - READ ONLY
echo CAPTURE TOOL V2
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_live_8085_writer_v2.ps1"
if errorlevel 1 (
  echo.
  echo CAPTURE FAILED
  pause
  exit /b 1
)
echo.
echo CAPTURE OK
echo Open folder: %~dp0CAPTURED_LIVE_8085_WRITER_V2
explorer.exe "%~dp0CAPTURED_LIVE_8085_WRITER_V2"
pause
