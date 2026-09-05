Riverwood HMS Writer :8085 Native Launcher Recovery V1

Purpose
-------
Restore the HMS booking writer after the old :8085 process was stopped and a guessed direct python launch failed.

Safety
------
- Does NOT patch Operations.
- Does NOT modify writer source.
- Does NOT touch :8082.
- Requires exact writer SHA256:
  23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac
- Requires the v10 technical card-budget marker.
- If :8085 is already listening, exits successfully without restarting it.
- Searches only active non-backup .bat/.cmd/.ps1 files under C:\riverwood_revenue_bot for an exact reference to pms_booking_adapter_v5328.py.
- Automatically starts a native launcher ONLY when exactly one such launcher exists.
- If multiple launchers are found, starts nothing and prints the list.
- If no exact launcher is found, performs one direct diagnostic launch with stdout/stderr redirected to C:\riverwood_revenue_bot\_writer_recovery_logs and prints the exact startup error.

Run
---
Double-click RECOVER_WRITER_8085_NATIVE_LAUNCHER_V1.bat.
The PowerShell script self-elevates through UAC if needed.

After RECOVERY OK
-----------------
1. Restart only C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd
2. Open the quote.
3. Click Update live preflight.
4. Do NOT book until live preflight is READY and the writer runtime guard passes.
