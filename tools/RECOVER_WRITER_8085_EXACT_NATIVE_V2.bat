@echo off
setlocal
cd /d "%~dp0"

echo Riverwood HMS Writer :8085 EXACT native recovery V2
echo.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Administrator rights are required. Opening UAC prompt...
  powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0recover_writer_8085_exact_native_v2.ps1"
set RC=%errorlevel%

echo.
if "%RC%"=="0" (
  echo RECOVERY SCRIPT FINISHED OK
) else (
  echo RECOVERY SCRIPT FAILED WITH CODE %RC%
  echo The exact result is also saved to:
  echo C:\riverwood_revenue_bot\_writer_recovery_logs\LAST_RECOVERY_RESULT.txt
)
echo.
echo THIS WINDOW WILL NOT CLOSE AUTOMATICALLY.
pause
exit /b %RC%
