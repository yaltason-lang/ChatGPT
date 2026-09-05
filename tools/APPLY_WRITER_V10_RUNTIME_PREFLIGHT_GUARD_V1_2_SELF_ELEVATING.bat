@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Riverwood HMS Writer v10 - Runtime Preflight Guard V1.2 SELF-ELEVATING
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0apply_writer_v10_runtime_guard_v1_2_elevated.ps1"
if errorlevel 1 goto :fail
echo.
echo INSTALL OK
echo Restart ONLY Operations using:
echo C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd
echo Then open the quote and click Update live preflight BEFORE booking.
echo :8082 was not touched.
pause
exit /b 0

:fail
echo.
echo INSTALL FAILED - see the exact message above.
echo If a UAC prompt appeared, it must be accepted for the verified :8085 restart.
pause
exit /b 1
