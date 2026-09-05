from __future__ import annotations

import argparse
import ast
import hashlib
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

MARKER = '# RIVERWOOD_EARLYLATE_ADJACENT_DAY_ALLOC_V1'
EXPECTED_LIVE_SHA256 = '139bf9dcf3bea9142d3e41b1d083dd5afe06489e8ca5d278a4bfe8b93b89727c'
DEFAULT_TARGET = Path(r'C:\Riverwood_Operations_MVP0_Core_Employees\accommodation_module.py')

HELPERS = r'''
# RIVERWOOD_EARLYLATE_ADJACENT_DAY_ALLOC_V1
# Early check-in consumes the previous hotel night for the selected physical RoomID.
# Late check-out consumes the following hotel night for the selected physical RoomID.
# These constraints are applied BEFORE allocation; preflight repeats the check fail-closed.
def _filter_rows_for_adjacent_day(
    rows: List[Dict[str, Any]], allowed_tokens: Iterable[Any], *, service: str, period: str,
) -> List[Dict[str, Any]]:
    allowed = {str(x).strip() for x in (allowed_tokens or []) if str(x).strip()}
    out: List[Dict[str, Any]] = []
    for source in rows or []:
        row = dict(source)
        room_ids = list(row.get('room_ids') or [])
        room_labels = list(row.get('room_labels') or [])
        kept_ids: List[Any] = []
        kept_labels: List[str] = []
        for idx, room_id in enumerate(room_ids):
            token = str(room_id).strip()
            if token and token in allowed:
                kept_ids.append(room_id)
                kept_labels.append(str(room_labels[idx]) if idx < len(room_labels) else token)
        row['room_ids'] = kept_ids
        row['room_labels'] = kept_labels
        row['available_rooms'] = len(kept_ids)
        row['active_rooms_whole_stay'] = min(
            _ival(row.get('active_rooms_whole_stay'), len(kept_ids), minimum=0), len(kept_ids)
        )
        cap = _ival(row.get('capacity_per_room'), 0, minimum=0)
        row['total_capacity'] = len(kept_ids) * cap if row.get('configured') else 0
        row['adjacent_day_service'] = service
        row['adjacent_day_period'] = period
        out.append(row)
    return out


def _group_adjacent_day_constraints(
    schedule: List[Dict[str, Any]], *, early_checkin: bool = False, late_checkout: bool = False,
) -> Dict[str, Any]:
    active = [dict(x) for x in (schedule or []) if isinstance(x, dict) and _ival(x.get('guest_count'), 0, minimum=0) > 0]
    if not active:
        active = [dict(x) for x in (schedule or []) if isinstance(x, dict)]
    out: Dict[str, Any] = {
        'first_date': '', 'last_date': '',
        'early_allowed': set(), 'late_allowed': set(),
        'early_period': '', 'late_period': '',
        'warnings': [],
    }
    if not active:
        return out
    first_date = str(active[0].get('date') or '')
    last_date = str(active[-1].get('date') or '')
    last_next = str(active[-1].get('next_date') or '')
    out['first_date'] = first_date
    out['last_date'] = last_date

    if early_checkin:
        start = (date.fromisoformat(first_date) - timedelta(days=1)).isoformat()
        end = first_date
        allowed, _labels, ww = _available_room_tokens_for_period(start, end)
        out['early_allowed'] = set(allowed)
        out['early_period'] = f'{start} → {end}'
        out['warnings'].extend([f'Ранній заїзд · {x}' for x in ww])

    if late_checkout:
        start = last_next
        end = (date.fromisoformat(last_next) + timedelta(days=1)).isoformat()
        allowed, _labels, ww = _available_room_tokens_for_period(start, end)
        out['late_allowed'] = set(allowed)
        out['late_period'] = f'{start} → {end}'
        out['warnings'].extend([f'Пізній виїзд · {x}' for x in ww])
    return out


def _adjacent_day_fit_error(day_date: str, constraints: Dict[str, Any], *, early: bool, late: bool) -> str:
    parts: List[str] = []
    if early:
        parts.append(
            'ранній заїзд: для розміщення дозволені тільки фізичні номери, '
            f"вільні попередню готельну добу {constraints.get('early_period') or ''}"
        )
    if late:
        parts.append(
            'пізній виїзд: для розміщення дозволені тільки фізичні номери, '
            f"вільні наступну готельну добу {constraints.get('late_period') or ''}"
        )
    return f"{day_date}: НЕ ВМІЩАЄМО після live HMS-перевірки сусідньої доби ({'; '.join(parts)})."
'''.strip('\n')


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _function_nodes(text: str, name: str):
    tree = ast.parse(text)
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def _remove_obsolete_earlylate_blocker(text: str) -> tuple[str, int]:
    tree = ast.parse(text)
    states = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_hms_booking_state']
    if len(states) != 1:
        raise RuntimeError(f'Expected exactly one _hms_booking_state, found {len(states)}')
    state = states[0]
    ranges = []
    for node in ast.walk(state):
        if not isinstance(node, ast.If):
            continue
        seg = ast.get_source_segment(text, node) or ''
        if 'раннього заїзду/пізнього виїзду' in seg and 'write_blockers' in seg:
            ranges.append((node.lineno, node.end_lineno))
    if not ranges:
        return text, 0
    lines = text.splitlines(keepends=True)
    for start, end in sorted(ranges, reverse=True):
        del lines[start - 1:end]
    return ''.join(lines), len(ranges)


def patch_text(source: str) -> tuple[str, dict]:
    text = source.replace('\r\n', '\n').replace('\r', '\n')
    if MARKER in text:
        verify_text(text)
        return text, {'already_applied': True, 'obsolete_blockers_removed': 0}

    for fn in ('_available_room_tokens_for_period', '_stay_time_availability_for_plans', '_calculate_varying_daily_group', 'calculator_page', '_hms_booking_state'):
        count = len(_function_nodes(text, fn))
        if count != 1:
            raise RuntimeError(f'Expected exactly one {fn}, found {count}')

    helper_anchor = '\ndef _stay_time_availability_for_plans(\n'
    if text.count(helper_anchor) != 1:
        raise RuntimeError('Cannot find unique _stay_time_availability_for_plans insertion anchor')
    text = text.replace(helper_anchor, '\n\n' + HELPERS + '\n\n\ndef _stay_time_availability_for_plans(\n', 1)

    sig_old = (
        "    cached_payloads: Optional[Dict[str, Dict[str, Any]]] = None, manual_mode: str = '',\n"
        "    manual_day: str = '', form=None, force_live: bool = False,\n"
    )
    sig_new = (
        "    cached_payloads: Optional[Dict[str, Dict[str, Any]]] = None, manual_mode: str = '',\n"
        "    manual_day: str = '', form=None, force_live: bool = False,\n"
        "    early_checkin: bool = False, late_checkout: bool = False,\n"
    )
    if text.count(sig_old) != 1:
        raise RuntimeError('Cannot find unique _calculate_varying_daily_group signature anchor')
    text = text.replace(sig_old, sig_new, 1)

    init_old = "    warnings: List[str] = []\n    cached_payloads = cached_payloads or {}\n    conn = _db()\n\n    for day in schedule:\n"
    init_new = (
        "    warnings: List[str] = []\n"
        "    cached_payloads = cached_payloads or {}\n"
        "    conn = _db()\n"
        "    adjacent_constraints = _group_adjacent_day_constraints(\n"
        "        schedule, early_checkin=bool(early_checkin), late_checkout=bool(late_checkout),\n"
        "    )\n"
        "    warnings.extend(adjacent_constraints.get('warnings') or [])\n\n"
        "    for day in schedule:\n"
    )
    if text.count(init_old) != 1:
        raise RuntimeError('Cannot find unique varying-group init anchor')
    text = text.replace(init_old, init_new, 1)

    rows_old = "        rows = _category_rows(payload, str(day.get('placement_mode') or 'standard'), bool(day.get('include_extra')))\n        adults = _ival(day.get('adults'), 0, minimum=0)\n"
    rows_new = (
        "        rows = _category_rows(payload, str(day.get('placement_mode') or 'standard'), bool(day.get('include_extra')))\n"
        "        day_early_constraint = bool(early_checkin and day_date == str(adjacent_constraints.get('first_date') or ''))\n"
        "        day_late_constraint = bool(late_checkout and day_date == str(adjacent_constraints.get('last_date') or ''))\n"
        "        if day_early_constraint:\n"
        "            rows = _filter_rows_for_adjacent_day(\n"
        "                rows, adjacent_constraints.get('early_allowed') or set(),\n"
        "                service='early_checkin_previous_night', period=str(adjacent_constraints.get('early_period') or ''),\n"
        "            )\n"
        "        if day_late_constraint:\n"
        "            rows = _filter_rows_for_adjacent_day(\n"
        "                rows, adjacent_constraints.get('late_allowed') or set(),\n"
        "                service='late_checkout_next_night', period=str(adjacent_constraints.get('late_period') or ''),\n"
        "            )\n"
        "        adults = _ival(day.get('adults'), 0, minimum=0)\n"
    )
    if text.count(rows_old) != 1:
        raise RuntimeError('Cannot find unique category rows anchor')
    text = text.replace(rows_old, rows_new, 1)

    auto_fail_old = "            if not summary.get('fits'):\n                raise ValueError(f'{day.get(\"date_label\")}: {_allocation_failure_message(summary)}')\n"
    auto_fail_new = (
        "            if not summary.get('fits'):\n"
        "                if day_early_constraint or day_late_constraint:\n"
        "                    raise ValueError(_adjacent_day_fit_error(\n"
        "                        day_date, adjacent_constraints, early=day_early_constraint, late=day_late_constraint,\n"
        "                    ))\n"
        "                raise ValueError(f'{day.get(\"date_label\")}: {_allocation_failure_message(summary)}')\n"
    )
    if text.count(auto_fail_old) != 1:
        raise RuntimeError('Cannot find unique automatic allocation failure anchor')
    text = text.replace(auto_fail_old, auto_fail_new, 1)

    call_old = "                force_live=bool(manual_mode),\n            )\n"
    call_new = (
        "                force_live=bool(manual_mode),\n"
        "                early_checkin=bool(early_checkin), late_checkout=bool(late_checkout),\n"
        "            )\n"
    )
    # Only patch the calculator-page call that contains the varying-group keyword block.
    calc_pos = text.find('def calculator_page():')
    if calc_pos < 0:
        raise RuntimeError('calculator_page not found')
    before = text[:calc_pos]
    calc = text[calc_pos:]
    if calc.count(call_old) != 1:
        raise RuntimeError(f'Cannot find unique calculator varying-group call anchor; found {calc.count(call_old)}')
    calc = calc.replace(call_old, call_new, 1)
    text = before + calc

    text, removed = _remove_obsolete_earlylate_blocker(text)
    verify_text(text)
    return text, {'already_applied': False, 'obsolete_blockers_removed': removed}


def verify_text(text: str) -> None:
    compile(text, '<patched accommodation_module.py>', 'exec')
    tree = ast.parse(text)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in (
        '_filter_rows_for_adjacent_day', '_group_adjacent_day_constraints', '_adjacent_day_fit_error',
        '_calculate_varying_daily_group', 'calculator_page', '_hms_booking_state', '_hms_booking_payload',
    ):
        if name not in funcs:
            raise RuntimeError(f'Missing required function after patch: {name}')
    varying = funcs['_calculate_varying_daily_group']
    args = [a.arg for a in varying.args.kwonlyargs]
    if 'early_checkin' not in args or 'late_checkout' not in args:
        raise RuntimeError('Varying-group allocator does not expose early/late inputs')
    varying_src = ast.get_source_segment(text, varying) or ''
    if '_group_adjacent_day_constraints(' not in varying_src:
        raise RuntimeError('Adjacent-day HMS query is not wired into varying-group allocator')
    if '_filter_rows_for_adjacent_day(' not in varying_src:
        raise RuntimeError('Adjacent-day physical-room filter is not wired before allocation')
    calc_src = ast.get_source_segment(text, funcs['calculator_page']) or ''
    if 'early_checkin=bool(early_checkin), late_checkout=bool(late_checkout)' not in calc_src:
        raise RuntimeError('calculator_page does not pass early/late into allocator')
    state_src = ast.get_source_segment(text, funcs['_hms_booking_state']) or ''
    if 'раннього заїзду/пізнього виїзду' in state_src and 'write_blockers' in state_src:
        raise RuntimeError('Obsolete blanket early/late write blocker still present')
    payload_src = ast.get_source_segment(text, funcs['_hms_booking_payload']) or ''
    if "'early_checkin': bool(room.get('early_checkin'))" not in payload_src:
        raise RuntimeError('HMS payload lost per-room early_checkin flag')
    if "'late_checkout': bool(room.get('late_checkout'))" not in payload_src:
        raise RuntimeError('HMS payload lost per-room late_checkout flag')
    if "'paid_children'" not in payload_src:
        raise RuntimeError('HMS payload lost paid_children semantics')


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(DEFAULT_TARGET))
    parser.add_argument('--out', default='')
    parser.add_argument('--no-sha-gate', action='store_true')
    parser.add_argument('--no-backup', action='store_true')
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_file():
        print(f'FAILED: source not found: {source}')
        return 2
    old_sha = _sha256(source)
    raw = source.read_bytes()
    newline = '\r\n' if b'\r\n' in raw else '\n'
    try:
        original = raw.decode('utf-8-sig')
    except Exception as exc:
        print(f'FAILED: source is not UTF-8/UTF-8-SIG: {exc}')
        return 3

    if MARKER not in original and not args.no_sha_gate and old_sha.lower() != EXPECTED_LIVE_SHA256:
        print('FAILED: live Operations SHA256 differs from captured baseline.')
        print(f'EXPECTED: {EXPECTED_LIVE_SHA256}')
        print(f'ACTUAL  : {old_sha}')
        return 4

    try:
        patched_lf, meta = patch_text(original)
        patched = patched_lf.replace('\n', newline)
        compile(patched.replace('\r\n', '\n'), str(source), 'exec')
    except Exception as exc:
        print(f'FAILED: patch verification: {exc}')
        return 5

    out = Path(args.out) if args.out else source
    if out.resolve() == source.resolve() and not meta.get('already_applied') and not args.no_backup:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = source.parent / '_backups' / f'before_earlylate_adjacent_v1_{stamp}'
        backup_dir.mkdir(parents=True, exist_ok=True)
        backup = backup_dir / source.name
        shutil.copy2(source, backup)
        print(f'BACKUP: {backup}')

    if meta.get('already_applied'):
        print('VERIFY OK: RIVERWOOD_EARLYLATE_ADJACENT_DAY_ALLOC_V1 already applied.')
        print(f'SHA256: {old_sha}')
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + '.tmp')
    tmp.write_bytes(patched.encode('utf-8'))
    if out.resolve() == source.resolve():
        os.replace(tmp, out)
    else:
        if out.exists():
            out.unlink()
        os.replace(tmp, out)

    new_sha = _sha256(out)
    print('APPLY OK: early/late adjacent hotel-day allocation installed.')
    print(f'OLD SHA256: {old_sha}')
    print(f'NEW SHA256: {new_sha}')
    print(f'OBSOLETE EARLY/LATE BLOCKERS REMOVED: {meta.get("obsolete_blockers_removed", 0)}')
    print('No process was stopped or restarted. Restart Operations dashboard (app.py / local :5050) manually.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
