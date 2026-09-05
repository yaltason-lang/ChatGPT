Riverwood Operations - EARLY/LATE ADJACENT HOTEL-DAY ALLOCATION V1

Purpose
- Keep the already working HMS booking flow intact.
- Apply early check-in / late check-out as physical-room constraints BEFORE allocation.

Rules implemented
1. Early check-in: selected physical RoomID must also be free for the previous hotel night.
2. Late check-out: selected physical RoomID must also be free for the following hotel night.
3. For a one-night stay with both services, the RoomID must pass both adjacent-night checks.
4. The filtered physical-room inventory is used by automatic, best-price and manual-selected-room allocation.
5. Paid children, extra beds, room composition and the existing HMS writer payload are preserved.
6. HMS live preflight repeats the room/date checks before booking.
7. The obsolete blanket early/late write blocker is removed only if it is present inside _hms_booking_state.

Deployment safety
- Exact captured Operations source SHA256 gate before first apply:
  139bf9dcf3bea9142d3e41b1d083dd5afe06489e8ca5d278a4bfe8b93b89727c
- Target file only:
  C:\Riverwood_Operations_MVP0_Core_Employees\accommodation_module.py
- Backup is created under:
  C:\Riverwood_Operations_MVP0_Core_Employees\_backups\before_earlylate_adjacent_v1_YYYYMMDD_HHMMSS\
- The installer does NOT stop/start/restart any service.
- :8082 is never touched.
- Writer :8085 is not modified.

Install
1. Run APPLY_EARLYLATE_ADJACENT_DAY_V1.bat.
2. Wait for APPLY OK / INSTALL OK.
3. Restart ONLY Operations dashboard using C:\Riverwood_Operations_MVP0_Core_Employees\1_START_DASHBOARD.cmd (local :5050).
4. Recalculate the quote from the calculator so room selection is rebuilt using the adjacent-day HMS constraints.
5. Save the new revision, refresh live preflight, then book in HMS.
