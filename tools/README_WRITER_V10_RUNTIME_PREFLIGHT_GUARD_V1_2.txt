Riverwood HMS Writer v10 - Runtime Preflight Guard V1.2 SELF-ELEVATING

Why V1.2 exists
----------------
V1.1 correctly verified the exact v10 writer source and the exact :8085 listener PID, but Windows refused Stop-Process with Access Denied because the installer was not elevated.

V1.2 fixes only that deployment issue.

Behavior
--------
1. Starts normally from the BAT file.
2. If not already Administrator, requests one UAC elevation automatically.
3. After elevation, verifies the Operations preflight guard marker. If already installed, it does not rewrite accommodation_module.py.
4. Restarts ONLY the verified :8085 Python listener using the existing no-CIM restart script.
5. The restart script still requires:
   - C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py
   - SHA256 23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac
   - marker RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE
   - existing Python listener on :8085
   - C:\riverwood_revenue_bot\.venv\Scripts\python.exe
6. :8082 is not touched.
7. Operations :5050 is not restarted automatically.

After INSTALL OK
----------------
Restart only:
C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd

Then open the quote and click "Оновити live preflight" BEFORE booking.
