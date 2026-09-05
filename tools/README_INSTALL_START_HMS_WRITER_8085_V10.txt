Riverwood HMS Writer v10 - dedicated booking sidecar :8085

Purpose
-------
Restore the booking writer on 127.0.0.1:8085 without touching Operations, the availability sidecar, or the revenue dashboard.

Why this exists
---------------
The prior recovery incorrectly assumed C:\riverwood_revenue_bot\START_DASHBOARD.bat was the booking writer launcher. Its own output proved it is the revenue dashboard on port 8080. The booking adapter itself is not a standalone server process; it is a Flask blueprint installer. This package therefore creates the missing dedicated Flask host for the already-installed v10 adapter.

What the package does
---------------------
1. Verifies C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py has the exact v10 SHA256:
   23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac
2. Verifies the v10 technical card-budget marker.
3. Refuses to stop any process.
4. If port 8085 is already occupied by an unknown service, it stops safely and changes nothing.
5. Copies a small dedicated wrapper to:
   C:\riverwood_revenue_bot\hms_booking_sidecar_8085_v10.py
6. Compiles and imports the wrapper before launch.
7. Confirms the current adapter registers HMS booking routes.
8. Starts only the dedicated wrapper with the existing Riverwood venv Python.
9. Waits for 127.0.0.1:8085 and verifies /riverwood-writer-health reports the exact v10 writer SHA.
10. Saves persistent logs under:
    C:\riverwood_revenue_bot\_writer_8085_logs\

Run
---
Run INSTALL_START_HMS_WRITER_8085_V10.bat.
The window always stays open.

Success markers
---------------
RECOVERY OK
8085 LISTEN PID=<pid>
Writer SHA OK: 23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac
INSTALL/START OK

After success
-------------
Restart only C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd so the already-installed Operations runtime guard sees the fresh 8085 process. Then run Live HMS preflight before booking.
