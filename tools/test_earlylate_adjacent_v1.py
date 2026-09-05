from __future__ import annotations

import ast
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List

import patch_live_operations_earlylate_v1 as patcher


def _ival(value, default=0, minimum=None, maximum=None):
    try:
        out = int(value)
    except Exception:
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def test_helper_logic():
    calls = []
    def fake_available(arrival, departure):
        calls.append((arrival, departure))
        if (arrival, departure) == ('2026-10-08', '2026-10-09'):
            return {'2', '3'}, {}, []
        if (arrival, departure) == ('2026-10-11', '2026-10-12'):
            return {'3', '4'}, {}, []
        return set(), {}, []

    ns = {
        'List': List, 'Dict': Dict, 'Any': Any, 'Iterable': Iterable,
        'date': date, 'timedelta': timedelta, '_ival': _ival,
        '_available_room_tokens_for_period': fake_available,
    }
    exec(patcher.HELPERS, ns)
    constraints = ns['_group_adjacent_day_constraints'](
        [
            {'date': '2026-10-09', 'next_date': '2026-10-10', 'guest_count': 4},
            {'date': '2026-10-10', 'next_date': '2026-10-11', 'guest_count': 4},
        ],
        early_checkin=True, late_checkout=True,
    )
    assert calls == [('2026-10-08', '2026-10-09'), ('2026-10-11', '2026-10-12')], calls
    rows = [{
        'room_type_id': 2, 'configured': True, 'capacity_per_room': 3,
        'available_rooms': 3, 'active_rooms_whole_stay': 3,
        'room_ids': [1, 2, 3], 'room_labels': ['101', '102', '103'], 'total_capacity': 9,
    }]
    early = ns['_filter_rows_for_adjacent_day'](
        rows, constraints['early_allowed'], service='early', period=constraints['early_period']
    )
    assert early[0]['room_ids'] == [2, 3]
    assert early[0]['room_labels'] == ['102', '103']
    assert early[0]['available_rooms'] == 2
    both = ns['_filter_rows_for_adjacent_day'](
        early, constraints['late_allowed'], service='late', period=constraints['late_period']
    )
    assert both[0]['room_ids'] == [3], both
    assert both[0]['available_rooms'] == 1
    msg = ns['_adjacent_day_fit_error']('2026-10-09', constraints, early=True, late=True)
    assert 'НЕ ВМІЩАЄМО' in msg
    assert '2026-10-08 → 2026-10-09' in msg
    assert '2026-10-11 → 2026-10-12' in msg


def test_source_patch():
    source = Path('accommodation_module.py').read_text(encoding='utf-8-sig')
    patched, meta = patcher.patch_text(source)
    patcher.verify_text(patched)
    assert patcher.MARKER in patched
    assert 'early_checkin=bool(early_checkin), late_checkout=bool(late_checkout)' in patched
    assert "'paid_children'" in patched
    tree = ast.parse(patched)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    varying_src = ast.get_source_segment(patched, funcs['_calculate_varying_daily_group']) or ''
    assert varying_src.find('_filter_rows_for_adjacent_day(') < varying_src.find('_auto_allocate(')


def test_obsolete_blocker_removal():
    sample = '''\ndef _hms_booking_state(q):\n    write_blockers = []\n    if q.get("early_checkin") or q.get("late_checkout"):\n        write_blockers.append("Автоматичний запис раннього заїзду/пізнього виїзду в HMS ще не зіставлений з полями GroupCard.")\n    return write_blockers\n'''
    cleaned, count = patcher._remove_obsolete_earlylate_blocker(sample)
    assert count == 1
    assert 'раннього заїзду/пізнього виїзду' not in cleaned
    compile(cleaned, '<cleaned>', 'exec')


if __name__ == '__main__':
    test_helper_logic()
    test_source_patch()
    test_obsolete_blocker_removal()
    print('EARLYLATE ADJACENT-DAY TESTS: PASS')
