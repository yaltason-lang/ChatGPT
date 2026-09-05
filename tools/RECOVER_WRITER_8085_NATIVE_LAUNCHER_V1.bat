@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Riverwood HMS Writer :8085 native-launcher recovery V1
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recover_writer_8085_native_launcher_v1.ps1"
set RC=%ERRORLEVEL%
echo.
if not "%RC%"=="0" (
  echo RECOVERY DID NOT COMPLETE. Read the exact output above.
  pause
  exit /b %RC%
)
echo RECOVERY COMPLETED.
echo Do not book yet. Restart Operations dashboard and run live preflight first.
pause
exit /b 0
