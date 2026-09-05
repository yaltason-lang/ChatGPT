@echo off
setlocal
cd /d "%~dp0"
echo Riverwood LIVE :8085 writer capture - READ ONLY
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0capture_live_8085_writer.ps1"
if errorlevel 1 (
  echo.
  echo CAPTURE FAILED
  pause
  exit /b 1
)
echo.
echo CAPTURE OK
echo Open folder: %~dp0CAPTURED_LIVE_8085_WRITER
explorer.exe "%~dp0CAPTURED_LIVE_8085_WRITER"
pause
