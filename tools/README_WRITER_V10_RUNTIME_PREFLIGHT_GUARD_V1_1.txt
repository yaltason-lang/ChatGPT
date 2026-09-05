Riverwood HMS Writer v10 - Runtime Preflight Guard V1.1 NO-CIM

Purpose
-------
Fix the V1 installer failure on Windows where Get-CimInstance returned a PID but did not expose ExecutablePath/CommandLine for the :8085 listener.

What V1.1 changes
-----------------
1. Operations preflight guard is installed FIRST and remains fail-closed.
2. The writer restart no longer depends on Win32_Process.CommandLine or ExecutablePath.
3. Before stopping anything it verifies:
   - C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py exists;
   - exact SHA256 = 23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac;
   - marker RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE exists;
   - :8085 has a Python LISTEN process;
   - C:\riverwood_revenue_bot\.venv\Scripts\python.exe exists.
4. It stops ONLY the current :8085 listener PID.
5. It starts the verified writer directly with:
   C:\riverwood_revenue_bot\.venv\Scripts\python.exe -u C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py
6. It requires a NEW Python listener PID on :8085 and rechecks the exact writer SHA.
7. :8082 is not touched.
8. Operations :5050 is not restarted automatically.

After INSTALL OK
----------------
Restart only:
C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd

Then open the quote and click "Оновити live preflight" BEFORE booking.

If the writer restart fails after the Operations guard was installed, DO NOT BOOK. Restart Operations anyway: the new guard will block booking until :8085 is confirmed fresh.
