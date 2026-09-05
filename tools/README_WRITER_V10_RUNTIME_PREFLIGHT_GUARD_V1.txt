Riverwood HMS Writer v10 Runtime + Preflight Guard V1
====================================================

Purpose
-------
The exact failure this package addresses is:
HMS_GUEST_SLOT_COUNT_MISMATCH ... stay 10/11 ... before=9 after=9
appearing only after clicking Book in HMS even though Operations preflight was green.

This package does two things only:
1) proves the writer file is the exact verified v10 card-budget build and restarts the real :8085 listener so stale Python code cannot remain in memory;
2) patches Operations preflight so it refuses READY unless the exact v10 source and a fresh :8085 listener are both proven.

It does NOT touch :8082.
It does NOT rewrite allocator / paid children / early-late allocation logic.
It does NOT auto-retry ReserveGroup or uncertain bookings.

Install
-------
Run as the same Windows user that normally runs Riverwood services:
  APPLY_WRITER_V10_RUNTIME_PREFLIGHT_GUARD_V1.bat

The installer will FIRST verify:
  C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py
  SHA256 = 23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac
  marker = RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE

Only after those checks pass does it restart the existing verified :8085 process using its captured executable and command line.
Then it patches:
  C:\Riverwood_Operations_MVP0_Core_Employees\accommodation_module.py

After INSTALL OK restart ONLY Operations with:
  C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd

Then open ACC-20260829-002 and click "Оновити live preflight".
Expected result:
- if writer/runtime is correct: preflight can become READY and explicitly reports verified writer runtime;
- if writer source/runtime is wrong: preflight is BLOCKED before booking and gives the exact writer reason.

If the installer stops on a writer SHA mismatch, do NOT book and do NOT retry ReserveGroup. Send the exact EXPECTED/ACTUAL SHA line.
