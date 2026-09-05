@echo off
setlocal EnableExtensions
cd /d "%~dp0"
echo Riverwood HMS Writer v10 - runtime alignment + preflight guard V1.1 NO-CIM
echo.

rem 1) Install the Operations fail-closed guard FIRST.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0patch_operations_writer_runtime_guard_v1.py"
) else (
  python "%~dp0patch_operations_writer_runtime_guard_v1.py"
)
if errorlevel 1 goto :guard_fail

echo.
echo Operations preflight guard installed/verified.
echo Now restarting ONLY verified writer :8085 without CIM CommandLine dependency.
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restart_verified_writer_v10_no_cim.ps1"
if errorlevel 1 goto :restart_fail

echo.
echo INSTALL OK
echo Restart ONLY Operations using:
echo C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd
echo Then open the quote and click Update live preflight BEFORE booking.
echo :8082 was not touched.
echo :8085 was restarted only after exact writer SHA verification.
pause
exit /b 0

:restart_fail
echo.
echo WRITER RESTART FAILED, BUT OPERATIONS PREFLIGHT GUARD IS ALREADY INSTALLED.
echo DO NOT BOOK. Restart Operations dashboard so the guard becomes active.
echo The guard will keep HMS booking blocked until :8085 is a fresh verified v10 runtime.
pause
exit /b 2

:guard_fail
echo.
echo INSTALL FAILED BEFORE WRITER RESTART.
echo No writer process was stopped.
pause
exit /b 1
