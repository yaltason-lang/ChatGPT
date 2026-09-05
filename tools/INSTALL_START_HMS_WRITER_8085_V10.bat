@echo off
setlocal
cd /d "%~dp0"
echo ============================================================
echo Riverwood HMS Writer v10 - dedicated sidecar on 127.0.0.1:8085
echo ============================================================
echo This window will stay open.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_start_hms_writer_8085_v10.ps1"
set RC=%ERRORLEVEL%
echo.
if "%RC%"=="0" (
  echo INSTALL/START OK
) else (
  echo INSTALL/START FAILED WITH CODE %RC%
  echo See C:\riverwood_revenue_bot\_writer_8085_logs\LAST_START_RESULT.txt
)
echo.
echo THIS WINDOW WILL NOT CLOSE AUTOMATICALLY.
pause
exit /b %RC%
