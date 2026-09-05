@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Riverwood HMS Writer v10 - runtime alignment + Operations preflight guard
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_verified_writer_v10.ps1"
if errorlevel 1 goto :fail
echo.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0patch_operations_writer_runtime_guard_v1.py"
) else (
  python "%~dp0patch_operations_writer_runtime_guard_v1.py"
)
if errorlevel 1 goto :fail
echo.
echo INSTALL OK
echo Restart ONLY Operations using:
echo C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd
echo Then open the quote and click Update live preflight BEFORE booking.
echo :8082 was not touched.
echo :8085 was restarted only after exact v10 SHA verification.
pause
exit /b 0

:fail
echo.
echo INSTALL FAILED - see the exact message above. No unverified writer was restarted.
pause
exit /b 1
