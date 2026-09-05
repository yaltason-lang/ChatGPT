Riverwood HMS Writer :8085 EXACT Native Recovery V2

Uses the exact native launcher observed in the prior working live capture:
C:\riverwood_revenue_bot\START_DASHBOARD.bat

The package verifies the exact writer v10 SHA and marker, launches only START_DASHBOARD.bat when :8085 is down, waits for a fresh Python listener on :8085, captures stdout/stderr, writes LAST_RECOVERY_RESULT.txt, and keeps the console open.

After RECOVERY OK, restart only C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd and run live preflight before booking.
