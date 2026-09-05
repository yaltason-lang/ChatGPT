from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import zipfile
import xml.etree.ElementTree as ET
import socket
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

bp = Blueprint('accommodation', __name__)
_DEPS: Dict[str, Any] = {}

ROOM_TYPE_NAMES = {
    2: 'Стандарт Дабл з видом на ліс',
    3: 'Стандарт Дабл з видом на озеро',
    4: 'Стандарт Твін з видом на ліс',
    5: 'Стандарт Твін з видом на озеро',
    6: 'Сімейний',
    7: 'Люкс',
    8: 'Шале 1К',
    9: 'Шале 3К і В',
    10: 'Шале 2К',
    11: 'Шале 2К і В',
    12: 'Шале 1К і В',
}

# Only values explicitly established by the project conversation are prefilled.
# Unknown business capacities remain zero and therefore cannot participate in allocation
# until a manager fills them in manually.
ROOM_RULE_SEED = {
    2: dict(standard_capacity=2, room_capacity=1, bed_capacity=1, extra_capacity=1, structure_note='Стандарт Double: звичайне розміщення — 2 гості; 1 окрема кімната; при розміщенні по окремих ліжках — 1 гість; HMS control case підтверджує 1 extra bed.'),
    3: dict(standard_capacity=2, room_capacity=1, bed_capacity=1, extra_capacity=0, structure_note='Стандарт Double: звичайне розміщення — 2 гості; 1 окрема кімната; при розміщенні по окремих ліжках — 1 гість.'),
    4: dict(standard_capacity=2, room_capacity=1, bed_capacity=2, extra_capacity=0, structure_note='Стандарт Twin: звичайне розміщення — 2 гості; 1 окрема кімната; 2 окремі ліжка.'),
    5: dict(standard_capacity=2, room_capacity=1, bed_capacity=2, extra_capacity=0, structure_note='Стандарт Twin: звичайне розміщення — 2 гості; 1 окрема кімната; 2 окремі ліжка.'),
    9: dict(standard_capacity=6, room_capacity=3, bed_capacity=6, extra_capacity=1, structure_note='Шале 3К + вітальня: звичайне/по основних ліжках — до 6 гостей; 3 основні кімнати; +1 диван.'),
    10: dict(standard_capacity=4, room_capacity=2, bed_capacity=4, extra_capacity=0, structure_note='Шале 2К: звичайне/по основних ліжках — до 4 гостей; 2 основні кімнати.'),
    11: dict(standard_capacity=4, room_capacity=2, bed_capacity=4, extra_capacity=1, structure_note='Шале 2К + вітальня: звичайне/по основних ліжках — до 4 гостей; 2 основні кімнати; +1 диван.'),
    12: dict(room_capacity=1, bed_capacity=0, extra_capacity=1, structure_note='Шале 1К + вітальня: 1 основна кімната; +1 додаткове місце у вітальні. Місткість по ліжках треба підтвердити вручну.'),
}

PLACEMENT_LABELS = {
    'standard': 'Звичайне готельне розміщення',
    'rooms': '1 людина в окремій кімнаті / спальні',
    'beds': '1 людина на окремому ліжку',
    'max': 'Максимальне розміщення з додатковими місцями',
}
CAPACITY_PROBE_LABELS = {
    'beds': 'Максимум гостей на окремих ліжках',
    'rooms': 'Максимум гостей в окремих кімнатах / спальнях',
    'max': 'Абсолютний максимум з усіма додатковими місцями',
}

STRATEGY_LABELS = {
    'priority': 'За логікою розміщення / пріоритетом категорій',
    'fewest_rooms': 'Мінімум фізичних номерів',
    'best_price': 'Найвигідніша за актуальною ціною',
}

GUEST_INPUT_MODE_LABELS = {
    'count': 'За кількістю гостей',
    'list': 'За списком гостей',
}
GUEST_TYPE_LABELS = {
    'adult': 'Дорослий',
    'child': 'Дитина',
}
GUEST_PREFERENCE_LABELS = {
    'auto': 'Без особливих умов',
    'twin': 'Бажано Twin / окремі ліжка',
    'separate_bed': 'Обов’язково окреме ліжко',
    'separate_room': 'Окрема кімната',
}

# Business default for the ordinary/public HMS price list. The current Room Quote
# handoff uses PriceListID=2 as the standard request contract. An ENV override keeps
# this deployment-safe without exposing technical IDs in the manager UI.
DEFAULT_BASE_PRICE_LIST_ID = 2

# Exact corporate HMS PriceList mapping captured from the authenticated 31.08.2026
# NewReservation/GroupCard flow. Only these discounts are allowed for automatic HMS
# write; every other percentage remains fail-closed instead of being approximated.
HMS_CORPORATE_PRICE_LIST_BY_DISCOUNT = {
    Decimal('0.00'): (2, 'BAR_BB'),
    Decimal('10.00'): (80, 'CORP_BB_10%'),
    Decimal('12.00'): (1113, 'CORP_BB_12%'),
    Decimal('14.00'): (1119, 'CORP_BB_14%'),
    Decimal('15.00'): (1105, 'CORP_BB_15%'),
    Decimal('20.00'): (81, 'CORP_BB_20%'),
    Decimal('22.00'): (1111, 'CORP_BB_22%'),
    Decimal('30.00'): (82, 'CORP_BB_30%'),
}

def _hms_booking_price_list_mapping(value: Any) -> Optional[Dict[str, Any]]:
    try:
        pct = _money_decimal(value or 0).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    except Exception:
        return None
    row = HMS_CORPORATE_PRICE_LIST_BY_DISCOUNT.get(pct)
    if not row:
        return None
    return {'discount_percent': float(pct), 'price_list_id': int(row[0]), 'price_list_name': str(row[1])}

# Riverwood owns 10 portable extra beds that can be installed only in Standard
# categories. Sofas / built-in extra places in Suites, Family rooms and Chalets do
# not consume this physical pool. The quantity is editable in Operations settings.
STANDARD_PORTABLE_EXTRA_BED_ROOM_TYPE_IDS = frozenset({2, 3, 4, 5})
DEFAULT_STANDARD_EXTRA_BED_POOL = 10
DEFAULT_EARLY_CHECKIN_PERCENT = Decimal('50.00')
DEFAULT_LATE_CHECKOUT_PERCENT = Decimal('50.00')
DEFAULT_TOURIST_TAX_PER_ADULT_NIGHT = Decimal('43.24')


def _db():
    return _DEPS['db']()


def _audit(entity_type: str, entity_id: str, action: str, **kwargs: Any) -> None:
    try:
        _DEPS['audit'](entity_type, entity_id, action, **kwargs)
    except Exception:
        pass


def _now() -> str:
    try:
        return str(_DEPS['now_iso']())
    except Exception:
        return datetime.now().isoformat(timespec='seconds')


def _actor() -> str:
    try:
        return str(_DEPS['current_employee_id']() or '')
    except Exception:
        return ''


def _uses_portable_standard_bed(room_type_id: Any) -> bool:
    return _ival(room_type_id, 0) in STANDARD_PORTABLE_EXTRA_BED_ROOM_TYPE_IDS


def _setting_int(key: str, default: int, *, minimum: int = 0, maximum: int = 9999, conn=None) -> int:
    try:
        conn = conn or _db()
        row = conn.execute('SELECT value_text FROM accommodation_settings WHERE setting_key=?', (str(key),)).fetchone()
        value = row['value_text'] if row else default
    except Exception:
        value = default
    return _ival(value, default, minimum=minimum, maximum=maximum)


def _standard_extra_bed_pool(conn=None) -> int:
    return _setting_int('standard_extra_bed_pool', DEFAULT_STANDARD_EXTRA_BED_POOL, minimum=0, maximum=100, conn=conn)


def _portable_standard_bed_usage(room_plan: Iterable[Dict[str, Any]]) -> int:
    return sum(
        _ival(room.get('extra_beds'), 0, minimum=0)
        for room in room_plan
        if _uses_portable_standard_bed(room.get('room_type_id'))
    )


def _ival(value: Any, default: int = 0, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    try:
        out = int(str(value).strip())
    except Exception:
        out = default
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _bool_form(name: str) -> int:
    return 1 if request.form.get(name) in ('1', 'on', 'true', 'yes') else 0


def _parse_dates(arrival: str, departure: str) -> Tuple[date, date, int]:
    try:
        a = date.fromisoformat(arrival)
        d = date.fromisoformat(departure)
    except Exception as exc:
        raise ValueError('Вкажіть коректні дати заїзду та виїзду.') from exc
    if d <= a:
        raise ValueError('Дата виїзду повинна бути пізніше дати заїзду.')
    nights = (d - a).days
    if nights > 90:
        raise ValueError('PMS Availability Sidecar дозволяє максимум 90 ночей у одному запиті.')
    return a, d, nights




def _daily_schedule_from_form(
    arrival: str, departure: str, *, adults: int, children: int, paid_children: int,
    placement_mode: str, include_extra: int, form=None,
) -> List[Dict[str, Any]]:
    """Read the per-night group composition.

    The top-level guest/placement fields are defaults.  Every night has its own explicit
    row in the form, so a group may change headcount or placement from one date to the
    next without creating a separate commercial proposal.
    """
    a, _d, nights = _parse_dates(arrival, departure)
    form = form or request.form
    out: List[Dict[str, Any]] = []
    for idx in range(nights):
        day = a + timedelta(days=idx)
        next_day = day + timedelta(days=1)
        key = day.strftime('%Y%m%d')
        prefix = f'day_{key}_'
        # Per-night fields are overrides only after the manager explicitly edits that night.
        # Otherwise the canonical source is the top-level group composition. This prevents
        # stale day rows (for example the old 30-guest test default) from silently overriding
        # newly entered Adults / Children values.
        override_raw = str(form.get(prefix + 'manual_override') or '').strip().lower()
        has_override = override_raw in ('1', 'on', 'true', 'yes')
        if has_override:
            da = _ival(form.get(prefix + 'adults'), adults, minimum=0)
            dc = _ival(form.get(prefix + 'children'), children, minimum=0)
            dp = _ival(form.get(prefix + 'paid_children'), paid_children, minimum=0)
            mode = str(form.get(prefix + 'placement_mode') or placement_mode).strip()
            extra_raw = form.get(prefix + 'include_extra')
            extra = 1 if extra_raw in ('1','on','true','yes') else 0
        else:
            da, dc, dp = adults, children, paid_children
            mode = placement_mode
            extra = 1 if include_extra else 0
        if mode not in PLACEMENT_LABELS:
            mode = placement_mode if placement_mode in PLACEMENT_LABELS else 'standard'
        if dp > dc:
            raise ValueError(f'{day.strftime("%d.%m.%Y")}: платних дітей не може бути більше, ніж дітей.')
        out.append({
            'date': day.isoformat(),
            'next_date': next_day.isoformat(),
            'date_label': day.strftime('%d.%m.%Y'),
            'next_date_label': next_day.strftime('%d.%m.%Y'),
            'adults': da,
            'children': dc,
            'paid_children': dp,
            'guest_count': da + dc,
            'placement_mode': mode,
            'placement_label': PLACEMENT_LABELS.get(mode, mode),
            'include_extra': extra,
        })
    return out


def _daily_schedule_varies(schedule: Iterable[Dict[str, Any]]) -> bool:
    rows = [dict(x) for x in schedule if isinstance(x, dict)]
    if len(rows) <= 1:
        return False
    first = rows[0]
    keys = ('adults','children','paid_children','placement_mode','include_extra')
    baseline = tuple(first.get(k) for k in keys)
    return any(tuple(row.get(k) for k in keys) != baseline for row in rows[1:])


def _daily_schedule_json(schedule: Iterable[Dict[str, Any]]) -> str:
    return json.dumps(list(schedule or []), ensure_ascii=False, separators=(',', ':'))


def _row_dict(row: Any) -> Dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        return {}


def _quote_snapshot_json(
    row: Any, *, version_created_at: str = '', version_created_by: str = '', revision_kind: str = ''
) -> str:
    payload = _row_dict(row)
    if version_created_at:
        payload['_version_created_at'] = version_created_at
    if version_created_by:
        payload['_version_created_by'] = version_created_by
    if revision_kind:
        payload['_revision_kind'] = revision_kind
    return json.dumps(payload, ensure_ascii=False, separators=(',', ':'))


def _ensure_revision_snapshots(conn, quote_row: Any) -> None:
    """Backfill full snapshots for legacy commercial-only revisions."""
    base = _row_dict(quote_row)
    if not base:
        return
    revs = conn.execute(
        'SELECT * FROM accommodation_quote_revisions WHERE quote_id=? ORDER BY revision_no',
        (base.get('quote_id'),),
    ).fetchall()
    for rev in revs:
        raw = str(rev['snapshot_json'] or '') if 'snapshot_json' in rev.keys() else ''
        if raw and raw not in ('{}', 'null'):
            continue
        snap = dict(base)
        snap['revision_no'] = _ival(rev['revision_no'], 1, minimum=1)
        snap['stay_total_before_tourist_tax'] = rev['hms_base_total']
        snap['commercial_discount_percent'] = rev['discount_percent']
        snap['commercial_discount_amount'] = rev['discount_amount']
        snap['commercial_total'] = rev['commercial_total']
        snap['commercial_note'] = rev['commercial_note'] or ''
        snap['updated_at'] = rev['created_at']
        snap['updated_by'] = rev['created_by']
        snap['_version_created_at'] = rev['created_at']
        snap['_version_created_by'] = rev['created_by']
        snap['_revision_kind'] = (
            str(rev['revision_kind'] or 'commercial') if 'revision_kind' in rev.keys() else 'commercial'
        )
        conn.execute(
            'UPDATE accommodation_quote_revisions SET snapshot_json=? WHERE revision_id=?',
            (json.dumps(snap, ensure_ascii=False, separators=(',', ':')), rev['revision_id']),
        )


def _revision_quote_view(
    conn, current_row: Any, requested_revision: Any
) -> Tuple[Dict[str, Any], bool, Optional[Any]]:
    current = _row_dict(current_row)
    current_no = _ival(current.get('revision_no'), 1, minimum=1)
    requested = _ival(requested_revision, current_no, minimum=1)
    if requested == current_no:
        rev = conn.execute(
            'SELECT * FROM accommodation_quote_revisions WHERE quote_id=? AND revision_no=?',
            (current.get('quote_id'), current_no),
        ).fetchone()
        current['_version_created_at'] = (rev['created_at'] if rev else None) or current.get('updated_at') or current.get('created_at') or ''
        current['_version_created_by'] = (rev['created_by'] if rev else None) or current.get('updated_by') or current.get('created_by') or ''
        current['_revision_kind'] = (rev['revision_kind'] if rev and 'revision_kind' in rev.keys() else '') or current.get('_revision_kind') or ('initial' if current_no == 1 else 'commercial')
        return current, True, rev
    _ensure_revision_snapshots(conn, current_row)
    conn.commit()
    rev = conn.execute(
        'SELECT * FROM accommodation_quote_revisions WHERE quote_id=? AND revision_no=?',
        (current.get('quote_id'), requested),
    ).fetchone()
    if not rev:
        raise KeyError(requested)
    try:
        snap = json.loads(rev['snapshot_json'] or '{}')
    except Exception:
        snap = {}
    if not isinstance(snap, dict) or not snap:
        snap = dict(current)
        snap['revision_no'] = requested
    snap['_version_created_at'] = snap.get('_version_created_at') or rev['created_at']
    snap['_version_created_by'] = snap.get('_version_created_by') or rev['created_by']
    snap['_revision_kind'] = snap.get('_revision_kind') or (
        rev['revision_kind'] if 'revision_kind' in rev.keys() else 'commercial'
    )
    return snap, False, rev


QUOTE_DATA_COLUMNS = (
    'client_name', 'title', 'arrival', 'departure', 'nights', 'guest_count', 'placement_mode',
    'include_extra', 'strategy', 'availability_source', 'availability_fetched_at',
    'availability_json', 'allocation_json', 'available_whole_stay', 'configured_capacity',
    'placed_guests', 'shortage', 'spare_places', 'manager_note', 'guest_note', 'tariff_status',
    'adults', 'children', 'occupancy_json', 'pricing_json', 'pricing_source', 'pricing_generated_at',
    'price_list_id', 'rate_plan_id', 'include_tourist_tax', 'stay_total_before_tourist_tax',
    'tourist_tax_total', 'stay_total', 'currency', 'commercial_discount_percent',
    'commercial_discount_amount', 'commercial_total', 'commercial_note', 'guest_input_mode',
    'guest_list_json', 'guest_list_source', 'daily_plan_json',
    'early_checkin', 'late_checkout',
)


def _persist_quote_version(
    conn, data: Dict[str, Any], *, edit_quote_id: str = '', revision_kind: str = 'recalculation'
) -> Tuple[str, str, int]:
    """Insert a proposal or create the next immutable version of the same ACC number."""
    now = _now()
    actor = _actor()
    record = {k: data.get(k) for k in QUOTE_DATA_COLUMNS}
    if edit_quote_id:
        old = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (edit_quote_id,)).fetchone()
        if not old:
            raise ValueError('Пропозицію для редагування не знайдено.')
        _ensure_revision_snapshots(conn, old)
        revision_no = _ival(old['revision_no'], 1, minimum=1) + 1
        assignments = ','.join(f'{k}=?' for k in QUOTE_DATA_COLUMNS)
        values = [record[k] for k in QUOTE_DATA_COLUMNS]
        conn.execute(
            f'UPDATE accommodation_quotes SET {assignments}, revision_no=?, updated_at=?, updated_by=? WHERE quote_id=?',
            (*values, revision_no, now, actor, edit_quote_id),
        )
        _clear_hms_booking_preflight(conn, edit_quote_id)
        updated = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (edit_quote_id,)).fetchone()
        if not updated or _ival(updated['revision_no'], 0) != revision_no:
            raise RuntimeError('Нову версію пропозиції не підтверджено після запису в БД.')
        conn.execute('''
            INSERT INTO accommodation_quote_revisions(
                revision_id, quote_id, revision_no, created_at, created_by,
                hms_base_total, discount_percent, discount_amount, commercial_total, commercial_note,
                snapshot_json, revision_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()), edit_quote_id, revision_no, now, actor,
            record.get('stay_total_before_tourist_tax'), record.get('commercial_discount_percent') or 0,
            record.get('commercial_discount_amount') or 0, record.get('commercial_total'),
            record.get('commercial_note') or '',
            _quote_snapshot_json(
                updated, version_created_at=now, version_created_by=actor, revision_kind=revision_kind
            ),
            revision_kind,
        ))
        return edit_quote_id, str(old['quote_number']), revision_no

    quote_id = str(uuid.uuid4())
    quote_number = _next_quote_number(conn)
    base = {
        'quote_id': quote_id,
        'quote_number': quote_number,
        'created_at': now,
        'created_by': actor,
        'updated_at': now,
        'updated_by': actor,
        'status': 'draft',
        'revision_no': 1,
    }
    base.update(record)
    cols = tuple(base.keys())
    conn.execute(
        f"INSERT INTO accommodation_quotes({','.join(cols)}) VALUES ({','.join('?' for _ in cols)})",
        tuple(base[k] for k in cols),
    )
    inserted = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
    if not inserted:
        raise RuntimeError('Пропозицію не вдалося підтвердити після запису в БД.')
    conn.execute('''
        INSERT INTO accommodation_quote_revisions(
            revision_id, quote_id, revision_no, created_at, created_by,
            hms_base_total, discount_percent, discount_amount, commercial_total, commercial_note,
            snapshot_json, revision_kind
        ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        str(uuid.uuid4()), quote_id, now, actor,
        record.get('stay_total_before_tourist_tax'), record.get('commercial_discount_percent') or 0,
        record.get('commercial_discount_amount') or 0, record.get('commercial_total'),
        record.get('commercial_note') or '',
        _quote_snapshot_json(
            inserted, version_created_at=now, version_created_by=actor, revision_kind='initial'
        ),
        'initial',
    ))
    return quote_id, quote_number, 1

def _ensure_table_columns(conn, table: str, columns: Dict[str, str]) -> None:
    """Idempotent SQLite migration for replace-only deployments."""
    existing = {str(r[1]) for r in conn.execute(f'PRAGMA table_info({table})').fetchall()}
    for name, ddl in columns.items():
        if name not in existing:
            conn.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')


def _money_decimal(value: Any, default: str = '0') -> Decimal:
    try:
        return Decimal(str(value if value not in (None, '') else default))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _money_float(value: Any) -> float:
    return float(_money_decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))


def _money_text(value: Any) -> str:
    q = _money_decimal(value).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    text = f'{q:,.2f}'
    return text.replace(',', '\x00').replace('.', ',').replace('\x00', ' ')



def _employee_full_name(employee_id: Any) -> str:
    """Resolve the saved quote author from the Operations employee directory."""
    emp_id = str(employee_id or '').strip()
    if not emp_id:
        return 'Менеджер Riverwood'
    try:
        row = _db().execute('SELECT full_name FROM employees WHERE employee_id=?', (emp_id,)).fetchone()
        if row:
            name = str(row['full_name'] or '').strip()
            if name:
                return name
    except Exception:
        pass
    return emp_id


def _daily_rate_amount(item: Any) -> Optional[Decimal]:
    if not isinstance(item, dict):
        return None
    keys = (
        'amount', 'price', 'rate', 'value', 'daily_rate', 'room_rate',
        'base_rate', 'base_price', 'room_price', 'price_per_room',
        'accommodation_price', 'night_price', 'night_rate',
    )
    for key in keys:
        value = item.get(key)
        if isinstance(value, dict):
            for nested_key in ('amount', 'price', 'rate', 'value'):
                nested = value.get(nested_key)
                if nested not in (None, ''):
                    value = nested
                    break
        if value in (None, '') or isinstance(value, (dict, list, tuple)):
            continue
        try:
            amount = Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if amount.is_finite():
            return amount
    return None



def _restriction_int(value: Any) -> int:
    if isinstance(value, bool) or value in (None, ''):
        return 0
    if isinstance(value, (int, float, Decimal)):
        try:
            return max(0, int(value))
        except Exception:
            return 0
    m = re.search(r'\d{1,3}', str(value))
    return max(0, int(m.group(0))) if m else 0


def _restriction_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if value in (None, ''):
        return None
    text = str(value).strip().lower()
    if text in ('1', 'true', 'yes', 'y', 'on', 'closed', 'blocked', 'stop', 'stopped'):
        return True
    if text in ('0', 'false', 'no', 'n', 'off', 'open', 'allowed'):
        return False
    return None


def _extract_booking_restrictions(payload: Any, selected_nights: int) -> List[Dict[str, Any]]:
    """Normalize booking restrictions that the live HMS/SERVIO quote exposes.

    Operations does not invent weekend/min-stay rules.  It only interprets structured
    restriction fields or restriction/warning text already returned by the sidecar/HMS.
    This deliberately accepts several common field spellings so a sidecar upgrade can
    expose restrictions without another Operations schema migration.
    """
    if not isinstance(payload, dict):
        return []

    out: List[Dict[str, Any]] = []
    seen = set()
    selected_nights = max(0, _ival(selected_nights, 0))

    def add(kind: str, message: str, *, blocking: bool = False, value: int = 0, date_value: str = '', raw: Any = None) -> None:
        key = (kind, message.strip(), bool(blocking), int(value or 0), str(date_value or ''))
        if not message.strip() or key in seen:
            return
        seen.add(key)
        out.append({
            'type': kind,
            'message': message.strip(),
            'blocking': bool(blocking),
            'value': int(value or 0),
            'date': str(date_value or ''),
            'source': 'HMS/SERVIO',
        })

    min_keys = {
        'minstay', 'min_stay', 'minimumstay', 'minimum_stay', 'minnights', 'min_nights',
        'minimum_nights', 'minlos', 'min_los', 'minimum_los', 'minimum_length_of_stay',
        'min_length_of_stay', 'minimumstaynights', 'minimum_stay_nights', 'min_stay_nights',
    }
    max_keys = {
        'maxstay', 'max_stay', 'maximumstay', 'maximum_stay', 'maxnights', 'max_nights',
        'maximum_nights', 'maxlos', 'max_los', 'maximum_los', 'maximum_length_of_stay', 'max_stay_nights',
    }
    cta_keys = {'cta', 'closed_to_arrival', 'closedtoarrival', 'is_closed_to_arrival'}
    ctd_keys = {'ctd', 'closed_to_departure', 'closedtodeparture', 'is_closed_to_departure'}
    stop_keys = {'stop_sale', 'stopsale', 'stop_sell', 'stopsell', 'closed_rate', 'rate_closed', 'is_closed'}
    allowed_keys = {'bookable', 'is_bookable', 'allowed', 'is_allowed', 'can_book', 'booking_allowed', 'restriction_ok'}
    text_keys = ('restriction', 'warning', 'message', 'notice', 'alert', 'condition', 'reason')

    nodes: List[Any] = [payload]
    for name in ('booking_restrictions', 'restrictions', 'rate_restrictions', 'stay_restrictions', 'conditions', 'warnings', 'messages', 'notices', 'alerts', 'daily_rates', 'rate_rules', 'rules'):
        value = payload.get(name)
        if isinstance(value, list):
            nodes.extend(value)
        elif isinstance(value, dict):
            nodes.append(value)
        elif isinstance(value, str) and value.strip():
            nodes.append({'message': value})

    for node in nodes:
        if not isinstance(node, dict):
            continue
        date_value = str(node.get('date') or node.get('day') or node.get('restriction_date') or '')
        normalized = {re.sub(r'[^a-z0-9_]+', '', str(k).lower()): v for k, v in node.items()}
        for k, v in normalized.items():
            if k in min_keys:
                n = _restriction_int(v)
                if n > 0 and selected_nights > 0 and selected_nights < n:
                    blocking = False  # MinStay is warning-only and only shown when actually not met
                    add('min_stay', f'Мінімальний строк проживання за цим тарифом — {n} ноч. Обрано {selected_nights} ноч.', blocking=blocking, value=n, date_value=date_value, raw=v)
            elif k in max_keys:
                n = _restriction_int(v)
                if n > 0:
                    blocking = selected_nights > n
                    add('max_stay', f'Максимальний строк проживання за цим тарифом — {n} ноч. Обрано {selected_nights} ноч.', blocking=blocking, value=n, date_value=date_value, raw=v)
            elif k in cta_keys and _restriction_bool(v) is True:
                add('closed_to_arrival', 'За умовами тарифу заїзд у вибрану дату закритий.', blocking=True, date_value=date_value, raw=v)
            elif k in ctd_keys and _restriction_bool(v) is True:
                add('closed_to_departure', 'За умовами тарифу виїзд у вибрану дату закритий.', blocking=True, date_value=date_value, raw=v)
            elif k in stop_keys and _restriction_bool(v) is True:
                add('stop_sale', 'Продаж за цим тарифом для вибраних умов закритий.', blocking=True, date_value=date_value, raw=v)
            elif k in allowed_keys:
                flag = _restriction_bool(v)
                if flag is False:
                    add('not_bookable', 'Вибрані умови недоступні для бронювання.', blocking=True, date_value=date_value, raw=v)

        # Parse only text that lives in fields whose names explicitly look like a
        # restriction/warning/message.  This avoids treating arbitrary numeric text in
        # the quote as a stay rule.
        texts: List[str] = []
        for original_key, value in node.items():
            key_low = str(original_key).lower()
            if not any(marker in key_low for marker in text_keys):
                continue
            if isinstance(value, str) and value.strip():
                texts.append(value.strip())
            elif isinstance(value, list):
                texts.extend(str(x).strip() for x in value if isinstance(x, str) and str(x).strip())
        for text in texts:
            low = text.lower()
            m = re.search(r'(?:minimum\s*(?:stay|los|length\s*of\s*stay)|min\s*(?:stay|los)|minstay|min_stay)\D{0,24}(\d{1,3})', low, re.I)
            if not m:
                m = re.search(r'(?:мінімальн\w*|мін\.?|min)\s*(?:строк\w*|термін\w*|проживан\w*|ноч\w*|д\w*)\D{0,24}(\d{1,3})', low, re.I)
            if m:
                n = int(m.group(1))
                if selected_nights > 0 and selected_nights < n:
                    blocking = False  # MinStay is warning-only and only shown when actually not met
                    add('min_stay', f'Мінімальний строк проживання за цим тарифом — {n} ноч. Обрано {selected_nights} ноч.', blocking=blocking, value=n, date_value=date_value, raw=text)
            if re.search(r'closed\s*to\s*arrival|\bcta\b', low, re.I):
                add('closed_to_arrival', 'За умовами тарифу заїзд у вибрану дату закритий.', blocking=True, date_value=date_value, raw=text)
            if re.search(r'closed\s*to\s*departure|\bctd\b', low, re.I):
                add('closed_to_departure', 'За умовами тарифу виїзд у вибрану дату закритий.', blocking=True, date_value=date_value, raw=text)
            if re.search(r'stop\s*sale|stop\s*sell|закрит\w*\s*тариф|продаж\w*\s*закрит', low, re.I):
                add('stop_sale', 'Продаж за цим тарифом для вибраних умов закритий.', blocking=True, date_value=date_value, raw=text)

    # v5.303: MinStay is an explicit manager-overridable warning at Riverwood.
    # Some HMS responses also set a generic booking_allowed=false solely because MinStay
    # is not met.  Downgrade that generic flag only when MinStay is present and there is
    # no concrete hard restriction (CTA/CTD/stop-sale/max-stay) in the same response.
    has_min_stay = any(str(x.get('type') or '') == 'min_stay' for x in out)
    concrete_hard = any(
        bool(x.get('blocking')) and str(x.get('type') or '') not in ('min_stay', 'not_bookable')
        for x in out
    )
    if has_min_stay and not concrete_hard:
        for item in out:
            if str(item.get('type') or '') == 'not_bookable' and bool(item.get('blocking')):
                item['blocking'] = False
                item['message'] = 'MinStay не виконано; прорахунок дозволено як виняток за погодженням Riverwood.'
    return out


def _aggregate_booking_restrictions(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    raw = [dict(x) for x in items if isinstance(x, dict)]
    out: List[Dict[str, Any]] = []

    # For stay-length rules the strongest applicable rule is the actionable one.
    # Showing MinStay=1, MinStay=2 and the same MinStay=2 per room only creates noise.
    for kind, strongest in (('min_stay', max), ('max_stay', min)):
        group = [x for x in raw if str(x.get('type') or '') == kind and _ival(x.get('value'), 0) > 0]
        if group:
            values = [_ival(x.get('value'), 0) for x in group]
            chosen_value = strongest(values)
            candidates = [x for x in group if _ival(x.get('value'), 0) == chosen_value]
            blocking_candidates = [x for x in candidates if bool(x.get('blocking'))]
            preferred = blocking_candidates if blocking_candidates else candidates
            chosen = next((x for x in preferred if x.get('date')), None)
            chosen = dict(chosen or preferred[0])
            chosen['blocking'] = bool(blocking_candidates)
            if blocking_candidates:
                affected = []
                seen_affected = set()
                for x in blocking_candidates:
                    marker = (
                        str(x.get('category') or ''), str(x.get('room_label') or ''),
                        str(x.get('segment_arrival') or x.get('date') or ''),
                        str(x.get('segment_departure') or ''), _ival(x.get('segment_nights'), 0),
                    )
                    if marker in seen_affected:
                        continue
                    seen_affected.add(marker)
                    affected.append({
                        'category': marker[0], 'room_label': marker[1],
                        'arrival': marker[2], 'departure': marker[3], 'nights': marker[4],
                    })
                if affected:
                    chosen['affected_segments'] = affected
            categories = []
            seen_categories = set()
            for x in group:
                if _ival(x.get('value'), 0) != chosen_value:
                    continue
                cat = str(x.get('category') or '').strip()
                if cat and cat not in seen_categories:
                    seen_categories.add(cat); categories.append(cat)
            if categories:
                chosen['categories'] = categories
            out.append(chosen)

    seen = set()
    for item in raw:
        if str(item.get('type') or '') in ('min_stay', 'max_stay'):
            continue
        key = (str(item.get('type') or ''), str(item.get('message') or ''), bool(item.get('blocking')), str(item.get('date') or ''))
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out



def _extra_place_business_label(room_type_id: Any, *, paid_children: int = 0, generic: bool = False) -> str:
    if paid_children > 0:
        return 'Додаткове розміщення дитини'
    if _uses_portable_standard_bed(room_type_id):
        return 'Переносне додаткове ліжко'
    if generic:
        return 'Додаткове розміщення'
    return 'Штатне додаткове місце'


def _summarize_extra_lines(items: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    groups: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get('label') or 'Додаткове нарахування').strip()
        amount = _charge_amount(raw.get('amount')) if raw.get('amount') is not None else None
        unit_amount = _charge_amount(raw.get('unit_amount')) if raw.get('unit_amount') is not None else None
        quantity = _ival(raw.get('quantity'), 1, minimum=1)
        room_labels: List[str] = []
        if isinstance(raw.get('room_labels'), list):
            room_labels.extend(str(x).strip() for x in raw.get('room_labels') or [] if str(x).strip())
        one_room = str(raw.get('room_label') or '').strip()
        if one_room:
            room_labels.append(one_room)
        key = (label, str(unit_amount if unit_amount is not None else amount), str(raw.get('type') or ''))
        group = groups.setdefault(key, {
            'type': str(raw.get('type') or ''), 'label': label, 'quantity': 0,
            'room_labels': [], 'unit_amount': None, 'total': Decimal('0'), 'has_amount': False,
        })
        group['quantity'] += quantity
        for room in room_labels:
            if room not in group['room_labels']:
                group['room_labels'].append(room)
        if unit_amount is not None:
            group['unit_amount'] = unit_amount
        elif amount is not None and quantity > 0:
            group['unit_amount'] = (amount / Decimal(quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if amount is not None:
            group['total'] += amount
            group['has_amount'] = True
    return [{
        'type': g['type'], 'label': g['label'], 'quantity': g['quantity'],
        'room_labels': g['room_labels'],
        'unit_amount': _money_float(g['unit_amount']) if g['unit_amount'] is not None else None,
        'total': _money_float(g['total']) if g['has_amount'] else None,
    } for g in groups.values()]


def _available_room_tokens_for_period(arrival: str, departure: str) -> Tuple[set, Dict[str, str], List[str]]:
    """Return physical RoomIDs that are actually free for one adjacent-night period."""
    raw = _request_pms_live(arrival, departure)
    payload, warnings = _validate_payload(raw, arrival, departure)
    tokens = set()
    labels: Dict[str, str] = {}
    for cat in payload.get('categories') or []:
        ids = list(cat.get('room_ids') or [])
        room_labels = list(cat.get('room_labels') or [])
        for idx, room_id in enumerate(ids):
            token = str(room_id).strip()
            if not token:
                continue
            tokens.add(token)
            labels[token] = str(room_labels[idx]) if idx < len(room_labels) else token
    return tokens, labels, warnings


def _stay_time_availability_for_plans(
    *, arrival: str, departure: str, first_room_plan: List[Dict[str, Any]], last_room_plan: List[Dict[str, Any]],
    request_early_all: bool = False, request_late_all: bool = False, strict_explicit: bool = False,
) -> Dict[str, Any]:
    """Resolve early-check-in / late-check-out per physical room.

    Early check-in is possible only when the same physical room is free on the night
    immediately before arrival. Late check-out is possible only when the same physical
    room is free on the night immediately after departure. Explicit manual selections are
    rejected when the adjacent availability does not allow the service. A global
    "for all available rooms" request is safely reduced to only the rooms that pass the
    live adjacent-night check.
    """
    a, d, _ = _parse_dates(arrival, departure)
    out: Dict[str, Any] = {
        'early_room_labels': [], 'late_room_labels': [],
        'early_room_ids': [], 'late_room_ids': [],
        'early_unavailable_labels': [], 'late_unavailable_labels': [],
        'early_checked_period': '', 'late_checked_period': '',
        'early_requested_count': 0, 'late_requested_count': 0,
        'early_available_count': 0, 'late_available_count': 0,
        'warnings': [],
    }

    def resolve(kind: str, plan: List[Dict[str, Any]], request_all: bool, before: bool) -> None:
        flag = 'early_checkin' if kind == 'early' else 'late_checkout'
        explicit = any(flag in room for room in plan or [])
        if not plan or (not request_all and not explicit):
            return
        if before:
            start = (a - timedelta(days=1)).isoformat()
            end = a.isoformat()
        else:
            start = d.isoformat()
            end = (d + timedelta(days=1)).isoformat()
        available, _labels, warnings = _available_room_tokens_for_period(start, end)
        out['warnings'].extend(warnings)
        out[f'{kind}_checked_period'] = f'{start} → {end}'

        requested: List[Dict[str, Any]] = []
        for room in plan or []:
            if explicit:
                if bool(room.get(flag)):
                    requested.append(room)
            elif request_all:
                requested.append(room)
        out[f'{kind}_requested_count'] = len(requested)

        selected: List[Dict[str, Any]] = []
        unavailable: List[Dict[str, Any]] = []
        requested_tokens = {str(r.get('room_id') or '').strip() for r in requested}
        for room in plan or []:
            token = str(room.get('room_id') or '').strip()
            is_requested = token in requested_tokens and bool(token)
            is_available = bool(token and token in available)
            # Persist an explicit per-room state so future revisions do not have to
            # infer it from the old quote-level boolean.
            room[flag] = bool(is_requested and is_available)
            room[f'{flag}_available'] = is_available
            if is_requested:
                if is_available:
                    selected.append(room)
                else:
                    unavailable.append(room)

        if strict_explicit and explicit and unavailable:
            names = ', '.join(str(r.get('room_label') or r.get('room_id') or '?') for r in unavailable[:12])
            service = 'Ранній заїзд' if kind == 'early' else 'Пізній виїзд'
            when = 'попередню ніч' if kind == 'early' else 'наступну ніч'
            raise ValueError(
                f'{service} неможливий для номерів {names}: ці фізичні номери зайняті у {when} '
                f'({start} → {end}). Виберіть інші номери або вимкніть послугу для них.'
            )

        out[f'{kind}_room_ids'] = [r.get('room_id') for r in selected]
        out[f'{kind}_room_labels'] = [str(r.get('room_label') or r.get('room_id') or '') for r in selected]
        out[f'{kind}_unavailable_labels'] = [str(r.get('room_label') or r.get('room_id') or '') for r in unavailable]
        out[f'{kind}_available_count'] = len(selected)

    resolve('early', first_room_plan or [], bool(request_early_all), True)
    resolve('late', last_room_plan or [], bool(request_late_all), False)
    return out


def _apply_stay_time_surcharges(
    pricing: Dict[str, Any], statement: Dict[str, Any], *,
    early_checkin: bool = False, late_checkout: bool = False,
    early_room_labels: Optional[Iterable[str]] = None, late_room_labels: Optional[Iterable[str]] = None,
    availability_meta: Optional[Dict[str, Any]] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Add 50% early/late service only for eligible physical rooms."""
    if not isinstance(pricing, dict) or not isinstance(statement, dict):
        return pricing, statement
    days = statement.get('days') if isinstance(statement.get('days'), list) else []
    early_total = Decimal('0')
    late_total = Decimal('0')
    early_set = None if early_room_labels is None else {str(x) for x in early_room_labels if str(x).strip()}
    late_set = None if late_room_labels is None else {str(x) for x in late_room_labels if str(x).strip()}

    def apply_to_day(day: Dict[str, Any], kind: str, percent: Decimal, selected: Optional[set]) -> Decimal:
        subtotal = Decimal('0')
        label = 'Ранній заїзд' if kind == 'early_checkin' else 'Пізній виїзд'
        for line in day.get('lines') or []:
            if not isinstance(line, dict):
                continue
            rooms = _ival(line.get('rooms'), 0, minimum=0)
            rate = _money_decimal(line.get('room_rate'))
            if rate <= 0 or rooms <= 0:
                continue
            line_labels = [str(x) for x in (line.get('room_labels') or []) if str(x).strip()]
            if selected is None:
                matched_labels = list(line_labels)
                count = rooms
            else:
                matched_labels = [x for x in line_labels if x in selected]
                count = len(matched_labels)
            if count <= 0:
                continue
            unit = (rate * percent / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            amount = (unit * Decimal(count)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            line.setdefault('extras', []).append({
                'type': kind, 'label': f'{label} · {percent:.0f}% тарифу',
                'date': str(day.get('date') or ''), 'amount': _money_float(amount),
                'unit_amount': _money_float(unit), 'quantity': count,
                'room_labels': matched_labels, 'room_label': '',
            })
            line['extra_total'] = _money_float(_money_decimal(line.get('extra_total')) + amount)
            line['total'] = _money_float(_money_decimal(line.get('total')) + amount)
            line['extra_summary'] = _summarize_extra_lines(line.get('extras') or [])
            subtotal += amount
        day['extra_total'] = _money_float(_money_decimal(day.get('extra_total')) + subtotal)
        day['total'] = _money_float(_money_decimal(day.get('total')) + subtotal)
        return subtotal

    if days and early_checkin:
        early_total = apply_to_day(days[0], 'early_checkin', DEFAULT_EARLY_CHECKIN_PERCENT, early_set)
    if days and late_checkout:
        late_total = apply_to_day(days[-1], 'late_checkout', DEFAULT_LATE_CHECKOUT_PERCENT, late_set)

    surcharge_total = early_total + late_total
    source_before = _money_decimal(pricing.get('stay_total_before_tourist_tax'))
    source_total = _money_decimal(pricing.get('stay_total'))
    adjusted_before = (source_before + surcharge_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    adjusted_total = (source_total + surcharge_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    pricing['source_stay_total_before_tourist_tax'] = _money_float(source_before)
    pricing['source_stay_total'] = _money_float(source_total)
    pricing['early_checkin'] = bool(early_checkin and (early_set is None or len(early_set) > 0))
    pricing['late_checkout'] = bool(late_checkout and (late_set is None or len(late_set) > 0))
    pricing['early_checkin_requested'] = bool(early_checkin)
    pricing['late_checkout_requested'] = bool(late_checkout)
    pricing['early_checkin_percent'] = _money_float(DEFAULT_EARLY_CHECKIN_PERCENT)
    pricing['late_checkout_percent'] = _money_float(DEFAULT_LATE_CHECKOUT_PERCENT)
    pricing['early_checkin_total'] = _money_float(early_total)
    pricing['late_checkout_total'] = _money_float(late_total)
    pricing['arrival_departure_surcharge_total'] = _money_float(surcharge_total)
    pricing['commercial_accommodation_total'] = _money_float(adjusted_before)
    pricing['stay_total_before_tourist_tax'] = _money_float(adjusted_before)
    pricing['stay_total'] = _money_float(adjusted_total)
    meta = dict(availability_meta or {})
    pricing['early_checkin_availability'] = {
        'checked_period': str(meta.get('early_checked_period') or ''),
        'requested_count': _ival(meta.get('early_requested_count'), len(early_set or []), minimum=0),
        'available_count': _ival(meta.get('early_available_count'), len(early_set or []), minimum=0),
        'room_labels': list(meta.get('early_room_labels') or list(early_set or [])),
        'unavailable_room_labels': list(meta.get('early_unavailable_labels') or []),
    }
    pricing['late_checkout_availability'] = {
        'checked_period': str(meta.get('late_checked_period') or ''),
        'requested_count': _ival(meta.get('late_requested_count'), len(late_set or []), minimum=0),
        'available_count': _ival(meta.get('late_available_count'), len(late_set or []), minimum=0),
        'room_labels': list(meta.get('late_room_labels') or list(late_set or [])),
        'unavailable_room_labels': list(meta.get('late_unavailable_labels') or []),
    }

    # v5.303: an explicitly requested early check-in / late check-out is part of the
    # requested scenario.  If even one requested physical room is unavailable on the
    # adjacent night, the overall result must be NOT FIT immediately, before Save.
    restrictions = [dict(x) for x in (pricing.get('booking_restrictions') or []) if isinstance(x, dict)]

    def add_stay_time_block(kind: str, requested: bool) -> None:
        if not requested:
            return
        info = pricing.get('early_checkin_availability') if kind == 'early' else pricing.get('late_checkout_availability')
        if not isinstance(info, dict):
            return
        requested_count = _ival(info.get('requested_count'), 0, minimum=0)
        available_count = _ival(info.get('available_count'), 0, minimum=0)
        unavailable = [str(x).strip() for x in (info.get('unavailable_room_labels') or []) if str(x).strip()]
        if requested_count <= 0:
            return
        if available_count >= requested_count and not unavailable:
            return
        service = 'Ранній заїзд' if kind == 'early' else 'Пізній виїзд'
        period = str(info.get('checked_period') or '')
        names = ', '.join(unavailable[:30]) or 'частина вибраних номерів'
        restrictions.append({
            'type': 'early_checkin_unavailable' if kind == 'early' else 'late_checkout_unavailable',
            'message': f'{service} неможливий для номерів {names}: фізичні номери зайняті у суміжну ніч' + (f' ({period}).' if period else '.'),
            'blocking': True,
            'date': period.split(' → ')[0] if period else '',
            'source': 'adjacent_night_availability',
        })

    add_stay_time_block('early', bool(early_checkin))
    add_stay_time_block('late', bool(late_checkout))
    restrictions = _aggregate_booking_restrictions(restrictions)
    pricing['booking_restrictions'] = restrictions
    pricing['booking_allowed'] = not any(bool(x.get('blocking')) for x in restrictions)

    pricing['daily_statement'] = statement
    return pricing, statement

def _pricing_category_breakdown(pricing: Any, nights: int = 0) -> List[Dict[str, Any]]:
    """Human-readable HMS financial breakdown by room category / occupancy price.

    No price is reconstructed from Operations rules. All displayed money comes from the
    saved/live HMS quote response. When daily rates are uniform, the UI can show the
    familiar `rooms x nights x rate` formula. If HMS changes the rate by date or occupancy,
    separate price lines are kept instead of averaging them into a misleading tariff.
    """
    if not isinstance(pricing, dict):
        return []
    rooms = pricing.get('rooms')
    if not isinstance(rooms, list):
        return []
    currency = str(pricing.get('currency') or 'UAH').strip() or 'UAH'
    categories: Dict[Tuple[int, str], Dict[str, Any]] = {}

    for pr in rooms:
        if not isinstance(pr, dict):
            continue
        response = pr.get('response') if isinstance(pr.get('response'), dict) else {}
        request_payload = pr.get('request') if isinstance(pr.get('request'), dict) else {}
        rid = _ival(pr.get('room_type_id'), _ival(response.get('room_type_id'), 0))
        category = str(pr.get('category') or response.get('category') or ROOM_TYPE_NAMES.get(rid) or f'RoomTypeID {rid}')
        occupants = _ival(request_payload.get('adults'), 0, minimum=0) + _ival(request_payload.get('children'), 0, minimum=0)
        extra_beds = _ival(request_payload.get('extra_beds'), 0, minimum=0)
        daily_raw = response.get('daily_rates') if isinstance(response.get('daily_rates'), list) else []
        daily_rates: List[Dict[str, Any]] = []
        schedule_key: List[Tuple[str, str]] = []
        for idx, item in enumerate(daily_raw):
            amount = _daily_rate_amount(item)
            day = str(item.get('date') or item.get('day') or '') if isinstance(item, dict) else ''
            if amount is None:
                continue
            q = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            daily_rates.append({'date': day, 'amount': _money_float(q)})
            schedule_key.append((day or str(idx), str(q)))

        base_total = _money_decimal(response.get('base_accommodation_total', response.get('base_stay_total', 0)))
        room_total = _money_decimal(pr.get('stay_total_before_tourist_tax', response.get('stay_total_before_tourist_tax', 0)))
        extra_total = room_total - base_total
        occ_label = f'{occupants} гост./номер' if occupants else 'за номер'
        if extra_beds:
            occ_label += f' · дод. місць {extra_beds}'
        key = (rid, category)
        cat = categories.setdefault(key, {
            'room_type_id': rid,
            'category': category,
            'rooms': 0,
            'guests': 0,
            'room_labels': [],
            'total': Decimal('0'),
            'base_total': Decimal('0'),
            'extra_total': Decimal('0'),
            'currency': currency,
            '_lines': {},
        })
        cat['rooms'] += 1
        cat['guests'] += occupants
        label = str(pr.get('room_label') or pr.get('room_id') or '').strip()
        if label:
            cat['room_labels'].append(label)
        cat['total'] += room_total
        cat['base_total'] += base_total
        cat['extra_total'] += extra_total

        line_key = (tuple(schedule_key), occ_label, str(extra_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)))
        line = cat['_lines'].setdefault(line_key, {
            'rooms': 0,
            'guests': 0,
            'occupancy_label': occ_label,
            'daily_rates': daily_rates,
            'base_total': Decimal('0'),
            'extra_total': Decimal('0'),
            'total': Decimal('0'),
        })
        line['rooms'] += 1
        line['guests'] += occupants
        line['base_total'] += base_total
        line['extra_total'] += extra_total
        line['total'] += room_total

    out: List[Dict[str, Any]] = []
    for cat in categories.values():
        lines_out: List[Dict[str, Any]] = []
        for line in cat.pop('_lines').values():
            rates = line['daily_rates']
            amounts = [Decimal(str(x['amount'])) for x in rates]
            uniform = bool(amounts) and all(x == amounts[0] for x in amounts)
            line_nights = len(rates) or max(0, _ival(nights, 0))
            uniform_rate: Optional[Decimal] = amounts[0] if uniform else None
            rate_source = 'daily_rates' if uniform_rate is not None else ''
            # Exact fallback for a one-night quote: base_accommodation_total in every
            # room response is the HMS base price for that one physical room.  Dividing
            # the grouped base total by the number of identical rooms is therefore an
            # exact room/night rate, not an ADR or locally reconstructed tariff.
            if uniform_rate is None and not rates and line_nights == 1 and line['rooms'] > 0:
                uniform_rate = (line['base_total'] / Decimal(line['rooms'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                rate_source = 'one_night_hms_base_total'
            per_room_stay_total = None
            if line['rooms'] > 0:
                per_room_stay_total = (line['base_total'] / Decimal(line['rooms'])).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            line_out = {
                'rooms': line['rooms'],
                'guests': line['guests'],
                'occupancy_label': line['occupancy_label'],
                'daily_rates': rates,
                'nights': line_nights,
                'uniform_rate': _money_float(uniform_rate) if uniform_rate is not None else None,
                'rate_source': rate_source,
                'per_room_stay_total': _money_float(per_room_stay_total) if per_room_stay_total is not None else None,
                'base_total': _money_float(line['base_total']),
                'extra_total': _money_float(line['extra_total']),
                'total': _money_float(line['total']),
            }
            lines_out.append(line_out)
        cat['lines'] = lines_out
        cat['total'] = _money_float(cat['total'])
        cat['base_total'] = _money_float(cat['base_total'])
        cat['extra_total'] = _money_float(cat['extra_total'])
        out.append(cat)
    return out



def _charge_amount(item: Any) -> Optional[Decimal]:
    """Read a money amount from one explicit extra/service charge line."""
    if isinstance(item, (int, float, Decimal)):
        value = item
    elif isinstance(item, str):
        value = item
    elif isinstance(item, dict):
        value = None
        for key in ('amount', 'total', 'price', 'value', 'charge_amount', 'line_total', 'total_amount', 'sum'):
            candidate = item.get(key)
            if candidate in (None, '') or isinstance(candidate, (dict, list, tuple)):
                continue
            value = candidate
            break
        if value is None:
            return None
    else:
        return None
    try:
        out = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if out.is_finite() else None


def _charge_date(item: Any) -> str:
    if not isinstance(item, dict):
        return ''
    for key in ('date', 'day', 'service_date', 'charge_date', 'stay_date', 'night'):
        value = item.get(key)
        if value not in (None, ''):
            return str(value)[:10]
    return ''


def _charge_label(item: Any, fallback: str) -> str:
    if not isinstance(item, dict):
        return fallback
    for key in ('name', 'label', 'service_name', 'title', 'description', 'charge_name', 'service'):
        value = item.get(key)
        if value not in (None, '') and not isinstance(value, (dict, list, tuple)):
            text = str(value).strip()
            if text:
                return text
    return fallback


def _room_extra_lines(pr: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalize room-level extra charges to clear Riverwood business language."""
    response = pr.get('response') if isinstance(pr.get('response'), dict) else {}
    req = pr.get('request') if isinstance(pr.get('request'), dict) else {}
    room_label = str(pr.get('room_label') or pr.get('room_id') or '').strip()
    rid = _ival(pr.get('room_type_id'), _ival(response.get('room_type_id'), 0))
    extra_beds = _ival(req.get('extra_beds'), 0, minimum=0)
    paid_children = _ival(req.get('paid_children'), 0, minimum=0)
    groups = (
        ('extra_bed_charges', _extra_place_business_label(rid)),
        ('extra_guest_charges', _extra_place_business_label(rid, paid_children=paid_children, generic=not extra_beds)),
        ('service_charges', 'Додаткова послуга'),
        ('mandatory_charges', 'Обов’язкова послуга'),
        ('other_mandatory_charges', 'Обов’язкова послуга'),
    )
    out: List[Dict[str, Any]] = []
    seen = set()
    for field, fallback in groups:
        raw = response.get(field)
        if not isinstance(raw, list):
            continue
        for item in raw:
            amount = _charge_amount(item)
            label = fallback if field in ('extra_bed_charges', 'extra_guest_charges') else _charge_label(item, fallback)
            day = _charge_date(item)
            ids = ''
            if isinstance(item, dict):
                ids = '|'.join(str(item.get(k) or '') for k in ('service_id','price_list_services_id','conditional_id','id'))
            key = (field, label, day, str(amount) if amount is not None else '', ids)
            if key in seen:
                continue
            seen.add(key)
            out.append({'type': field, 'label': label, 'date': day,
                        'amount': _money_float(amount) if amount is not None else None,
                        'room_label': room_label})

    # If the live response already contains a priced extra-guest/extra-bed line, that line
    # is the placement charge. Do not add a second unpriced marker for the same bed/place.
    priced_placement_exists = any(x.get('type') in ('extra_bed_charges', 'extra_guest_charges') for x in out)
    if extra_beds and not priced_placement_exists:
        out.append({'type': 'extra_bed_placement',
                    'label': _extra_place_business_label(rid, paid_children=paid_children),
                    'date': '', 'amount': None, 'room_label': room_label})

    base = _money_decimal(response.get('base_accommodation_total', response.get('base_stay_total', 0)))
    room_total = _money_decimal(pr.get('stay_total_before_tourist_tax', response.get('stay_total_before_tourist_tax', 0)))
    expected_extra = (room_total - base).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    known_extra = sum((_money_decimal(x.get('amount')) for x in out if x.get('amount') is not None), Decimal('0'))
    remainder = (expected_extra - known_extra).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if remainder > Decimal('0.00'):
        out.append({'type': 'other_extra_charge', 'label': 'Інше додаткове нарахування',
                    'date': '', 'amount': _money_float(remainder), 'room_label': room_label})
    return out

def _pricing_daily_statement(pricing: Any, arrival: str, departure: str) -> Dict[str, Any]:
    """Build a guest-facing night-by-night accommodation statement.

    Every displayed room rate comes from the saved/live quote daily_rates. No ADR,
    averaging or local weekday/weekend formulas are used. Undated service/extra lines are
    kept separately for the full stay instead of being arbitrarily assigned to a night.
    """
    if isinstance(pricing, dict):
        override = pricing.get('daily_statement')
        if isinstance(override, dict) and isinstance(override.get('days'), list) and override.get('days'):
            return override
    if not isinstance(pricing, dict):
        return {'days': [], 'period_extras': [], 'has_daily_rates': False, 'currency': 'UAH'}
    try:
        a, d, nights = _parse_dates(arrival, departure)
    except Exception:
        return {'days': [], 'period_extras': [], 'has_daily_rates': False, 'currency': str(pricing.get('currency') or 'UAH')}
    stay_dates = [(a + timedelta(days=i)).isoformat() for i in range(nights)]
    date_to_idx = {x: i for i, x in enumerate(stay_dates)}
    currency = str(pricing.get('currency') or 'UAH').strip() or 'UAH'
    days: List[Dict[str, Any]] = []
    for i, day in enumerate(stay_dates):
        next_day = (a + timedelta(days=i+1)).isoformat()
        days.append({
            'date': day,
            'next_date': next_day,
            'date_label': (a + timedelta(days=i)).strftime('%d.%m.%Y'),
            'next_date_label': (a + timedelta(days=i+1)).strftime('%d.%m.%Y'),
            'lines': [],
            '_line_map': {},
            'base_total': Decimal('0'),
            'extra_total': Decimal('0'),
            'total': Decimal('0'),
        })
    period_extras: List[Dict[str, Any]] = []
    has_daily = False

    rooms = pricing.get('rooms') if isinstance(pricing.get('rooms'), list) else []
    for pr in rooms:
        if not isinstance(pr, dict):
            continue
        response = pr.get('response') if isinstance(pr.get('response'), dict) else {}
        req = pr.get('request') if isinstance(pr.get('request'), dict) else {}
        rid = _ival(pr.get('room_type_id'), _ival(response.get('room_type_id'), 0))
        category = str(pr.get('category') or response.get('category') or ROOM_TYPE_NAMES.get(rid) or f'Категорія {rid}')
        room_label = str(pr.get('room_label') or pr.get('room_id') or '').strip()
        guests = _ival(req.get('adults'), 0, minimum=0) + _ival(req.get('children'), 0, minimum=0)
        extra_beds = _ival(req.get('extra_beds'), 0, minimum=0)
        daily_raw = response.get('daily_rates') if isinstance(response.get('daily_rates'), list) else []

        rate_by_date: Dict[str, Decimal] = {}
        rate_by_index: Dict[int, Decimal] = {}
        for idx, item in enumerate(daily_raw):
            amount = _daily_rate_amount(item)
            if amount is None:
                continue
            q = amount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            day = ''
            if isinstance(item, dict):
                day = str(item.get('date') or item.get('day') or item.get('stay_date') or '')[:10]
            if day in date_to_idx:
                rate_by_date[day] = q
            else:
                rate_by_index[idx] = q
        if rate_by_date or rate_by_index:
            has_daily = True
        elif nights == 1:
            # Exact one-night fallback only; never average a multi-night stay.
            base = _money_decimal(response.get('base_accommodation_total', response.get('base_stay_total', 0)))
            rate_by_index[0] = base.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            has_daily = True

        for idx, day in enumerate(stay_dates):
            rate = rate_by_date.get(day, rate_by_index.get(idx))
            if rate is None:
                continue
            key = (rid, category, str(rate), guests, extra_beds)
            bucket = days[idx]
            line = bucket['_line_map'].get(key)
            if line is None:
                line = {
                    'room_type_id': rid,
                    'category': category,
                    'rooms': 0,
                    'guests': 0,
                    'room_labels': [],
                    'room_rate': _money_float(rate),
                    'base_total': Decimal('0'),
                    'extra_total': Decimal('0'),
                    'total': Decimal('0'),
                    'extras': [],
                    'extra_beds': 0,
                }
                bucket['_line_map'][key] = line
                bucket['lines'].append(line)
            line['rooms'] += 1
            line['guests'] += guests
            if room_label:
                line['room_labels'].append(room_label)
            line['base_total'] += rate
            line['extra_beds'] += extra_beds
            bucket['base_total'] += rate

        for charge in _room_extra_lines(pr):
            amount = _money_decimal(charge.get('amount')) if charge.get('amount') is not None else None
            day = str(charge.get('date') or '')[:10]
            target = None
            if day in date_to_idx:
                target = days[date_to_idx[day]]
            elif nights == 1 and not day:
                target = days[0]
            item = dict(charge)
            item['category'] = category
            if target is not None:
                # Attach to the matching category/room line where possible.
                attached = False
                for line in target['lines']:
                    if line['category'] == category and (not room_label or room_label in line['room_labels']):
                        line['extras'].append(item)
                        if amount is not None:
                            line['extra_total'] += amount
                        attached = True
                        break
                if not attached:
                    target.setdefault('extras', []).append(item)
                if amount is not None:
                    target['extra_total'] += amount
            else:
                period_extras.append(item)

    for day in days:
        for line in day['lines']:
            line['base_total'] = _money_float(line['base_total'])
            line['extra_total'] = _money_float(line['extra_total'])
            line['total'] = _money_float(_money_decimal(line['base_total']) + _money_decimal(line['extra_total']))
            line['extra_summary'] = _summarize_extra_lines(line.get('extras') or [])
        day.pop('_line_map', None)
        day['base_total'] = _money_float(day['base_total'])
        day['extra_total'] = _money_float(day['extra_total'])
        day['total'] = _money_float(_money_decimal(day['base_total']) + _money_decimal(day['extra_total']))
    return {
        'days': days if has_daily else [],
        'period_extras': period_extras,
        'has_daily_rates': has_daily,
        'currency': currency,
    }




def _daily_statement_is_complete(statement: Any, nights: int) -> bool:
    if not isinstance(statement, dict):
        return False
    days = statement.get('days')
    if not isinstance(days, list) or len(days) != nights:
        return False
    return all(isinstance(day, dict) and isinstance(day.get('lines'), list) and day.get('lines') for day in days)


def _exact_daily_statement_for_static_plan(
    *, arrival: str, departure: str, room_plan: List[Dict[str, Any]],
    price_list_id: int = 0, rate_plan_id: int = 0,
) -> Dict[str, Any]:
    """Obtain an exact night-by-night statement if the main multi-night quote omitted daily_rates.

    Each probe is still a live room quote for the exact room type and occupancy.  Its
    booking restriction flag is NOT used here; the full-stay quote remains authoritative
    for MinStay/CTA/CTD.  The probes are only a source of the actual nightly money lines.
    """
    a, _d, nights = _parse_dates(arrival, departure)
    all_days: List[Dict[str, Any]] = []
    all_period_extras: List[Dict[str, Any]] = []
    currency = 'UAH'
    for idx in range(nights):
        day = (a + timedelta(days=idx)).isoformat()
        next_day = (a + timedelta(days=idx + 1)).isoformat()
        one = _quote_room_plan(
            arrival=day, departure=next_day, room_plan=room_plan,
            price_list_id=price_list_id, rate_plan_id=rate_plan_id,
        )
        statement = _pricing_daily_statement(one, day, next_day)
        if not statement.get('days'):
            raise RuntimeError(f'Не вдалося отримати денну деталізацію вартості за {day}.')
        currency = str(statement.get('currency') or one.get('currency') or currency)
        day_item = dict(statement['days'][0])
        # Make sure labels always correspond to the requested night.
        day_item['date'] = day
        day_item['next_date'] = next_day
        day_item['date_label'] = (a + timedelta(days=idx)).strftime('%d.%m.%Y')
        day_item['next_date_label'] = (a + timedelta(days=idx + 1)).strftime('%d.%m.%Y')
        all_days.append(day_item)
        all_period_extras.extend(statement.get('period_extras') or [])
    return {'days': all_days, 'period_extras': all_period_extras, 'has_daily_rates': True, 'currency': currency}


def _day_pricing_room_map(day_result: Dict[str, Any]) -> Dict[Tuple[int, str], Dict[str, Any]]:
    out: Dict[Tuple[int, str], Dict[str, Any]] = {}
    pricing = day_result.get('pricing') if isinstance(day_result.get('pricing'), dict) else {}
    for pr in pricing.get('rooms') or []:
        if not isinstance(pr, dict):
            continue
        rid = _ival(pr.get('room_type_id'), 0)
        label = str(pr.get('room_label') or pr.get('room_id') or '').strip()
        out[(rid, label)] = pr
    return out


def _daily_booking_restrictions(day_results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-evaluate live restrictions against each physical room's actual consecutive use.

    This is what makes a changing group composition safe: if room 111 is kept for two
    consecutive nights, MinStay=2 is satisfied; if an extra room is used only on the first
    night, that room has a one-night stay and the proposal is blocked with a concrete
    room/date explanation.
    """
    usage: Dict[Tuple[int, str], List[int]] = {}
    room_maps: List[Dict[Tuple[int, str], Dict[str, Any]]] = []
    for idx, day in enumerate(day_results):
        rm = _day_pricing_room_map(day)
        room_maps.append(rm)
        for key in rm:
            usage.setdefault(key, []).append(idx)

    out: List[Dict[str, Any]] = []
    seen = set()
    for (rid, label), indices in usage.items():
        indices = sorted(set(indices))
        if not indices:
            continue
        runs: List[List[int]] = []
        current = [indices[0]]
        for idx in indices[1:]:
            if idx == current[-1] + 1:
                current.append(idx)
            else:
                runs.append(current); current = [idx]
        runs.append(current)

        for run in runs:
            start_i, end_i = run[0], run[-1]
            stay_nights = len(run)
            first_day = day_results[start_i]
            last_day = day_results[end_i]
            first_pr = room_maps[start_i].get((rid, label)) or {}
            last_pr = room_maps[end_i].get((rid, label)) or {}
            category = str(first_pr.get('category') or last_pr.get('category') or ROOM_TYPE_NAMES.get(rid) or f'Категорія {rid}')
            start_date = str(first_day.get('date') or '')
            departure = str(last_day.get('next_date') or '')

            first_resp = first_pr.get('response') if isinstance(first_pr.get('response'), dict) else {}
            last_resp = last_pr.get('response') if isinstance(last_pr.get('response'), dict) else {}
            first_restr = _extract_booking_restrictions(first_resp, stay_nights)
            last_restr = _extract_booking_restrictions(last_resp, stay_nights)

            def emit(kind: str, message: str, blocking: bool, value: int = 0):
                key = (kind, rid, label, start_date, departure, value, blocking)
                if key in seen:
                    return
                seen.add(key)
                out.append({
                    'type': kind, 'message': message, 'blocking': bool(blocking), 'value': int(value or 0),
                    'date': start_date, 'room_type_id': rid, 'category': category, 'room_label': label,
                    'segment_arrival': start_date, 'segment_departure': departure, 'segment_nights': stay_nights,
                    'source': 'booking_system',
                })

            min_values = [_ival(x.get('value'), 0) for x in first_restr if x.get('type') == 'min_stay' and _ival(x.get('value'), 0) > 0]
            if min_values:
                required = max(min_values)
                if stay_nights < required:
                    blocking = False  # MinStay is warning-only and only shown when the room segment is too short
                    emit('min_stay', f'{category} · номер {label}: для заїзду {start_date} мінімальний строк — {required} ноч.; цей номер використовується {stay_nights} ноч. ({start_date} → {departure}).', blocking, required)
            max_values = [_ival(x.get('value'), 0) for x in first_restr if x.get('type') == 'max_stay' and _ival(x.get('value'), 0) > 0]
            if max_values:
                allowed = min(max_values)
                blocking = stay_nights > allowed
                emit('max_stay', f'{category} · номер {label}: максимальний строк — {allowed} ноч.; заплановано {stay_nights} ноч. ({start_date} → {departure}).', blocking, allowed)
            if any(x.get('type') == 'closed_to_arrival' and x.get('blocking') for x in first_restr):
                emit('closed_to_arrival', f'{category} · номер {label}: заїзд {start_date} закритий умовами бронювання.', True)
            if any(x.get('type') == 'closed_to_departure' and x.get('blocking') for x in last_restr):
                emit('closed_to_departure', f'{category} · номер {label}: виїзд {departure} закритий умовами бронювання.', True)

            # A stop-sale on any night of the actual room segment is blocking.
            for idx in run:
                pr = room_maps[idx].get((rid, label)) or {}
                resp = pr.get('response') if isinstance(pr.get('response'), dict) else {}
                rr = _extract_booking_restrictions(resp, 1)
                if any(x.get('type') in ('stop_sale','not_bookable') and x.get('blocking') for x in rr):
                    d = str(day_results[idx].get('date') or '')
                    emit('stop_sale', f'{category} · номер {label}: продаж на ніч {d} → {day_results[idx].get("next_date")} закритий.', True)
                    break
    return _aggregate_booking_restrictions(out)


def _compose_daily_pricing(day_results: List[Dict[str, Any]], restrictions: List[Dict[str, Any]]) -> Dict[str, Any]:
    before = Decimal('0'); tax = Decimal('0'); total = Decimal('0'); base = Decimal('0')
    rooms: List[Dict[str, Any]] = []
    days: List[Dict[str, Any]] = []
    period_extras: List[Dict[str, Any]] = []
    currencies = set(); generated = []; sources = set(); price_lists = set(); rate_plans = set()
    for day in day_results:
        pricing = day.get('pricing') if isinstance(day.get('pricing'), dict) else {}
        statement = day.get('pricing_daily') if isinstance(day.get('pricing_daily'), dict) else {}
        before += _money_decimal(pricing.get('stay_total_before_tourist_tax'))
        tax += _money_decimal(pricing.get('tourist_tax_total'))
        total += _money_decimal(pricing.get('stay_total'))
        base += _money_decimal(pricing.get('base_accommodation_total'))
        rooms.extend(pricing.get('rooms') or [])
        if statement.get('days'):
            d = dict(statement['days'][0])
            d['adults'] = _ival(day.get('adults'), 0)
            d['children'] = _ival(day.get('children'), 0)
            d['guest_count'] = _ival(day.get('guest_count'), 0)
            d['placement_mode'] = str(day.get('placement_mode') or '')
            d['placement_label'] = str(day.get('placement_label') or '')
            d['used_rooms'] = _ival((day.get('summary') or {}).get('used_rooms'), 0)
            days.append(d)
        period_extras.extend(statement.get('period_extras') or [])
        c = str(pricing.get('currency') or '').strip()
        if c: currencies.add(c)
        if pricing.get('generated_at'): generated.append(str(pricing.get('generated_at')))
        if pricing.get('source'): sources.add(str(pricing.get('source')))
        price_lists.update(_ival(x,0) for x in pricing.get('price_list_ids') or [] if _ival(x,0)>0)
        rate_plans.update(_ival(x,0) for x in pricing.get('rate_plan_ids') or [] if _ival(x,0)>0)
    if len(currencies) > 1:
        raise RuntimeError(f'Система бронювання повернула різні валюти по днях: {sorted(currencies)}')
    currency = next(iter(currencies), 'UAH')
    booking_allowed = not any(bool(x.get('blocking')) for x in restrictions)
    return {
        'ok': True, 'daily_mode': True, 'rooms': rooms, 'room_count': len(rooms),
        'base_accommodation_total': _money_float(base),
        'stay_total_before_tourist_tax': _money_float(before),
        'tourist_tax_total': _money_float(tax), 'stay_total': _money_float(total),
        'hms_accommodation_total': _money_float(before), 'currency': currency,
        'source': ' + '.join(sorted(sources)) if sources else 'Система бронювання',
        'generated_at': max(generated) if generated else _now(),
        'price_list_ids': sorted(price_lists), 'rate_plan_ids': sorted(rate_plans),
        'booking_restrictions': restrictions, 'booking_allowed': booking_allowed,
        'daily_statement': {'days': days, 'period_extras': period_extras, 'has_daily_rates': True, 'currency': currency},
        'daily_calculations': day_results,
    }

def _percent_decimal(value: Any) -> Decimal:
    try:
        out = Decimal(str(value if value not in (None, '') else '0'))
    except (InvalidOperation, ValueError, TypeError):
        out = Decimal('0')
    if out < 0:
        out = Decimal('0')
    if out > 100:
        out = Decimal('100')
    return out.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _tourist_tax_estimate(schedule: Iterable[Dict[str, Any]], *, rate: Any = DEFAULT_TOURIST_TAX_PER_ADULT_NIGHT) -> Dict[str, Any]:
    """Informational tourist-tax estimate for the manager/client summary.

    Riverwood currently communicates 43.24 UAH per adult per night when the tax applies.
    Children are not included here. The commercial accommodation total is intentionally
    unchanged because business-trip/exemption documents can make the actual tax zero.
    """
    tax_rate = _money_decimal(rate).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    adult_nights = 0
    for day in schedule or []:
        if not isinstance(day, dict):
            continue
        adult_nights += _ival(day.get('adults'), 0, minimum=0)
    total = (tax_rate * Decimal(adult_nights)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'tourist_tax_estimate': _money_float(total),
        'tourist_tax_rate': _money_float(tax_rate),
        'tourist_tax_adult_nights': adult_nights,
    }


def _commercial_terms(base_total: Any, discount_percent: Any) -> Dict[str, float]:
    base = _money_decimal(base_total).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    pct = _percent_decimal(discount_percent)
    discount = (base * pct / Decimal('100')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    total = (base - discount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {
        'base_total': float(base),
        'discount_percent': float(pct),
        'discount_amount': float(discount),
        'commercial_total': float(total),
    }


def _guest_clean_text(value: Any, limit: int = 300) -> str:
    text = re.sub(r'\s+', ' ', str(value or '').strip())
    return text[:limit]


def _guest_norm_name(value: Any) -> str:
    return re.sub(r'[^0-9a-zа-яіїєґ]+', '', _guest_clean_text(value).lower(), flags=re.IGNORECASE)


def _normalize_guest(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    full_name = _guest_clean_text(item.get('full_name') or item.get('name'), 180)
    if not full_name:
        return None
    guest_type = str(item.get('guest_type') or item.get('type') or 'adult').strip().lower()
    if guest_type not in GUEST_TYPE_LABELS:
        guest_type = 'adult'
    paid_child = 1 if guest_type == 'child' and str(item.get('paid_child') or '').strip().lower() in ('1', 'true', 'yes', 'on', 'так') else 0
    preference = str(item.get('preference') or 'auto').strip().lower()
    if preference not in GUEST_PREFERENCE_LABELS:
        preference = 'auto'
    guest_id = _guest_clean_text(item.get('guest_id'), 64) or uuid.uuid4().hex[:16]
    return {
        'guest_id': guest_id,
        'full_name': full_name,
        'guest_type': guest_type,
        'paid_child': paid_child,
        'preference': preference,
        'roommate': _guest_clean_text(item.get('roommate'), 180),
        'note': _guest_clean_text(item.get('note'), 500),
    }


def _guest_list_from_json(raw: Any) -> List[Dict[str, Any]]:
    if raw in (None, ''):
        return []
    try:
        data = json.loads(str(raw))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in data:
        if isinstance(item, dict):
            normalized = _normalize_guest(item)
            if normalized:
                out.append(normalized)
    return out


def _guest_list_from_form() -> List[Dict[str, Any]]:
    names = request.form.getlist('guest_name')
    if not names:
        return _guest_list_from_json(request.form.get('guest_list_json'))
    types = request.form.getlist('guest_type')
    paid = request.form.getlist('guest_paid_child')
    prefs = request.form.getlist('guest_preference')
    roommates = request.form.getlist('guest_roommate')
    notes = request.form.getlist('guest_row_note')
    ids = request.form.getlist('guest_id')
    out: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        item = {
            'guest_id': ids[i] if i < len(ids) else '',
            'full_name': name,
            'guest_type': types[i] if i < len(types) else 'adult',
            'paid_child': paid[i] if i < len(paid) else '0',
            'preference': prefs[i] if i < len(prefs) else 'auto',
            'roommate': roommates[i] if i < len(roommates) else '',
            'note': notes[i] if i < len(notes) else '',
        }
        normalized = _normalize_guest(item)
        if normalized:
            out.append(normalized)
    return out


def _matrix_to_guest_list(rows: List[List[str]]) -> List[Dict[str, Any]]:
    rows = [[_guest_clean_text(v, 500) for v in row] for row in rows if any(_guest_clean_text(v) for v in row)]
    if not rows:
        return []
    header_idx = -1
    name_col = -1
    type_col = -1
    pref_col = -1
    roommate_col = -1
    note_col = -1
    paid_col = -1
    name_headers = {'піб', 'фіо', 'імя', "ім'я", 'ім’я', 'повнеімя', 'повнеім’я', 'гість', 'гостя', 'guest', 'guestname', 'name'}
    for ri, row in enumerate(rows[:20]):
        normalized = [re.sub(r'[^0-9a-zа-яіїєґ]+', '', x.lower(), flags=re.IGNORECASE) for x in row]
        for ci, cell in enumerate(normalized):
            if cell in name_headers or cell.startswith('піб') or cell.startswith('фіо'):
                header_idx, name_col = ri, ci
                break
        if name_col >= 0:
            for ci, cell in enumerate(normalized):
                if cell in ('тип', 'типгостя', 'дорослийдитина', 'adultchild'): type_col = ci
                elif 'побаж' in cell or cell in ('розміщення', 'placement'): pref_col = ci
                elif 'разом' in cell or 'сусід' in cell or 'roommate' in cell: roommate_col = ci
                elif 'приміт' in cell or 'коментар' in cell or cell == 'note': note_col = ci
                elif 'платн' in cell and 'дит' in cell: paid_col = ci
            break
    data_rows = rows[header_idx + 1:] if name_col >= 0 else rows
    if name_col < 0:
        # Headerless rooming lists are common: use the first column containing person-like text.
        name_col = 0
    out: List[Dict[str, Any]] = []
    for row in data_rows:
        name = row[name_col] if name_col < len(row) else ''
        # Skip totals, section titles and obvious numeric-only cells.
        if not name or re.fullmatch(r'[\d\s.,+-]+', name or ''):
            continue
        if _guest_norm_name(name) in ('загальна вартість', 'загальнавартість', 'знижка', 'умовискасування'):
            continue
        guest_type_raw = row[type_col] if 0 <= type_col < len(row) else 'adult'
        gt_norm = _guest_norm_name(guest_type_raw)
        guest_type = 'child' if any(x in gt_norm for x in ('дит', 'child', 'kid')) else 'adult'
        pref_raw = row[pref_col] if 0 <= pref_col < len(row) else ''
        pref_norm = _guest_norm_name(pref_raw)
        preference = 'auto'
        if 'twin' in pref_norm or 'твін' in pref_norm: preference = 'twin'
        elif 'окрем' in pref_norm and ('кімнат' in pref_norm or 'номер' in pref_norm): preference = 'separate_room'
        elif 'окрем' in pref_norm and ('ліж' in pref_norm or 'bed' in pref_norm): preference = 'separate_bed'
        paid_raw = row[paid_col] if 0 <= paid_col < len(row) else ''
        normalized = _normalize_guest({
            'full_name': name,
            'guest_type': guest_type,
            'paid_child': paid_raw,
            'preference': preference,
            'roommate': row[roommate_col] if 0 <= roommate_col < len(row) else '',
            'note': row[note_col] if 0 <= note_col < len(row) else '',
        })
        if normalized:
            out.append(normalized)
    return out


def _xlsx_matrix(data: bytes) -> List[List[str]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:
        raise ValueError('Файл Excel пошкоджений або має непідтримуваний формат. Потрібен .xlsx.') from exc
    shared: List[str] = []
    try:
        root = ET.fromstring(zf.read('xl/sharedStrings.xml'))
        ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        for si in root.findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in si.findall('.//m:t', ns)))
    except KeyError:
        pass
    try:
        sheet = ET.fromstring(zf.read('xl/worksheets/sheet1.xml'))
    except Exception as exc:
        raise ValueError('У Excel-файлі не знайдено першого аркуша.') from exc
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    rows: List[List[str]] = []
    for row in sheet.findall('.//m:sheetData/m:row', ns):
        values: Dict[int, str] = {}
        max_col = -1
        for cell in row.findall('m:c', ns):
            ref = cell.get('r') or ''
            letters = ''.join(ch for ch in ref if ch.isalpha()).upper()
            col = 0
            for ch in letters:
                col = col * 26 + (ord(ch) - 64)
            col = max(0, col - 1)
            max_col = max(max_col, col)
            typ = cell.get('t') or ''
            value = ''
            if typ == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.findall('.//m:t', ns))
            else:
                v = cell.find('m:v', ns)
                raw = v.text if v is not None and v.text is not None else ''
                if typ == 's' and raw.isdigit():
                    idx = int(raw)
                    value = shared[idx] if 0 <= idx < len(shared) else ''
                else:
                    value = raw
            values[col] = value
        if max_col >= 0:
            rows.append([values.get(i, '') for i in range(max_col + 1)])
    return rows


def _guest_list_from_upload(file_storage) -> List[Dict[str, Any]]:
    if not file_storage or not getattr(file_storage, 'filename', ''):
        return []
    filename = str(file_storage.filename or '').strip()
    ext = Path(filename).suffix.lower()
    data = file_storage.read()
    if not data:
        return []
    if len(data) > 8 * 1024 * 1024:
        raise ValueError('Файл списку гостей завеликий. Максимум 8 МБ.')
    if ext == '.xlsx':
        matrix = _xlsx_matrix(data)
    elif ext == '.csv':
        text = None
        for enc in ('utf-8-sig', 'cp1251', 'utf-8'):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if text is None:
            raise ValueError('CSV не вдалося прочитати. Збережіть його в UTF-8 або Windows-1251.')
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except Exception:
            dialect = csv.excel
            dialect.delimiter = ';'
        matrix = [list(r) for r in csv.reader(io.StringIO(text), dialect)]
    else:
        raise ValueError('Для списку гостей підтримуються тільки .xlsx та .csv.')
    guests = _matrix_to_guest_list(matrix)
    if not guests:
        raise ValueError('У файлі не знайдено ПІБ гостей. Додайте колонку «ПІБ» або список імен у першій колонці.')
    return guests


def _merge_guest_lists(base: List[Dict[str, Any]], extra: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Do not deduplicate by name: two real guests may have the same full name.
    return list(base) + list(extra)


def _guest_counts(guests: List[Dict[str, Any]]) -> Tuple[int, int, int]:
    adults = sum(1 for g in guests if g.get('guest_type') != 'child')
    children = sum(1 for g in guests if g.get('guest_type') == 'child')
    paid_children = sum(1 for g in guests if g.get('guest_type') == 'child' and _ival(g.get('paid_child'), 0) > 0)
    return adults, children, paid_children


def _assign_guest_list_to_room_plan(room_plan: List[Dict[str, Any]], guests: List[Dict[str, Any]], placement_mode: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Attach concrete people to the already selected physical rooms without changing HMS capacity rules."""
    plan = [dict(r) for r in room_plan]
    if not guests:
        return plan, []
    for room in plan:
        room['guest_ids'] = []
        room['guest_names'] = []
        room['guests'] = []
        room['_remaining'] = max(0, _ival(room.get('capacity_per_room'), 0))

    def room_rank(room: Dict[str, Any], guest: Dict[str, Any]) -> Tuple[int, int, int]:
        rid = _ival(room.get('room_type_id'), 0)
        is_twin = rid in (4, 5)
        pref = guest.get('preference') or 'auto'
        if pref in ('twin', 'separate_bed'):
            tier = 0 if is_twin else 3
        elif placement_mode == 'beds':
            tier = 0 if is_twin else (1 if rid in (2, 3) else 2)
        else:
            tier = 0
        occupied = _ival(room.get('capacity_per_room'), 0) - _ival(room.get('_remaining'), 0)
        return (tier, occupied, _ival(room.get('position'), 0))

    remaining = list(guests)
    # Pair requests are handled first when the named roommate is present and a room can take both.
    by_name = {_guest_norm_name(g.get('full_name')): g for g in remaining if _guest_norm_name(g.get('full_name'))}
    processed = set()
    units: List[List[Dict[str, Any]]] = []
    for guest in remaining:
        gid = guest.get('guest_id')
        if gid in processed:
            continue
        roommate = by_name.get(_guest_norm_name(guest.get('roommate'))) if guest.get('roommate') else None
        if roommate and roommate.get('guest_id') != gid and roommate.get('guest_id') not in processed:
            units.append([guest, roommate])
            processed.add(gid); processed.add(roommate.get('guest_id'))
        else:
            units.append([guest])
            processed.add(gid)

    unassigned: List[Dict[str, Any]] = []
    for unit in units:
        candidates = [r for r in plan if _ival(r.get('_remaining'), 0) >= len(unit)]
        assigned_count = lambda r: len(r.get('guests') or [])
        if any(g.get('preference') == 'separate_room' for g in unit):
            # One separate-room request consumes one configured bedroom/room slot.
            if len(unit) != 1:
                candidates = []
            else:
                candidates = [r for r in candidates if assigned_count(r) < _ival(r.get('room_capacity_rule'), 0)]
        if any(g.get('preference') == 'separate_bed' for g in unit):
            # A strict separate-bed request may only use a room that has enough real bed slots.
            candidates = [r for r in candidates if assigned_count(r) + len(unit) <= _ival(r.get('bed_capacity_rule'), 0)]
        if not candidates:
            unassigned.extend(unit)
            continue
        lead = unit[0]
        candidates.sort(key=lambda r: room_rank(r, lead))
        room = candidates[0]
        for guest in unit:
            room['guest_ids'].append(guest.get('guest_id'))
            room['guest_names'].append(guest.get('full_name'))
            room['guests'].append(dict(guest))
        room['_remaining'] = max(0, _ival(room.get('_remaining'), 0) - len(unit))

    # In list-driven mode, concrete adult/child counts become the room occupancy sent to HMS.
    for room in plan:
        assigned = room.get('guests') or []
        if assigned:
            room['adults'] = sum(1 for g in assigned if g.get('guest_type') != 'child')
            room['children'] = sum(1 for g in assigned if g.get('guest_type') == 'child')
            room['paid_children'] = sum(1 for g in assigned if g.get('guest_type') == 'child' and _ival(g.get('paid_child'), 0) > 0)
            room['occupants'] = room['adults'] + room['children']
            room['extra_beds'] = max(0, min(_ival(room.get('extra_capacity'), 0), room['occupants'] - _ival(room.get('base_capacity'), 0)))
        room.pop('_remaining', None)
    return plan, unassigned


def _default_quote_price_list_id() -> int:
    raw = (os.environ.get('PMS_QUOTE_DEFAULT_PRICE_LIST_ID') or '').strip()
    return _ival(raw, DEFAULT_BASE_PRICE_LIST_ID, minimum=1) if raw else DEFAULT_BASE_PRICE_LIST_ID


def _default_quote_rate_plan_id() -> int:
    raw = (os.environ.get('PMS_QUOTE_DEFAULT_RATE_PLAN_ID') or '').strip()
    return _ival(raw, 0, minimum=0) if raw else 0


def _last_verified_live_rate_plan(price_list_id: int) -> Dict[str, Any]:
    """Return the most recently *actually returned* live BAR_BB RatePlan for this price list.

    The HMS sidecar can legitimately expose more than one BAR_BB RatePlan (for example
    an older YieldPlanet plan and the current RoomsWizard plan).  When Operations sends
    only PriceListID, the sidecar correctly fails closed instead of guessing between them.

    We therefore reuse only a RatePlan that was already returned by a successful live HMS
    quote stored in this same Operations database.  This is not a local price fallback: the
    next request still goes to HMS live with that explicit RatePlanID and is rejected if HMS
    no longer accepts it.
    """
    price_list_id = _ival(price_list_id, 0, minimum=0)
    if price_list_id <= 0:
        return {}
    try:
        conn = _db()
        rows = conn.execute(
            """
            SELECT quote_number, created_at, updated_at, pricing_json
            FROM accommodation_quotes
            WHERE tariff_status='live_hms'
              AND pricing_json IS NOT NULL
              AND COALESCE(price_list_id, 0)=?
            ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
            LIMIT 30
            """,
            (price_list_id,),
        ).fetchall()
    except Exception:
        return {}

    for row in rows:
        try:
            raw = row['pricing_json'] if hasattr(row, 'keys') else row[3]
            payload = json.loads(raw or '{}')
            if not isinstance(payload, dict):
                continue
            ids = sorted({_ival(x, 0, minimum=0) for x in (payload.get('rate_plan_ids') or []) if _ival(x, 0, minimum=0) > 0})
            rooms = payload.get('rooms') if isinstance(payload.get('rooms'), list) else []
            response_ids = set()
            plan_names = set()
            list_names = set()
            for room in rooms:
                if not isinstance(room, dict):
                    continue
                resp = room.get('response') if isinstance(room.get('response'), dict) else {}
                if _ival(resp.get('price_list_id'), 0) not in (0, price_list_id):
                    continue
                rid = _ival(resp.get('rate_plan_id'), 0, minimum=0)
                if rid > 0:
                    response_ids.add(rid)
                if str(resp.get('rate_plan_name') or '').strip():
                    plan_names.add(str(resp.get('rate_plan_name')).strip())
                if str(resp.get('price_list_name') or '').strip():
                    list_names.add(str(resp.get('price_list_name')).strip())
            if response_ids:
                ids = sorted(response_ids)
            if len(ids) != 1:
                continue
            # We only promote the normal/public BAR_BB family when the live trace names it.
            # Older responses without names remain acceptable if their top-level trace is unambiguous.
            if list_names and not any('bar_bb' in name.casefold() for name in list_names):
                continue
            quote_number = row['quote_number'] if hasattr(row, 'keys') else row[0]
            verified_at = (row['updated_at'] if hasattr(row, 'keys') else row[2]) or (row['created_at'] if hasattr(row, 'keys') else row[1])
            return {
                'rate_plan_id': ids[0],
                'price_list_id': price_list_id,
                'quote_number': str(quote_number or ''),
                'verified_at': str(verified_at or ''),
                'rate_plan_name': sorted(plan_names)[0] if plan_names else '',
                'price_list_name': sorted(list_names)[0] if list_names else '',
                'source': 'latest_successful_live_quote',
            }
        except Exception:
            continue
    return {}


def _effective_quote_rate_plan_id(price_list_id: int) -> Tuple[int, Dict[str, Any]]:
    explicit = _default_quote_rate_plan_id()
    if explicit > 0:
        return explicit, {'rate_plan_id': explicit, 'source': 'env:PMS_QUOTE_DEFAULT_RATE_PLAN_ID'}
    verified = _last_verified_live_rate_plan(price_list_id)
    return _ival(verified.get('rate_plan_id'), 0, minimum=0), verified


def _pricing_error_for_manager(exc: Exception) -> str:
    text = str(exc or '').strip()
    lower = text.lower()
    if 'booking restrictions could not be verified' in lower or 'booking_restrictions_unavailable' in lower:
        return 'Не вдалося перевірити актуальні умови бронювання для вибраних дат. Розрахунок зупинено, щоб не пропустити обмеження тривалості або продажу.'
    if 'rate_plan_id or price_list_id is required' in lower:
        return 'Не вдалося визначити базовий тариф для цього розрахунку. Потрібне одноразове налаштування базового тарифу в системі бронювання.'
    if 'standard bar_bb price could not be resolved safely' in lower or 'bar_bb_rate_plan_ambiguous' in lower or 'http 409' in lower or ('rate_plan_id' in lower and 'multiple' in lower):
        return 'Не вдалося однозначно визначити актуальний базовий тариф для цих дат. Це помилка визначення ціни, а не обмеження тривалості проживання.'
    if 'timeout' in lower or 'не відповів' in lower or 'недоступ' in lower:
        return 'Система бронювання тимчасово не відповіла. Розрахунок не підмінено старою або орієнтовною ціною.'
    # Keep business validation messages, but hide internal component names from the
    # normal manager flow. Service details remain available in the collapsed trace.
    cleaned = re.sub(r'\bPMS Room Quote\b', 'Система бронювання', text, flags=re.I)
    cleaned = re.sub(r'\bPMS Sidecar\b', 'Система бронювання', cleaned, flags=re.I)
    cleaned = re.sub(r'\bHMS/SERVIO\b', 'системи бронювання', cleaned, flags=re.I)
    cleaned = re.sub(r'\blive HMS\b', 'актуальної системи бронювання', cleaned, flags=re.I)
    return cleaned


def _response_money_decimal(payload: Dict[str, Any], key: str, *, fallback_key: str = '') -> Decimal:
    """Read money from a live HMS response without silently coercing malformed data to zero."""
    value = payload.get(key)
    if value in (None, '') and fallback_key:
        value = payload.get(fallback_key)
    if value in (None, ''):
        raise RuntimeError(f'PMS Room Quote не повернув {key}; pricing result відхилено.')
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise RuntimeError(f'PMS Room Quote повернув некоректне грошове поле {key}: {value!r}') from exc
    if not amount.is_finite():
        raise RuntimeError(f'PMS Room Quote повернув нечислове грошове поле {key}: {value!r}')
    return amount


def ensure_accommodation_schema(conn=None) -> None:
    own = conn is None
    conn = conn or _db()
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS accommodation_room_type_rules (
        room_type_id INTEGER PRIMARY KEY,
        pms_category TEXT NOT NULL DEFAULT '',
        guest_label TEXT NOT NULL DEFAULT '',
        standard_capacity INTEGER NOT NULL DEFAULT 0,
        is_enabled INTEGER NOT NULL DEFAULT 1,
        room_capacity INTEGER NOT NULL DEFAULT 0,
        bed_capacity INTEGER NOT NULL DEFAULT 0,
        extra_capacity INTEGER NOT NULL DEFAULT 0,
        extra_label TEXT NOT NULL DEFAULT 'Додаткове місце / диван',
        extra_enabled INTEGER NOT NULL DEFAULT 1,
        priority INTEGER NOT NULL DEFAULT 100,
        max_capacity_override INTEGER NOT NULL DEFAULT 0,
        structure_note TEXT NOT NULL DEFAULT '',
        manager_note TEXT NOT NULL DEFAULT '',
        updated_at TEXT,
        updated_by TEXT
    );

    CREATE TABLE IF NOT EXISTS accommodation_settings (
        setting_key TEXT PRIMARY KEY,
        value_text TEXT NOT NULL DEFAULT '',
        updated_at TEXT,
        updated_by TEXT
    );

    CREATE TABLE IF NOT EXISTS accommodation_availability_cache (
        cache_id TEXT PRIMARY KEY,
        arrival TEXT NOT NULL,
        departure TEXT NOT NULL,
        nights INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        is_live INTEGER NOT NULL DEFAULT 1,
        warning TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_accommodation_cache_period
        ON accommodation_availability_cache(arrival, departure, fetched_at DESC);

    CREATE TABLE IF NOT EXISTS accommodation_quotes (
        quote_id TEXT PRIMARY KEY,
        quote_number TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        created_by TEXT,
        updated_at TEXT NOT NULL,
        updated_by TEXT,
        status TEXT NOT NULL DEFAULT 'draft',
        client_name TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '',
        arrival TEXT NOT NULL,
        departure TEXT NOT NULL,
        nights INTEGER NOT NULL,
        guest_count INTEGER NOT NULL,
        placement_mode TEXT NOT NULL,
        include_extra INTEGER NOT NULL DEFAULT 0,
        strategy TEXT NOT NULL DEFAULT 'priority',
        availability_source TEXT NOT NULL DEFAULT '',
        availability_fetched_at TEXT,
        availability_json TEXT NOT NULL,
        allocation_json TEXT NOT NULL,
        available_whole_stay INTEGER NOT NULL DEFAULT 0,
        configured_capacity INTEGER NOT NULL DEFAULT 0,
        placed_guests INTEGER NOT NULL DEFAULT 0,
        shortage INTEGER NOT NULL DEFAULT 0,
        spare_places INTEGER NOT NULL DEFAULT 0,
        manager_note TEXT NOT NULL DEFAULT '',
        guest_note TEXT NOT NULL DEFAULT '',
        tariff_status TEXT NOT NULL DEFAULT 'not_connected',
        adults INTEGER NOT NULL DEFAULT 0,
        children INTEGER NOT NULL DEFAULT 0,
        occupancy_json TEXT NOT NULL DEFAULT '{}',
        pricing_json TEXT NOT NULL DEFAULT '{}',
        pricing_source TEXT NOT NULL DEFAULT '',
        pricing_generated_at TEXT,
        price_list_id INTEGER,
        rate_plan_id INTEGER,
        include_tourist_tax INTEGER NOT NULL DEFAULT 0,
        stay_total_before_tourist_tax REAL,
        tourist_tax_total REAL,
        stay_total REAL,
        currency TEXT NOT NULL DEFAULT '',
        commercial_discount_percent REAL NOT NULL DEFAULT 0,
        commercial_discount_amount REAL NOT NULL DEFAULT 0,
        commercial_total REAL,
        commercial_note TEXT NOT NULL DEFAULT '',
        revision_no INTEGER NOT NULL DEFAULT 1,
        guest_input_mode TEXT NOT NULL DEFAULT 'count',
        guest_list_json TEXT NOT NULL DEFAULT '[]',
        guest_list_source TEXT NOT NULL DEFAULT '',
        daily_plan_json TEXT NOT NULL DEFAULT '[]',
        early_checkin INTEGER NOT NULL DEFAULT 0,
        late_checkout INTEGER NOT NULL DEFAULT 0,
        hms_booking_status TEXT NOT NULL DEFAULT '',
        hms_booking_preflight_json TEXT NOT NULL DEFAULT '{}',
        hms_booking_preflight_at TEXT,
        hms_booking_preflight_by TEXT NOT NULL DEFAULT '',
        hms_booking_quote_revision INTEGER,
        hms_booking_idempotency_key TEXT NOT NULL DEFAULT '',
        hms_booking_payload_json TEXT NOT NULL DEFAULT '{}',
        hms_booking_group_id TEXT NOT NULL DEFAULT '',
        hms_booking_created_at TEXT,
        hms_booking_created_by TEXT NOT NULL DEFAULT '',
        hms_booking_last_error TEXT NOT NULL DEFAULT '',
        hms_booking_bridge_job_id TEXT NOT NULL DEFAULT '',
        hms_booking_bridge_state TEXT NOT NULL DEFAULT '',
        hms_booking_bridge_started_at TEXT,
        hms_booking_bridge_started_by TEXT NOT NULL DEFAULT '',
        hms_booking_bridge_seen_at TEXT,
        hms_booking_bridge_group_id TEXT NOT NULL DEFAULT '',
        hms_booking_bridge_login_id TEXT NOT NULL DEFAULT '',
        hms_booking_bridge_diagnostic_json TEXT NOT NULL DEFAULT '{}',
        hms_booking_bridge_error TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_accommodation_quotes_created
        ON accommodation_quotes(created_at DESC);

    CREATE TABLE IF NOT EXISTS accommodation_quote_revisions (
        revision_id TEXT PRIMARY KEY,
        quote_id TEXT NOT NULL,
        revision_no INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        created_by TEXT,
        hms_base_total REAL,
        discount_percent REAL NOT NULL DEFAULT 0,
        discount_amount REAL NOT NULL DEFAULT 0,
        commercial_total REAL,
        commercial_note TEXT NOT NULL DEFAULT '',
        snapshot_json TEXT NOT NULL DEFAULT '{}',
        revision_kind TEXT NOT NULL DEFAULT 'commercial',
        UNIQUE(quote_id, revision_no)
    );
    CREATE INDEX IF NOT EXISTS idx_accommodation_quote_revisions
        ON accommodation_quote_revisions(quote_id, revision_no DESC);
    ''')
    _ensure_table_columns(conn, 'accommodation_room_type_rules', {
        'standard_capacity': "INTEGER NOT NULL DEFAULT 0",
    })
    _ensure_table_columns(conn, 'accommodation_quotes', {
        'adults': "INTEGER NOT NULL DEFAULT 0",
        'children': "INTEGER NOT NULL DEFAULT 0",
        'occupancy_json': "TEXT NOT NULL DEFAULT '{}'",
        'pricing_json': "TEXT NOT NULL DEFAULT '{}'",
        'pricing_source': "TEXT NOT NULL DEFAULT ''",
        'pricing_generated_at': "TEXT",
        'price_list_id': "INTEGER",
        'rate_plan_id': "INTEGER",
        'include_tourist_tax': "INTEGER NOT NULL DEFAULT 0",
        'stay_total_before_tourist_tax': "REAL",
        'tourist_tax_total': "REAL",
        'stay_total': "REAL",
        'currency': "TEXT NOT NULL DEFAULT ''",
        'commercial_discount_percent': "REAL NOT NULL DEFAULT 0",
        'commercial_discount_amount': "REAL NOT NULL DEFAULT 0",
        'commercial_total': "REAL",
        'commercial_note': "TEXT NOT NULL DEFAULT ''",
        'revision_no': "INTEGER NOT NULL DEFAULT 1",
        'guest_input_mode': "TEXT NOT NULL DEFAULT 'count'",
        'guest_list_json': "TEXT NOT NULL DEFAULT '[]'",
        'guest_list_source': "TEXT NOT NULL DEFAULT ''",
        'daily_plan_json': "TEXT NOT NULL DEFAULT '[]'",
        'early_checkin': "INTEGER NOT NULL DEFAULT 0",
        'late_checkout': "INTEGER NOT NULL DEFAULT 0",
        'hms_booking_status': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_preflight_json': "TEXT NOT NULL DEFAULT '{}'",
        'hms_booking_preflight_at': "TEXT",
        'hms_booking_preflight_by': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_quote_revision': "INTEGER",
        'hms_booking_idempotency_key': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_payload_json': "TEXT NOT NULL DEFAULT '{}'",
        'hms_booking_group_id': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_created_at': "TEXT",
        'hms_booking_created_by': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_last_error': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_bridge_job_id': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_bridge_state': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_bridge_started_at': "TEXT",
        'hms_booking_bridge_started_by': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_bridge_seen_at': "TEXT",
        'hms_booking_bridge_group_id': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_bridge_login_id': "TEXT NOT NULL DEFAULT ''",
        'hms_booking_bridge_diagnostic_json': "TEXT NOT NULL DEFAULT '{}'",
        'hms_booking_bridge_error': "TEXT NOT NULL DEFAULT ''",
    })
    _ensure_table_columns(conn, 'accommodation_quote_revisions', {
        'snapshot_json': "TEXT NOT NULL DEFAULT '{}'",
        'revision_kind': "TEXT NOT NULL DEFAULT 'commercial'",
    })

    now = _now() if _DEPS else datetime.now().isoformat(timespec='seconds')
    conn.execute(
        "INSERT INTO accommodation_settings(setting_key,value_text,updated_at,updated_by) VALUES('standard_extra_bed_pool',?,?,?) ON CONFLICT(setting_key) DO NOTHING",
        (str(DEFAULT_STANDARD_EXTRA_BED_POOL), now, 'SYSTEM_V5302'),
    )
    for room_type_id, category in ROOM_TYPE_NAMES.items():
        seed = ROOM_RULE_SEED.get(room_type_id, {})
        conn.execute('''
            INSERT INTO accommodation_room_type_rules(
                room_type_id, pms_category, guest_label, standard_capacity, is_enabled,
                room_capacity, bed_capacity, extra_capacity, extra_label,
                extra_enabled, priority, max_capacity_override,
                structure_note, manager_note, updated_at, updated_by
            ) VALUES (?, ?, ?, ?, 1, ?, ?, ?, 'Додаткове місце / диван', 1, ?, 0, ?, '', ?, 'SYSTEM_V5291')
            ON CONFLICT(room_type_id) DO NOTHING
        ''', (
            room_type_id, category, category,
            int(seed.get('standard_capacity', 0)),
            int(seed.get('room_capacity', 0)),
            int(seed.get('bed_capacity', 0)),
            int(seed.get('extra_capacity', 0)),
            room_type_id * 10,
            str(seed.get('structure_note', '')),
            now,
        ))
    # v5.294 adds an explicit ordinary-hotel occupancy mode required by the Room Quote TZ.
    # Populate it only on untouched system seed rows; manager-edited rows are never overwritten.
    for room_type_id, seed in ROOM_RULE_SEED.items():
        standard_capacity = int(seed.get('standard_capacity', 0))
        if standard_capacity > 0:
            conn.execute(
                "UPDATE accommodation_room_type_rules SET standard_capacity=? WHERE room_type_id=? AND standard_capacity=0 AND updated_by='SYSTEM_V5291'",
                (standard_capacity, room_type_id),
            )
    # HMS live control case confirms RT2 can host 3 adults using one extra bed. Fix only
    # the untouched v5.291 seed (0); later manual manager settings stay authoritative.
    conn.execute(
        "UPDATE accommodation_room_type_rules SET extra_capacity=1, structure_note=?, updated_at=?, updated_by='SYSTEM_V5294_CONTEXT_MIGRATION' "
        "WHERE room_type_id=2 AND extra_capacity=0 AND updated_by='SYSTEM_V5291'",
        (str(ROOM_RULE_SEED[2].get('structure_note') or ''), now),
    )
    # Existing live-priced quotes become revision 1 without changing the HMS source value.
    conn.execute('''
        UPDATE accommodation_quotes
        SET commercial_total=COALESCE(stay_total_before_tourist_tax, stay_total),
            commercial_discount_percent=COALESCE(commercial_discount_percent, 0),
            commercial_discount_amount=COALESCE(commercial_discount_amount, 0),
            revision_no=CASE WHEN revision_no IS NULL OR revision_no<1 THEN 1 ELSE revision_no END
        WHERE commercial_total IS NULL AND tariff_status='live_hms'
    ''')
    # RoomTypeID 13 must never participate in this module.
    conn.execute('DELETE FROM accommodation_room_type_rules WHERE room_type_id=13')
    conn.commit()
    if own:
        return


def _read_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    try:
        text = path.read_text(encoding='utf-8-sig', errors='ignore')
    except Exception:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, val = line.split('=', 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def _candidate_secret_env_paths() -> List[Tuple[Path, str]]:
    """Secret lookup order for the PMS sidecar token.

    Preferred locations remain Operations-owned ENV/.env. Because the accepted v3
    deployment places the PMS Availability Sidecar on the existing Revenue host, an
    on-host Operations installation may also reuse the already protected Revenue .env
    as a read-only fallback. The token is never copied into SQLite or rendered to UI.
    """
    project_dir = Path(_DEPS.get('project_dir') or Path.cwd())
    out: List[Tuple[Path, str]] = []

    explicit = os.environ.get('PMS_AVAILABILITY_ENV_PATH', '').strip()
    if explicit:
        out.append((Path(explicit), 'Operations explicit secret file'))

    out.extend([
        (project_dir / '.env', 'Operations project .env'),
        (project_dir / 'data' / '.env', 'Operations data .env'),
    ])

    # Optional explicit Revenue root/path for non-standard installations.
    shared_env = (os.environ.get('PMS_AVAILABILITY_SHARED_ENV_PATH') or '').strip()
    if shared_env:
        out.append((Path(shared_env), 'Shared Revenue sidecar secret file'))

    revenue_root = (os.environ.get('RIVERWOOD_REVENUE_ROOT') or '').strip()
    if revenue_root:
        out.append((Path(revenue_root) / '.env', 'Revenue sidecar .env'))

    # Accepted Riverwood Windows layout from the PMS Sidecar v3 handoff.
    out.append((Path(r'C:\riverwood_revenue_bot\.env'), 'Revenue sidecar .env'))

    # Useful when both projects live as sibling folders under one parent.
    out.append((project_dir.parent / 'riverwood_revenue_bot' / '.env', 'Revenue sibling .env'))

    unique: List[Tuple[Path, str]] = []
    seen = set()
    for item, label in out:
        key = str(item).lower()
        if key not in seen:
            unique.append((item, label))
            seen.add(key)
    return unique


def _pms_connection_info() -> Dict[str, Any]:
    # Current accepted architecture: isolated PMS Availability Sidecar on Revenue server :8082.
    url = (os.environ.get('PMS_AVAILABILITY_API_URL') or '').strip()
    if not url:
        url = 'http://127.0.0.1:8082/api/internal/pms-availability'

    token = (os.environ.get('PMS_AVAILABILITY_API_TOKEN') or os.environ.get('RIVERWOOD_PMS_AVAILABILITY_API_TOKEN') or '').strip()
    token_source = 'Operations process ENV' if token else ''
    if not token:
        for env_path, source_label in _candidate_secret_env_paths():
            vals = _read_env_file(env_path)
            candidate = (vals.get('PMS_AVAILABILITY_API_TOKEN') or vals.get('RIVERWOOD_PMS_AVAILABILITY_API_TOKEN') or '').strip()
            if candidate:
                token = candidate
                token_source = f'{source_label}: {env_path}'
                break

    # Sidecar v1 accepts both methods. X-Riverwood-Internal-Token is the safe default.
    auth_mode = (os.environ.get('PMS_AVAILABILITY_API_AUTH_MODE') or 'x-token').strip().lower()
    explicit_header = (os.environ.get('PMS_AVAILABILITY_API_HEADER') or '').strip()
    explicit_prefix = (os.environ.get('PMS_AVAILABILITY_API_AUTH_PREFIX') or '').strip()
    if explicit_header:
        header = explicit_header
        prefix = explicit_prefix
        auth_label = f'Custom header: {header}'
    elif auth_mode in ('bearer', 'authorization', 'authorization-bearer'):
        header = 'Authorization'
        prefix = 'Bearer '
        auth_label = 'Authorization: Bearer'
    else:
        header = 'X-Riverwood-Internal-Token'
        prefix = ''
        auth_label = 'X-Riverwood-Internal-Token'

    parsed = urllib.parse.urlsplit(url)
    health_url = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, '/health', '', '')) if parsed.scheme and parsed.netloc else ''
    # When no explicit URL is configured, Operations and Revenue are installed on the
    # same Riverwood Windows host in the accepted deployment. Loopback is preferred so
    # the request does not depend on Windows inbound-firewall/hairpin routing. The
    # documented LAN address remains a connection fallback.
    explicit_url = bool((os.environ.get('PMS_AVAILABILITY_API_URL') or '').strip())
    fallback_url = '' if explicit_url else 'http://192.168.89.214:8082/api/internal/pms-availability'
    try:
        request_timeout = float((os.environ.get('PMS_AVAILABILITY_REQUEST_TIMEOUT') or '8').strip())
    except Exception:
        request_timeout = 8.0
    request_timeout = max(2.0, min(request_timeout, 15.0))
    try:
        connect_timeout = float((os.environ.get('PMS_AVAILABILITY_CONNECT_TIMEOUT') or '1.5').strip())
    except Exception:
        connect_timeout = 1.5
    connect_timeout = max(0.5, min(connect_timeout, 3.0))
    return {
        'url': url,
        'fallback_url': fallback_url,
        'health_url': health_url,
        'service': 'pms_availability_sidecar',
        'architecture': 'PMS Availability Sidecar v1 / 8082',
        'request_timeout': request_timeout,
        'connect_timeout': connect_timeout,
        'token': token,
        'header': header,
        'prefix': prefix,
        'auth_label': auth_label,
        'token_configured': bool(token),
        'header_configured': bool(header),
        'token_source': token_source,
        'header_source': 'Sidecar v1 contract' if not explicit_header else 'Operations ENV override',
    }


def _replace_api_path(url: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(str(url or ''))
    if not parsed.scheme or not parsed.netloc:
        return ''
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, '', ''))


def _pms_quote_connection_info() -> Dict[str, Any]:
    """Room Quote uses the same :8082 sidecar and auth as availability."""
    availability = _pms_connection_info()
    explicit_url = (os.environ.get('PMS_ROOM_QUOTE_API_URL') or '').strip()
    url = explicit_url or _replace_api_path(availability.get('url', ''), '/api/internal/pms-room-quote')
    fallback_url = ''
    if not explicit_url and availability.get('fallback_url'):
        fallback_url = _replace_api_path(availability.get('fallback_url', ''), '/api/internal/pms-room-quote')
    try:
        request_timeout = float((os.environ.get('PMS_ROOM_QUOTE_REQUEST_TIMEOUT') or '12').strip())
    except Exception:
        request_timeout = 12.0
    request_timeout = max(2.0, min(request_timeout, 30.0))
    out = dict(availability)
    out.update({
        'url': url,
        'fallback_url': fallback_url,
        'service': 'pms_room_quote_sidecar',
        'architecture': 'HMS/SERVIO Room Quote API / 8082',
        'request_timeout': request_timeout,
    })
    return out



def _pms_timetable_connection_info() -> Dict[str, Any]:
    """Hotel timetable uses the same :8082 sidecar and internal token."""
    availability = _pms_connection_info()
    explicit_url = (os.environ.get('PMS_TIMETABLE_API_URL') or '').strip()
    url = explicit_url or _replace_api_path(availability.get('url', ''), '/api/internal/pms-timetable')
    fallback_url = ''
    if not explicit_url and availability.get('fallback_url'):
        fallback_url = _replace_api_path(availability.get('fallback_url', ''), '/api/internal/pms-timetable')
    try:
        request_timeout = float((os.environ.get('PMS_TIMETABLE_REQUEST_TIMEOUT') or '18').strip())
    except Exception:
        request_timeout = 18.0
    request_timeout = max(3.0, min(request_timeout, 35.0))
    out = dict(availability)
    out.update({
        'url': url,
        'fallback_url': fallback_url,
        'service': 'pms_timetable_sidecar',
        'architecture': 'HMS timetable adapter / 8082',
        'request_timeout': request_timeout,
    })
    return out

def _pms_booking_connection_info() -> Dict[str, Any]:
    """Dedicated HMS write service on :8085; the proven read sidecar :8082 is untouched."""
    availability = _pms_connection_info()
    explicit_url = (os.environ.get('RIVERWOOD_HMS_BOOKING_API_URL') or os.environ.get('PMS_BOOKING_API_URL') or '').strip()
    base = explicit_url or 'http://127.0.0.1:8085/api/internal/hms-booking'
    # Never route destructive booking writes into the proven read-sidecar port :8082,
    # even if a stale PMS_BOOKING_API_URL remains in the Windows environment from an
    # earlier experiment. A dedicated override may use another port/host, but not 8082.
    try:
        parsed_booking_url = urllib.parse.urlsplit(base)
        booking_host = (parsed_booking_url.hostname or '').strip().lower()
        booking_port = parsed_booking_url.port
        if booking_port == 8082 or (booking_host in ('127.0.0.1', 'localhost', '::1') and booking_port in (8083, 8084)):
            base = 'http://127.0.0.1:8085/api/internal/hms-booking'
    except Exception:
        base = 'http://127.0.0.1:8085/api/internal/hms-booking'
    try:
        request_timeout = float((os.environ.get('PMS_BOOKING_REQUEST_TIMEOUT') or '25').strip())
    except Exception:
        request_timeout = 25.0
    request_timeout = max(5.0, min(request_timeout, 45.0))
    out = dict(availability)
    out.update({
        'url': base.rstrip('/'),
        'fallback_url': '',
        'service': 'hms_booking_writer',
        'architecture': 'Dedicated HMS Booking Writer / 8085 (read sidecar :8082 untouched)',
        'request_timeout': request_timeout,
    })
    return out


def _request_hms_booking_draft(booking_payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    info = _pms_booking_connection_info()
    if not info.get('token'):
        raise RuntimeError('Не знайдено внутрішній токен sidecar для HMS booking writer.')
    key = str(booking_payload.get('idempotency_key') or '').strip()
    quote_id = str(booking_payload.get('quote_id') or '').strip()
    quote_number = str(booking_payload.get('quote_number') or '').strip()
    if not key or not quote_id:
        raise ValueError('Booking snapshot не має idempotency_key/quote_id.')
    request_payload = {
        'contract_version': 'riverwood-hms-booking-v1',
        'idempotency_key': key,
        'quote_id': quote_id,
        'quote_number': quote_number,
    }
    request_timeout = float(timeout if timeout is not None else info.get('request_timeout') or 18.0)
    connect_timeout = float(info.get('connect_timeout') or 1.5)
    bases = [str(info.get('url') or '')]
    if info.get('fallback_url') and str(info.get('fallback_url')) not in bases:
        bases.append(str(info.get('fallback_url')))
    bases = [x.rstrip('/') for x in bases if x]
    last_exc: Optional[Exception] = None
    for base in bases:
        url = base + '/draft'
        try:
            _tcp_probe(url, connect_timeout)
        except Exception as exc:
            last_exc = exc
            continue
        token_value = f"{info['prefix']}{info['token']}"
        headers = {
            info['header']: token_value,
            'Accept': 'application/json',
            'User-Agent': 'Riverwood-Operations-HMS-Booking/5.319',
        }
        try:
            raw, _ = _open_json_post(url, headers, request_payload, request_timeout)
        except RuntimeError as first_exc:
            code = getattr(first_exc, 'http_code', 0)
            if code in (401, 403) and info['header'].lower() in ('x-riverwood-internal-token', 'authorization'):
                retry_headers = ({'X-Riverwood-Internal-Token': info['token']} if info['header'].lower() == 'authorization' else {'Authorization': f"Bearer {info['token']}"})
                retry_headers.update({'Accept': 'application/json', 'User-Agent': 'Riverwood-Operations-HMS-Booking/5.319'})
                raw, _ = _open_json_post(url, retry_headers, request_payload, request_timeout)
            else:
                last_exc = first_exc
                if getattr(first_exc, 'network_kind', '') == 'connect':
                    continue
                raise
        try:
            result = json.loads(raw.decode('utf-8-sig'))
        except Exception as exc:
            raise RuntimeError('HMS booking sidecar повернув не JSON.') from exc
        if not isinstance(result, dict) or not result.get('ok'):
            detail = str(result.get('error') or '') if isinstance(result, dict) else ''
            raise RuntimeError('HMS booking sidecar не створив чернетку' + (f': {detail}' if detail else '.'))
        group_id = _ival(result.get('group_id'), 0, minimum=0)
        if group_id <= 0:
            raise RuntimeError('HMS booking sidecar повернув некоректний GroupID.')
        if str(result.get('idempotency_key') or '') != key:
            raise RuntimeError('HMS booking sidecar повернув інший idempotency_key.')
        proof = result.get('creation_proof') if isinstance(result.get('creation_proof'), dict) else {}
        if str(proof.get('kind') or '') != 'reservation_post_302' or _ival(proof.get('group_id'), 0, minimum=0) != group_id:
            raise RuntimeError('Safety guard: GroupID не підтверджений прямим HTTP 302 Location після штатного Reservation POST.')
        if bool(result.get('reservation_confirmed')) or _ival(result.get('reserve_steps_executed'), 0, minimum=0) != 0:
            raise RuntimeError('Safety guard: draft endpoint несподівано повідомив про ReserveGroup; результат відхилено.')
        return result
    if last_exc:
        raise RuntimeError(str(last_exc)) from last_exc
    raise RuntimeError('HMS booking sidecar недоступний.')


def _request_hms_booking_sidecar_action(action: str, request_payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    info = _pms_booking_connection_info()
    if not info.get('token'):
        raise RuntimeError('Не знайдено внутрішній токен sidecar для HMS booking writer.')
    action = str(action or '').strip().lstrip('/')
    if action not in ('verify-draft', 'prepare'):
        raise ValueError('Непідтримувана HMS booking дія.')
    request_timeout = float(timeout if timeout is not None else info.get('request_timeout') or 18.0)
    connect_timeout = float(info.get('connect_timeout') or 1.5)
    bases = [str(info.get('url') or '')]
    if info.get('fallback_url') and str(info.get('fallback_url')) not in bases:
        bases.append(str(info.get('fallback_url')))
    bases = [x.rstrip('/') for x in bases if x]
    last_exc: Optional[Exception] = None
    for base in bases:
        url = base + '/' + action
        try:
            _tcp_probe(url, connect_timeout)
        except Exception as exc:
            last_exc = exc
            continue
        token_value = f"{info['prefix']}{info['token']}"
        headers = {info['header']: token_value, 'Accept': 'application/json', 'User-Agent': 'Riverwood-Operations-HMS-Booking/5.319'}
        try:
            raw, _ = _open_json_post(url, headers, request_payload, request_timeout)
        except RuntimeError as first_exc:
            code = getattr(first_exc, 'http_code', 0)
            if code in (401, 403) and info['header'].lower() in ('x-riverwood-internal-token', 'authorization'):
                retry_headers = ({'X-Riverwood-Internal-Token': info['token']} if info['header'].lower() == 'authorization' else {'Authorization': f"Bearer {info['token']}"})
                retry_headers.update({'Accept': 'application/json', 'User-Agent': 'Riverwood-Operations-HMS-Booking/5.319'})
                raw, _ = _open_json_post(url, retry_headers, request_payload, request_timeout)
            else:
                last_exc = first_exc
                if getattr(first_exc, 'network_kind', '') == 'connect':
                    continue
                raise
        try:
            result = json.loads(raw.decode('utf-8-sig'))
        except Exception as exc:
            raise RuntimeError('HMS booking sidecar повернув не JSON.') from exc
        if not isinstance(result, dict) or not result.get('ok'):
            detail = str(result.get('error') or '') if isinstance(result, dict) else ''
            raise RuntimeError('HMS booking sidecar відхилив дію ' + action + (f': {detail}' if detail else '.'))
        if bool(result.get('reservation_confirmed')) or _ival(result.get('reserve_steps_executed'), 0, minimum=0) != 0:
            raise RuntimeError('Safety guard: sidecar несподівано повідомив про ReserveGroup; результат відхилено.')
        return result
    if last_exc:
        raise RuntimeError(str(last_exc)) from last_exc
    raise RuntimeError('HMS booking sidecar недоступний.')


def _request_hms_booking_verify(booking_payload: Dict[str, Any], group_id: Any, timeout: Optional[float] = None) -> Dict[str, Any]:
    key = str(booking_payload.get('idempotency_key') or '').strip()
    gid = _ival(group_id, 0, minimum=0)
    if not key or gid <= 0:
        raise ValueError('Немає idempotency_key/GroupID для перевірки HMS-чернетки.')
    result = _request_hms_booking_sidecar_action('verify-draft', {'idempotency_key': key, 'group_id': gid}, timeout)
    if _ival(result.get('group_id'), 0, minimum=0) != gid:
        raise RuntimeError('HMS sidecar перевірив інший GroupID.')
    return result


def _request_hms_booking_prepare(booking_payload: Dict[str, Any], group_id: Any, timeout: Optional[float] = None) -> Dict[str, Any]:
    key = str(booking_payload.get('idempotency_key') or '').strip()
    gid = _ival(group_id, 0, minimum=0)
    if not key or gid <= 0:
        raise ValueError('Немає idempotency_key/GroupID для підготовки HMS snapshot.')
    result = _request_hms_booking_sidecar_action('prepare', {'idempotency_key': key, 'group_id': gid, 'payload': booking_payload}, timeout)
    if bool(result.get('hms_write_executed')):
        raise RuntimeError('Safety guard: prepare endpoint не повинен змінювати HMS.')
    return result


def _request_hms_booking_reserve(booking_payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    """Execute the one-transaction HMS writer through the local sidecar.

    This is the only Operations call allowed to reach ReserveGroup. A transport timeout is
    treated as *uncertain* because the sidecar/HMS may have continued after the HTTP client
    stopped waiting; automatic retry must therefore be blocked.
    """
    info = _pms_booking_connection_info()
    if not info.get('token'):
        raise RuntimeError('Не знайдено внутрішній токен sidecar для HMS booking writer.')
    key = str(booking_payload.get('idempotency_key') or '').strip()
    if not key:
        raise ValueError('Booking snapshot не має idempotency_key.')
    try:
        default_timeout = float((os.environ.get('PMS_BOOKING_RESERVE_TIMEOUT') or '240').strip())
    except Exception:
        default_timeout = 240.0
    request_timeout = max(30.0, min(float(timeout if timeout is not None else default_timeout), 300.0))
    connect_timeout = float(info.get('connect_timeout') or 1.5)
    bases = [str(info.get('url') or '')]
    if info.get('fallback_url') and str(info.get('fallback_url')) not in bases:
        bases.append(str(info.get('fallback_url')))
    bases = [x.rstrip('/') for x in bases if x]
    last_exc: Optional[Exception] = None

    def _post_once(url: str, headers: Dict[str, str]) -> Dict[str, Any]:
        body = json.dumps({'idempotency_key': key, 'payload': booking_payload}, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        hdr = dict(headers)
        hdr.update({'Accept': 'application/json', 'Content-Type': 'application/json; charset=utf-8', 'User-Agent': 'Riverwood-Operations-HMS-Booking/5.322'})
        req = urllib.request.Request(url, data=body, method='POST', headers=hdr)
        try:
            with urllib.request.urlopen(req, timeout=request_timeout) as resp:
                raw = resp.read(8 * 1024 * 1024).decode('utf-8-sig', errors='replace')
                code = int(getattr(resp, 'status', 200) or 200)
        except urllib.error.HTTPError as exc:
            code = int(exc.code)
            try:
                raw = exc.read(8 * 1024 * 1024).decode('utf-8-sig', errors='replace')
            except Exception:
                raw = ''
            try:
                obj = json.loads(raw) if raw else {}
            except Exception:
                obj = {'ok': False, 'error': raw[:1600] or f'HTTP {code}'}
            if not isinstance(obj, dict):
                obj = {'ok': False, 'error': str(obj)}
            obj['_http_status'] = code
            if code in (401, 403):
                err = RuntimeError(str(obj.get('error') or f'HMS booking sidecar HTTP {code}'))
                setattr(err, 'http_code', code)
                setattr(err, 'booking_result', obj)
                raise err
            err = RuntimeError(str(obj.get('error') or f'HMS booking sidecar HTTP {code}'))
            setattr(err, 'http_code', code)
            setattr(err, 'booking_result', obj)
            raise err
        except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
            reason = getattr(exc, 'reason', exc)
            is_timeout = isinstance(exc, (TimeoutError, socket.timeout)) or isinstance(reason, (TimeoutError, socket.timeout))
            if is_timeout:
                result = {
                    'ok': False, 'uncertain': True, 'automatic_retry_blocked': True,
                    'reservation_confirmed': False, 'group_id': 0,
                    'error': f'HMS booking sidecar не завершив відповідь за {request_timeout:g} с. Результат бронювання невизначений; автоматичний повтор заблоковано.',
                }
                err = RuntimeError(result['error'])
                setattr(err, 'network_kind', 'timeout')
                setattr(err, 'booking_result', result)
                raise err from exc
            err = RuntimeError(f'HMS booking sidecar недоступний: {reason}')
            setattr(err, 'network_kind', 'connect')
            raise err from exc
        try:
            obj = json.loads(raw)
        except Exception as exc:
            result = {'ok': False, 'uncertain': True, 'automatic_retry_blocked': True, 'reservation_confirmed': False,
                      'error': 'HMS booking sidecar повернув не JSON після запуску booking transaction.'}
            err = RuntimeError(result['error'])
            setattr(err, 'booking_result', result)
            raise err from exc
        if not isinstance(obj, dict):
            raise RuntimeError('HMS booking sidecar повернув некоректний payload.')
        obj['_http_status'] = code
        return obj

    for base in bases:
        url = base + '/reserve'
        try:
            _tcp_probe(url, connect_timeout)
        except Exception as exc:
            last_exc = exc
            continue
        primary_headers = {info['header']: f"{info['prefix']}{info['token']}"}
        try:
            result = _post_once(url, primary_headers)
        except RuntimeError as first_exc:
            code = getattr(first_exc, 'http_code', 0)
            if code in (401, 403) and info['header'].lower() in ('x-riverwood-internal-token', 'authorization'):
                retry_headers = ({'X-Riverwood-Internal-Token': info['token']} if info['header'].lower() == 'authorization' else {'Authorization': f"Bearer {info['token']}"})
                result = _post_once(url, retry_headers)
            else:
                if getattr(first_exc, 'network_kind', '') == 'connect':
                    last_exc = first_exc
                    continue
                raise
        if not result.get('ok'):
            err = RuntimeError(str(result.get('error') or 'HMS booking sidecar відхилив транзакцію.'))
            setattr(err, 'booking_result', result)
            raise err
        if not bool(result.get('reservation_confirmed')) or _ival(result.get('reserve_steps_executed'), 0, minimum=0) != 3:
            err = RuntimeError('Safety guard: sidecar не підтвердив завершення всіх трьох ReserveGroup-кроків.')
            setattr(err, 'booking_result', result)
            raise err
        group_id = _ival(result.get('group_id'), 0, minimum=0)
        if group_id <= 0:
            err = RuntimeError('Safety guard: sidecar підтвердив бронювання без валідного HMS GroupID.')
            setattr(err, 'booking_result', result)
            raise err
        if str(result.get('idempotency_key') or '') not in ('', key):
            err = RuntimeError('Safety guard: sidecar повернув інший idempotency_key.')
            setattr(err, 'booking_result', result)
            raise err
        return result
    if last_exc:
        raise RuntimeError(str(last_exc)) from last_exc
    raise RuntimeError('HMS booking sidecar недоступний.')


def _tcp_probe(url: str, timeout: float) -> None:
    """Fail fast when the sidecar host/port is not reachable.

    A dropped firewall route used to leave the Flask request waiting on urllib's
    socket timeout and could make the whole panel appear to time out behind the
    reverse proxy. This probe bounds the connect phase before we start the API call.
    """
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    if not host:
        raise RuntimeError('Некоректна адреса PMS Sidecar: відсутній host.')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    try:
        with socket.create_connection((host, port), timeout=float(timeout)):
            return
    except (OSError, TimeoutError) as exc:
        err = RuntimeError(f'PMS Sidecar {host}:{port} недоступний по TCP: {exc}')
        setattr(err, 'network_kind', 'connect')
        raise err from exc


def _open_json_request(url: str, headers: Dict[str, str], timeout: float) -> Tuple[bytes, int]:
    req = urllib.request.Request(url, method='GET', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            return resp.read(), int(getattr(resp, 'status', 200) or 200)
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            detail = exc.read(600).decode('utf-8', errors='ignore').strip()
        except Exception:
            pass
        message = f'PMS Sidecar HTTP {exc.code}' + (f': {detail}' if detail else '')
        err = RuntimeError(message)
        setattr(err, 'http_code', int(exc.code))
        raise err from exc
    except (TimeoutError, socket.timeout) as exc:
        err = RuntimeError(f'PMS Sidecar не відповів за {float(timeout):g} с. Запит зупинено, панель продовжує працювати.')
        setattr(err, 'network_kind', 'timeout')
        raise err from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            err = RuntimeError(f'PMS Sidecar не відповів за {float(timeout):g} с. Запит зупинено, панель продовжує працювати.')
            setattr(err, 'network_kind', 'timeout')
            raise err from exc
        err = RuntimeError(f'PMS Sidecar недоступний: {reason}')
        setattr(err, 'network_kind', 'connect')
        raise err from exc


def _open_json_post(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: float) -> Tuple[bytes, int]:
    body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    req_headers = dict(headers)
    req_headers['Content-Type'] = 'application/json; charset=utf-8'
    req = urllib.request.Request(url, data=body, method='POST', headers=req_headers)
    try:
        with urllib.request.urlopen(req, timeout=float(timeout)) as resp:
            return resp.read(), int(getattr(resp, 'status', 200) or 200)
    except urllib.error.HTTPError as exc:
        detail = ''
        try:
            raw_detail = exc.read(1600).decode('utf-8', errors='ignore').strip()
            detail = raw_detail
            if raw_detail:
                try:
                    obj = json.loads(raw_detail)
                    if isinstance(obj, dict):
                        detail = str(obj.get('error') or obj.get('message') or obj.get('detail') or raw_detail)
                except Exception:
                    pass
        except Exception:
            pass
        message = f'PMS Room Quote HTTP {exc.code}' + (f': {detail}' if detail else '')
        err = RuntimeError(message)
        setattr(err, 'http_code', int(exc.code))
        raise err from exc
    except (TimeoutError, socket.timeout) as exc:
        err = RuntimeError(f'PMS Room Quote не відповів за {float(timeout):g} с. Ціна не підміняється кешем або ADR.')
        setattr(err, 'network_kind', 'timeout')
        raise err from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, 'reason', exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            err = RuntimeError(f'PMS Room Quote не відповів за {float(timeout):g} с. Ціна не підміняється кешем або ADR.')
            setattr(err, 'network_kind', 'timeout')
            raise err from exc
        err = RuntimeError(f'PMS Room Quote недоступний: {reason}')
        setattr(err, 'network_kind', 'connect')
        raise err from exc


def _request_pms_room_quote(request_payload: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
    info = _pms_quote_connection_info()
    if not info.get('token'):
        raise RuntimeError('Не знайдено PMS_AVAILABILITY_API_TOKEN. Room Quote використовує той самий секрет sidecar :8082; секрет не зберігається у прорахунку.')
    request_timeout = float(timeout if timeout is not None else info.get('request_timeout') or 12.0)
    connect_timeout = float(info.get('connect_timeout') or 1.5)
    base_urls = [str(info.get('url') or '')]
    if info.get('fallback_url') and str(info['fallback_url']) not in base_urls:
        base_urls.append(str(info['fallback_url']))
    base_urls = [u for u in base_urls if u]
    last_exc: Optional[Exception] = None
    for base_url in base_urls:
        try:
            _tcp_probe(base_url, connect_timeout)
        except Exception as exc:
            last_exc = exc
            continue
        token_value = f"{info['prefix']}{info['token']}"
        headers = {
            info['header']: token_value,
            'Accept': 'application/json',
            'User-Agent': 'Riverwood-Operations-Accommodation/5.298',
        }
        try:
            raw, _ = _open_json_post(base_url, headers, request_payload, request_timeout)
        except RuntimeError as first_exc:
            if getattr(first_exc, 'network_kind', '') == 'timeout':
                raise
            code = getattr(first_exc, 'http_code', 0)
            if code in (401, 403) and info['header'].lower() in ('x-riverwood-internal-token', 'authorization'):
                if info['header'].lower() == 'authorization':
                    retry_headers = {'X-Riverwood-Internal-Token': info['token'], 'Accept': 'application/json', 'User-Agent': 'Riverwood-Operations-Accommodation/5.298'}
                else:
                    retry_headers = {'Authorization': f"Bearer {info['token']}", 'Accept': 'application/json', 'User-Agent': 'Riverwood-Operations-Accommodation/5.298'}
                raw, _ = _open_json_post(base_url, retry_headers, request_payload, request_timeout)
            else:
                last_exc = first_exc
                if getattr(first_exc, 'network_kind', '') == 'connect':
                    continue
                raise
        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except Exception as exc:
            raise RuntimeError('PMS Room Quote повернув не JSON.') from exc
        if not isinstance(payload, dict) or not payload.get('ok'):
            detail = payload.get('error') if isinstance(payload, dict) else ''
            raise RuntimeError('PMS Room Quote повернув ok=false' + (f': {detail}' if detail else '.'))
        if str(payload.get('arrival') or '') != str(request_payload.get('arrival') or ''):
            raise RuntimeError('PMS Room Quote повернув інший arrival.')
        if str(payload.get('departure') or '') != str(request_payload.get('departure') or ''):
            raise RuntimeError('PMS Room Quote повернув інший departure.')
        if _ival(payload.get('room_type_id'), 0) != _ival(request_payload.get('room_type_id'), 0):
            raise RuntimeError('PMS Room Quote повернув інший RoomTypeID.')
        # The Operations calculator accepts only a complete, traceable HMS quote.
        # Malformed/missing money is an error; it must never turn into a local zero/fallback.
        _response_money_decimal(payload, 'stay_total')
        _response_money_decimal(payload, 'stay_total_before_tourist_tax')
        _response_money_decimal(payload, 'tourist_tax_total')
        _response_money_decimal(payload, 'base_accommodation_total', fallback_key='base_stay_total')
        currency = str(payload.get('currency') or '').strip()
        if not currency:
            raise RuntimeError('PMS Room Quote не повернув currency; pricing result відхилено.')
        nights = (date.fromisoformat(str(request_payload['departure'])) - date.fromisoformat(str(request_payload['arrival']))).days
        if payload.get('nights') not in (None, '') and _ival(payload.get('nights'), -1) != nights:
            raise RuntimeError('PMS Room Quote повернув іншу кількість ночей.')
        daily_rates = payload.get('daily_rates')
        if not isinstance(daily_rates, list) or len(daily_rates) != nights:
            raise RuntimeError(f'PMS Room Quote має повернути daily_rates для кожної ночі ({nights}); отримано {len(daily_rates) if isinstance(daily_rates, list) else "не список"}.')
        requested_price_list = _ival(request_payload.get('price_list_id'), 0)
        returned_price_list = _ival(payload.get('price_list_id'), 0)
        if requested_price_list > 0 and returned_price_list > 0 and returned_price_list != requested_price_list:
            raise RuntimeError(f'PMS Room Quote повернув інший PriceListID: {returned_price_list} замість {requested_price_list}.')
        requested_rate_plan = _ival(request_payload.get('rate_plan_id'), 0)
        returned_rate_plan = _ival(payload.get('rate_plan_id'), 0)
        if requested_rate_plan > 0 and returned_rate_plan > 0 and returned_rate_plan != requested_rate_plan:
            raise RuntimeError(f'PMS Room Quote повернув інший RatePlanID: {returned_rate_plan} замість {requested_rate_plan}.')
        payload.setdefault('_operations_quote_url', base_url)
        return payload
    if last_exc:
        raise RuntimeError(str(last_exc)) from last_exc
    raise RuntimeError('PMS Room Quote endpoint недоступний. Ціна не підміняється локальним розрахунком.')



def _request_pms_timetable(arrival: str, departure: str, *, pad_days: int = 1, timeout: Optional[float] = None) -> Dict[str, Any]:
    _parse_dates(arrival, departure)
    info = _pms_timetable_connection_info()
    if not info.get('token'):
        raise RuntimeError('Не знайдено внутрішній токен sidecar для шахматки номерів.')
    request_timeout = float(timeout if timeout is not None else info.get('request_timeout') or 18.0)
    connect_timeout = float(info.get('connect_timeout') or 1.5)
    base_urls = [str(info.get('url') or '')]
    if info.get('fallback_url') and str(info['fallback_url']) not in base_urls:
        base_urls.append(str(info['fallback_url']))
    base_urls = [u for u in base_urls if u]
    last_exc: Optional[Exception] = None
    for base_url in base_urls:
        try:
            _tcp_probe(base_url, connect_timeout)
        except Exception as exc:
            last_exc = exc
            continue
        query = urllib.parse.urlencode({'arrival': arrival, 'departure': departure, 'pad_days': _ival(pad_days, 1, minimum=0, maximum=7)})
        url = f"{base_url}{'&' if '?' in base_url else '?'}{query}"
        token_value = f"{info['prefix']}{info['token']}"
        headers = {
            info['header']: token_value,
            'Accept': 'application/json',
            'User-Agent': 'Riverwood-Operations-Timetable/5.310',
        }
        try:
            raw, _ = _open_json_request(url, headers, request_timeout)
        except RuntimeError as first_exc:
            code = getattr(first_exc, 'http_code', 0)
            if code in (401, 403) and info['header'].lower() in ('x-riverwood-internal-token', 'authorization'):
                retry_headers = ({'X-Riverwood-Internal-Token': info['token']} if info['header'].lower() == 'authorization' else {'Authorization': f"Bearer {info['token']}"})
                retry_headers.update({'Accept': 'application/json', 'User-Agent': 'Riverwood-Operations-Timetable/5.310'})
                raw, _ = _open_json_request(url, retry_headers, request_timeout)
            else:
                last_exc = first_exc
                if getattr(first_exc, 'network_kind', '') == 'connect':
                    continue
                raise
        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except Exception as exc:
            raise RuntimeError('Шахматка номерів повернула некоректні дані.') from exc
        if not isinstance(payload, dict) or not payload.get('ok'):
            detail = str(payload.get('error') or '') if isinstance(payload, dict) else ''
            raise RuntimeError('Не вдалося отримати шахматку номерів' + (f': {detail}' if detail else '.'))
        rooms = payload.get('rooms')
        occupancies = payload.get('occupancies')
        days = payload.get('days')
        if not isinstance(rooms, list) or not isinstance(occupancies, list) or not isinstance(days, list):
            raise RuntimeError('Шахматка номерів повернула неповний набір даних.')
        # Privacy boundary: Operations only accepts the normalized/redacted contract.
        forbidden = {'GuestName', 'ContactPhone', 'Balance', 'CompanyName', 'Comment', 'GuestID', 'PersReservation'}
        serialized = json.dumps(payload, ensure_ascii=False)
        if any(key in serialized for key in forbidden):
            raise RuntimeError('Шахматка відхилена: sidecar повернув персональні або фінансові поля бронювання.')
        return payload
    if last_exc:
        raise RuntimeError(str(last_exc)) from last_exc
    raise RuntimeError('Sidecar шахматки номерів недоступний.')

def _request_pms_live(arrival: str, departure: str, timeout: Optional[float] = None) -> Dict[str, Any]:
    info = _pms_connection_info()
    if not info['token']:
        raise RuntimeError('Не знайдено PMS_AVAILABILITY_API_TOKEN. Перевірено ENV/.env Operations та read-only Revenue sidecar .env на цьому сервері. Токен не показується і не зберігається в БД.')

    request_timeout = float(timeout if timeout is not None else info.get('request_timeout') or 8.0)
    connect_timeout = float(info.get('connect_timeout') or 1.5)
    base_urls = [str(info['url'])]
    if info.get('fallback_url') and str(info['fallback_url']) not in base_urls:
        base_urls.append(str(info['fallback_url']))

    last_exc: Optional[Exception] = None
    for base_url in base_urls:
        try:
            _tcp_probe(base_url, connect_timeout)
        except Exception as exc:
            last_exc = exc
            continue

        query = urllib.parse.urlencode({'arrival': arrival, 'departure': departure})
        separator = '&' if '?' in base_url else '?'
        url = f"{base_url}{separator}{query}"
        token_value = f"{info['prefix']}{info['token']}"
        headers = {
            info['header']: token_value,
            'Accept': 'application/json',
            'User-Agent': 'Riverwood-Operations-Accommodation/5.298',
        }
        try:
            raw, _ = _open_json_request(url, headers, request_timeout)
        except RuntimeError as first_exc:
            # A timeout/read failure means the sidecar accepted the connection but did
            # not finish the request. Do NOT repeat the same SQL query through the LAN
            # alias; return control to Flask/cache immediately.
            if getattr(first_exc, 'network_kind', '') == 'timeout':
                raise
            code = getattr(first_exc, 'http_code', 0)
            if code not in (401, 403) or info['header'].lower() not in ('x-riverwood-internal-token', 'authorization'):
                last_exc = first_exc
                # Only a connection-level failure may try the documented LAN alias.
                if getattr(first_exc, 'network_kind', '') == 'connect':
                    continue
                raise
            if info['header'].lower() == 'authorization':
                retry_headers = {
                    'X-Riverwood-Internal-Token': info['token'],
                    'Accept': 'application/json',
                    'User-Agent': 'Riverwood-Operations-Accommodation/5.298',
                }
            else:
                retry_headers = {
                    'Authorization': f"Bearer {info['token']}",
                    'Accept': 'application/json',
                    'User-Agent': 'Riverwood-Operations-Accommodation/5.298',
                }
            raw, _ = _open_json_request(url, retry_headers, request_timeout)

        try:
            payload = json.loads(raw.decode('utf-8-sig'))
        except Exception as exc:
            raise RuntimeError('PMS Sidecar повернув не JSON.') from exc
        if not isinstance(payload, dict) or not payload.get('ok'):
            raise RuntimeError('PMS Sidecar повернув ok=false або некоректну структуру.')
        # Record which endpoint actually answered without ever exposing the token.
        payload.setdefault('_operations_sidecar_url', base_url)
        return payload

    if last_exc:
        raise RuntimeError(str(last_exc)) from last_exc
    raise RuntimeError('PMS Sidecar недоступний.')

def _validate_payload(payload: Dict[str, Any], arrival: str, departure: str) -> Tuple[Dict[str, Any], List[str]]:
    _, _, expected_nights = _parse_dates(arrival, departure)
    warnings: List[str] = []
    if str(payload.get('arrival') or '') != arrival:
        raise ValueError('PMS Sidecar повернув іншу дату arrival, ніж була запитана.')
    if str(payload.get('departure') or '') != departure:
        raise ValueError('PMS Sidecar повернув іншу дату departure, ніж була запитана.')
    if _ival(payload.get('nights'), -1) != expected_nights:
        raise ValueError('PMS Sidecar повернув некоректну кількість ночей.')
    categories = payload.get('categories')
    if not isinstance(categories, list):
        raise ValueError('PMS Sidecar не повернув categories[].')

    clean_categories: List[Dict[str, Any]] = []
    seen_room_ids = set()
    for raw in categories:
        if not isinstance(raw, dict):
            continue
        rid = _ival(raw.get('room_type_id'), 0)
        if rid <= 0:
            continue
        if rid == 13:
            warnings.append('PMS повернув RoomTypeID 13; Operations виключила його згідно контракту.')
            continue
        room_ids = raw.get('room_ids') if isinstance(raw.get('room_ids'), list) else []
        labels = raw.get('room_labels') if isinstance(raw.get('room_labels'), list) else []
        available = _ival(raw.get('available_rooms'), len(room_ids), minimum=0)
        if room_ids and available != len(room_ids):
            warnings.append(f'RoomTypeID {rid}: available_rooms={available}, але room_ids={len(room_ids)}.')
        for room_id in room_ids:
            if room_id in seen_room_ids:
                warnings.append(f'Фізичний RoomID {room_id} повторюється у categories[].')
            seen_room_ids.add(room_id)
        clean_categories.append({
            'room_type_id': rid,
            'category': str(raw.get('category') or ROOM_TYPE_NAMES.get(rid) or f'RoomTypeID {rid}'),
            'active_rooms_whole_stay': _ival(raw.get('active_rooms_whole_stay'), 0, minimum=0),
            'occupied_rooms_any_overlap': _ival(raw.get('occupied_rooms_any_overlap'), 0, minimum=0),
            'available_rooms': available,
            'room_ids': room_ids,
            'room_labels': labels,
        })
    payload = dict(payload)
    payload['categories'] = clean_categories
    payload['available_whole_stay'] = _ival(payload.get('available_whole_stay'), len(seen_room_ids), minimum=0)
    payload['active_rooms_whole_stay'] = _ival(payload.get('active_rooms_whole_stay'), 0, minimum=0)
    payload['occupied_rooms_any_overlap'] = _ival(payload.get('occupied_rooms_any_overlap'), 0, minimum=0)
    return payload, warnings


def _sync_categories_from_payload(conn, payload: Dict[str, Any]) -> None:
    now = _now()
    for cat in payload.get('categories') or []:
        rid = _ival(cat.get('room_type_id'), 0)
        if rid <= 0 or rid == 13:
            continue
        category = str(cat.get('category') or ROOM_TYPE_NAMES.get(rid) or f'RoomTypeID {rid}')
        conn.execute('''
            INSERT INTO accommodation_room_type_rules(
                room_type_id, pms_category, guest_label, standard_capacity, is_enabled,
                room_capacity, bed_capacity, extra_capacity, extra_label,
                extra_enabled, priority, max_capacity_override,
                structure_note, manager_note, updated_at, updated_by
            ) VALUES (?, ?, ?, 0, 1, 0, 0, 0, 'Додаткове місце / диван', 1, ?, 0, '', '', ?, 'PMS_DISCOVERY')
            ON CONFLICT(room_type_id) DO UPDATE SET pms_category=excluded.pms_category
        ''', (rid, category, category, rid * 10, now))
    conn.execute('DELETE FROM accommodation_room_type_rules WHERE room_type_id=13')
    conn.commit()


def _save_cache(payload: Dict[str, Any], arrival: str, departure: str, warnings: Iterable[str], is_live: bool = True) -> str:
    cache_id = str(uuid.uuid4())
    conn = _db()
    conn.execute('''
        INSERT INTO accommodation_availability_cache(
            cache_id, arrival, departure, nights, payload_json, fetched_at, source, is_live, warning
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        cache_id, arrival, departure, _ival(payload.get('nights'), 0),
        json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
        _now(), str(payload.get('source') or 'PMS Availability Sidecar'),
        1 if is_live else 0, '\n'.join(str(x) for x in warnings if x),
    ))
    # Keep cache bounded.
    conn.execute('''DELETE FROM accommodation_availability_cache
                    WHERE cache_id IN (
                        SELECT cache_id FROM accommodation_availability_cache
                        ORDER BY fetched_at DESC LIMIT -1 OFFSET 300
                    )''')
    conn.commit()
    return cache_id


def _load_cache(cache_id: str) -> Optional[Dict[str, Any]]:
    if not cache_id:
        return None
    row = _db().execute('SELECT * FROM accommodation_availability_cache WHERE cache_id=?', (cache_id,)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row['payload_json'])
    except Exception:
        return None
    return {'row': row, 'payload': payload}


def _last_good_cache(arrival: str, departure: str) -> Optional[Dict[str, Any]]:
    row = _db().execute('''
        SELECT * FROM accommodation_availability_cache
        WHERE arrival=? AND departure=?
        ORDER BY fetched_at DESC LIMIT 1
    ''', (arrival, departure)).fetchone()
    if not row:
        return None
    try:
        payload = json.loads(row['payload_json'])
    except Exception:
        return None
    return {'row': row, 'payload': payload}


def _rules_map(conn=None) -> Dict[int, Dict[str, Any]]:
    conn = conn or _db()
    rows = conn.execute('SELECT * FROM accommodation_room_type_rules WHERE room_type_id<>13 ORDER BY priority, room_type_id').fetchall()
    return {int(r['room_type_id']): dict(r) for r in rows}


def _capacity_for_rule(rule: Dict[str, Any], placement_mode: str, include_extra: bool) -> Tuple[int, int, int]:
    standard_capacity = _ival(rule.get('standard_capacity'), 0, minimum=0)
    room_capacity = _ival(rule.get('room_capacity'), 0, minimum=0)
    bed_capacity = _ival(rule.get('bed_capacity'), 0, minimum=0)
    extra_capacity = _ival(rule.get('extra_capacity'), 0, minimum=0) if _ival(rule.get('extra_enabled'), 0) else 0
    if placement_mode == 'standard':
        base = standard_capacity
        extra = extra_capacity if include_extra else 0
    elif placement_mode == 'rooms':
        base = room_capacity
        extra = extra_capacity if include_extra else 0
    elif placement_mode == 'beds':
        base = bed_capacity
        extra = extra_capacity if include_extra else 0
    elif placement_mode == 'max':
        base = standard_capacity if standard_capacity > 0 else (bed_capacity if bed_capacity > 0 else room_capacity)
        extra = extra_capacity
    else:
        base = 0
        extra = 0
    override = _ival(rule.get('max_capacity_override'), 0, minimum=0)
    total = base + extra
    if placement_mode == 'max' and override > 0:
        total = override
        extra = max(0, total - base)
    return base, extra, total


def _category_rows(payload: Dict[str, Any], placement_mode: str, include_extra: bool) -> List[Dict[str, Any]]:
    rules = _rules_map()
    rows: List[Dict[str, Any]] = []
    for cat in payload.get('categories') or []:
        rid = _ival(cat.get('room_type_id'), 0)
        if rid == 13 or rid <= 0:
            continue
        rule = rules.get(rid) or {
            'room_type_id': rid, 'pms_category': cat.get('category') or '', 'guest_label': cat.get('category') or '',
            'is_enabled': 0, 'standard_capacity': 0, 'room_capacity': 0, 'bed_capacity': 0, 'extra_capacity': 0, 'extra_enabled': 0,
            'priority': rid * 10, 'max_capacity_override': 0, 'manager_note': '', 'structure_note': '',
        }
        base, extra, cap = _capacity_for_rule(rule, placement_mode, include_extra)
        available = _ival(cat.get('available_rooms'), 0, minimum=0)
        configured = bool(_ival(rule.get('is_enabled'), 0) and cap > 0)
        rows.append({
            'room_type_id': rid,
            'category': str(cat.get('category') or rule.get('pms_category') or f'RoomTypeID {rid}'),
            'guest_label': str(rule.get('guest_label') or cat.get('category') or ''),
            'available_rooms': available,
            'active_rooms_whole_stay': _ival(cat.get('active_rooms_whole_stay'), 0, minimum=0),
            'occupied_rooms_any_overlap': _ival(cat.get('occupied_rooms_any_overlap'), 0, minimum=0),
            'room_ids': list(cat.get('room_ids') or []),
            'room_labels': list(cat.get('room_labels') or []),
            'is_enabled': _ival(rule.get('is_enabled'), 0),
            'priority': _ival(rule.get('priority'), 100),
            'base_capacity': base,
            'extra_capacity': extra,
            'standard_capacity_rule': _ival(rule.get('standard_capacity'), 0, minimum=0),
            'room_capacity_rule': _ival(rule.get('room_capacity'), 0, minimum=0),
            'bed_capacity_rule': _ival(rule.get('bed_capacity'), 0, minimum=0),
            'capacity_per_room': cap,
            'configured': configured,
            'total_capacity': available * cap if configured else 0,
            'structure_note': str(rule.get('structure_note') or ''),
            'manager_note': str(rule.get('manager_note') or ''),
            'extra_label': str(rule.get('extra_label') or 'Додаткове місце / диван'),
            'portable_standard_bed': _uses_portable_standard_bed(rid),
        })
    rows.sort(key=lambda r: (r['priority'], r['room_type_id']))
    return rows


def _auto_allocate(
    rows: List[Dict[str, Any]], guests: int, strategy: str, placement_mode: str = '',
    portable_extra_bed_pool: Optional[int] = None,
) -> Dict[int, int]:
    candidates = [r for r in rows if r['configured'] and r['available_rooms'] > 0 and r['capacity_per_room'] > 0]
    if placement_mode == 'beds':
        # Conference/group rule: consume Twin inventory first, then Double as single occupancy,
        # then other configured categories.
        def bed_tier(row: Dict[str, Any]) -> int:
            rid = _ival(row.get('room_type_id'), 0)
            if rid in (4, 5):
                return 0
            if rid in (2, 3):
                return 1
            return 2
        if strategy == 'fewest_rooms':
            candidates.sort(key=lambda r: (bed_tier(r), -r['capacity_per_room'], r['priority'], r['room_type_id']))
        else:
            candidates.sort(key=lambda r: (bed_tier(r), r['priority'], -r['capacity_per_room'], r['room_type_id']))
    elif strategy == 'fewest_rooms':
        candidates.sort(key=lambda r: (-r['capacity_per_room'], r['priority'], r['room_type_id']))
    else:
        candidates.sort(key=lambda r: (r['priority'], -r['capacity_per_room'], r['room_type_id']))

    pool_remaining = _standard_extra_bed_pool() if portable_extra_bed_pool is None else max(0, int(portable_extra_bed_pool))
    remaining = max(0, guests)
    allocation: Dict[int, int] = {}

    for row in candidates:
        rid = _ival(row.get('room_type_id'), 0)
        if remaining <= 0:
            allocation[rid] = 0
            continue
        max_rooms = _ival(row.get('available_rooms'), 0, minimum=0)
        base = _ival(row.get('base_capacity'), 0, minimum=0)
        extra = _ival(row.get('extra_capacity'), 0, minimum=0)
        cap = _ival(row.get('capacity_per_room'), 0, minimum=0)
        portable = bool(row.get('portable_standard_bed'))

        def effective_capacity(room_count: int) -> int:
            if portable and extra > 0:
                return room_count * base + min(pool_remaining, room_count * extra)
            return room_count * cap

        use_rooms = max_rooms
        for n in range(1, max_rooms + 1):
            if effective_capacity(n) >= remaining:
                use_rooms = n
                break
        allocation[rid] = use_rooms
        if use_rooms <= 0:
            continue

        if portable and extra > 0:
            base_capacity = use_rooms * base
            portable_needed = max(0, min(pool_remaining, use_rooms * extra, remaining - base_capacity))
            pool_remaining -= portable_needed
            placed = base_capacity + portable_needed
        else:
            placed = use_rooms * cap
        remaining = max(0, remaining - placed)
    return allocation


def _allocation_summary(
    rows: List[Dict[str, Any]], allocation: Dict[int, int], guests: int,
    portable_extra_bed_pool: Optional[int] = None,
) -> Dict[str, Any]:
    items: List[Dict[str, Any]] = []
    placed_capacity = 0
    used_rooms = 0
    extra_places_used_capacity = 0
    pool_limit = _standard_extra_bed_pool() if portable_extra_bed_pool is None else max(0, int(portable_extra_bed_pool))
    pool_remaining = pool_limit

    # Capacity shown to the allocator already respects the global physical stock of
    # portable beds in Standards. Built-in sofas / extra places in non-Standard room
    # types remain independent from that stock.
    available_capacity = 0
    available_pool_remaining = pool_limit
    for row in rows:
        if not row.get('configured'):
            continue
        available = _ival(row.get('available_rooms'), 0, minimum=0)
        base = _ival(row.get('base_capacity'), 0, minimum=0)
        extra = _ival(row.get('extra_capacity'), 0, minimum=0)
        cap = _ival(row.get('capacity_per_room'), 0, minimum=0)
        if row.get('portable_standard_bed') and extra > 0:
            portable_capacity = min(available_pool_remaining, available * extra)
            available_capacity += available * base + portable_capacity
            available_pool_remaining -= portable_capacity
        else:
            available_capacity += available * cap

    portable_capacity_selected = 0
    for row in rows:
        requested = max(0, _ival(allocation.get(row['room_type_id']), 0))
        use = min(requested, row['available_rooms'])
        if not row['configured']:
            use = 0
        base = _ival(row.get('base_capacity'), 0, minimum=0)
        extra = _ival(row.get('extra_capacity'), 0, minimum=0)
        cap = _ival(row.get('capacity_per_room'), 0, minimum=0)
        if row.get('portable_standard_bed') and extra > 0:
            row_portable = min(pool_remaining, use * extra)
            pool_remaining -= row_portable
            portable_capacity_selected += row_portable
            subtotal = use * base + row_portable
        else:
            subtotal = use * cap
        used_rooms += use
        placed_capacity += subtotal
        extra_places_used_capacity += max(0, subtotal - use * base)
        item = dict(row)
        item.update({'allocated_rooms': use, 'allocated_capacity': subtotal})
        items.append(item)
    selected_without_portable = 0
    selected_portable_capacity_total = 0
    for item in items:
        use = _ival(item.get('allocated_rooms'), 0, minimum=0)
        if item.get('portable_standard_bed'):
            selected_without_portable += use * _ival(item.get('base_capacity'), 0, minimum=0)
            selected_portable_capacity_total += use * _ival(item.get('extra_capacity'), 0, minimum=0)
        else:
            selected_without_portable += use * _ival(item.get('capacity_per_room'), 0, minimum=0)
    standard_extra_required = max(0, min(selected_portable_capacity_total, guests - selected_without_portable))
    shortage = max(0, guests - placed_capacity)
    spare = max(0, placed_capacity - guests)
    return {
        'rows': items,
        'used_rooms': used_rooms,
        'placed_guests': min(guests, placed_capacity),
        'allocated_capacity': placed_capacity,
        'shortage': shortage,
        'spare_places': spare,
        'configured_capacity': available_capacity,
        'fits': shortage == 0,
        'extra_places_capacity_in_selected_rooms': extra_places_used_capacity,
        'standard_extra_bed_pool_limit': pool_limit,
        'standard_extra_bed_capacity_selected': portable_capacity_selected,
        'standard_extra_beds_required_for_selected': standard_extra_required,
        'standard_extra_bed_pool_blocking': bool(standard_extra_required > pool_limit),
    }


def _allocation_failure_message(summary: Dict[str, Any]) -> str:
    if summary.get('fits'):
        return ''
    if summary.get('standard_extra_bed_pool_blocking'):
        required = _ival(summary.get('standard_extra_beds_required_for_selected'), 0, minimum=0)
        limit = _ival(summary.get('standard_extra_bed_pool_limit'), DEFAULT_STANDARD_EXTRA_BED_POOL, minimum=0)
        return (
            f'Для вибраної схеми потрібно щонайменше {required} переносних додаткових ліжок у стандартних номерах, '
            f'але фізично доступно {limit}. Дивани та штатні додаткові місця в інших категоріях цей фонд не витрачають.'
        )
    shortage = _ival(summary.get('shortage'), 0, minimum=0)
    if shortage:
        return f'За доступними номерами та налаштованою місткістю не вистачає {shortage} місць.'
    return 'Вибрана схема розміщення не вміщує всіх гостей.'



def _capacity_probe_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Physical guest capacity for an availability result, respecting the shared Standard-bed pool."""
    pool_limit = _standard_extra_bed_pool()
    pool_remaining = pool_limit
    total_capacity = 0
    total_rooms = 0
    portable_used = 0
    detail: List[Dict[str, Any]] = []
    for row in sorted((dict(x) for x in rows if isinstance(x, dict)), key=lambda x: (_ival(x.get('priority'), 9999), _ival(x.get('room_type_id'), 9999))):
        if not row.get('configured'):
            continue
        available = _ival(row.get('available_rooms'), 0, minimum=0)
        if available <= 0:
            continue
        base = _ival(row.get('base_capacity'), 0, minimum=0)
        extra = _ival(row.get('extra_capacity'), 0, minimum=0)
        base_total = available * base
        extra_total = available * extra
        portable = bool(row.get('portable_standard_bed'))
        if portable and extra_total > 0:
            extra_total = min(pool_remaining, extra_total)
            pool_remaining -= extra_total
            portable_used += extra_total
        capacity = base_total + extra_total
        if capacity <= 0:
            continue
        total_capacity += capacity
        total_rooms += available
        detail.append({
            'room_type_id': _ival(row.get('room_type_id'), 0),
            'category': str(row.get('guest_label') or row.get('category') or ''),
            'rooms': available,
            'base_capacity': base_total,
            'extra_capacity': extra_total,
            'capacity': capacity,
            'portable_extra_beds': extra_total if portable else 0,
            'built_in_extra_places': extra_total if not portable else 0,
        })
    return {
        'capacity': total_capacity, 'rooms': total_rooms, 'rows': detail,
        'portable_extra_beds_used': portable_used, 'portable_extra_bed_pool_limit': pool_limit,
    }


def _capacity_probe_for_period(arrival: str, departure: str, mode: str) -> Dict[str, Any]:
    if mode not in CAPACITY_PROBE_LABELS:
        mode = 'max'
    a, _d, nights = _parse_dates(arrival, departure)
    include_extra = mode == 'max'
    conn = _db()

    raw = _request_pms_live(arrival, departure)
    payload, warnings = _validate_payload(raw, arrival, departure)
    _sync_categories_from_payload(conn, payload)
    rows = _category_rows(payload, mode, include_extra)
    whole = _capacity_probe_rows(rows)

    by_day: List[Dict[str, Any]] = []
    for idx in range(nights):
        d = (a + timedelta(days=idx)).isoformat()
        nd = (a + timedelta(days=idx + 1)).isoformat()
        day_raw = _request_pms_live(d, nd)
        day_payload, day_warnings = _validate_payload(day_raw, d, nd)
        _sync_categories_from_payload(conn, day_payload)
        day_rows = _category_rows(day_payload, mode, include_extra)
        one = _capacity_probe_rows(day_rows)
        one.update({
            'date': d, 'next_date': nd,
            'date_label': (a + timedelta(days=idx)).strftime('%d.%m.%Y'),
            'next_date_label': (a + timedelta(days=idx + 1)).strftime('%d.%m.%Y'),
            'warnings': day_warnings,
        })
        by_day.append(one)
    conn.commit()
    limiting = min(by_day, key=lambda x: _ival(x.get('capacity'), 0)) if by_day else None
    return {
        'arrival': arrival, 'departure': departure, 'nights': nights, 'mode': mode,
        'mode_label': CAPACITY_PROBE_LABELS[mode], 'whole_period_capacity': whole['capacity'],
        'whole_period_rooms': whole['rooms'], 'rows': whole['rows'], 'by_day': by_day,
        'limiting_day': limiting, 'warnings': warnings,
        'portable_extra_beds_used': whole['portable_extra_beds_used'],
        'portable_extra_bed_pool_limit': whole['portable_extra_bed_pool_limit'],
        'include_extra': include_extra,
    }

def _manual_allocation_from_form(rows: List[Dict[str, Any]]) -> Dict[int, int]:
    allocation: Dict[int, int] = {}
    for row in rows:
        rid = row['room_type_id']
        allocation[rid] = _ival(request.form.get(f'alloc_{rid}'), 0, minimum=0, maximum=row['available_rooms'])
    return allocation


def _room_plan_key(room_type_id: int, position: int) -> str:
    return f'{int(room_type_id)}_{int(position)}'


def _build_room_plan(summary: Dict[str, Any], adults: int, children: int, paid_children_total: int = 0, form=None) -> List[Dict[str, Any]]:
    """Build an editable per-physical-room occupancy plan.

    Automatic occupancy fills all ordinary/base places first and only then uses extra
    places. This is important for the shared stock of 10 portable Standard beds: the
    allocator must never request a portable bed merely because the first room was filled
    to maximum while another selected room still had a normal bed free.
    """
    instances: List[Dict[str, Any]] = []
    for row in summary.get('rows') or []:
        use = _ival(row.get('allocated_rooms'), 0, minimum=0)
        room_ids = list(row.get('room_ids') or [])
        room_labels = list(row.get('room_labels') or [])
        for idx in range(use):
            key = _room_plan_key(_ival(row.get('room_type_id'), 0), idx + 1)
            instances.append({
                'key': key,
                'room_type_id': _ival(row.get('room_type_id'), 0),
                'category': str(row.get('category') or ''),
                'room_id': room_ids[idx] if idx < len(room_ids) else None,
                'room_label': str(room_labels[idx]) if idx < len(room_labels) else (f'RoomID {room_ids[idx]}' if idx < len(room_ids) else f'#{idx + 1}'),
                'position': idx + 1,
                'base_capacity': _ival(row.get('base_capacity'), 0, minimum=0),
                'extra_capacity': _ival(row.get('extra_capacity'), 0, minimum=0),
                'capacity_per_room': _ival(row.get('capacity_per_room'), 0, minimum=0),
                'room_capacity_rule': _ival(row.get('room_capacity_rule'), 0, minimum=0),
                'bed_capacity_rule': _ival(row.get('bed_capacity_rule'), 0, minimum=0),
                'portable_standard_bed': bool(row.get('portable_standard_bed')),
            })

    # Determine default occupancy counts by filling base places in every selected room
    # before any extra place. Then use extras only for the remaining guests.
    total_people = max(0, adults) + max(0, children)
    occ_targets = [0 for _ in instances]
    left = total_people
    for i, item in enumerate(instances):
        take = min(left, _ival(item.get('base_capacity'), 0, minimum=0))
        occ_targets[i] += take
        left -= take
    for i, item in enumerate(instances):
        if left <= 0:
            break
        take = min(left, _ival(item.get('extra_capacity'), 0, minimum=0))
        occ_targets[i] += take
        left -= take

    remaining_adults = max(0, adults)
    remaining_children = max(0, children)
    remaining_paid_children = max(0, min(children, paid_children_total))
    active_indices = [i for i, target in enumerate(occ_targets) if target > 0]
    if active_indices and remaining_adults < len(active_indices):
        raise ValueError(
            f'Для вибраного розміщення потрібно {len(active_indices)} зайнятих номерів, але дорослих лише {remaining_adults}. '
            'PMS не тарифікує номер, у якому проживають тільки діти. Оберіть місткіші номери/інший режим або зменште кількість окремих номерів.'
        )

    # Seed one adult into every occupied room first. The old sequential filler could put
    # all adults into the first rooms and leave the last selected rooms child-only, which
    # caused PMS HTTP 400 `adults must be >= 1` despite adults being present in the group.
    seeded_adults = [0 for _ in instances]
    for i in active_indices:
        seeded_adults[i] = 1
        remaining_adults -= 1

    out: List[Dict[str, Any]] = []
    for index, item in enumerate(instances):
        target_occ = occ_targets[index]
        default_adults = seeded_adults[index]
        free = max(0, target_occ - default_adults)
        add_adults = min(remaining_adults, free)
        default_adults += add_adults
        remaining_adults -= add_adults
        free = max(0, target_occ - default_adults)
        default_children = min(remaining_children, free)
        remaining_children -= default_children
        key = item['key']

        def field(name: str, default: int = 0) -> int:
            if form is None:
                return default
            raw = form.get(f'roomplan_{key}_{name}')
            if raw in (None, ''):
                return default
            return _ival(raw, default, minimum=0)

        row = dict(item)
        default_paid_children = min(remaining_paid_children, default_children)
        remaining_paid_children -= default_paid_children
        default_occupants = default_adults + default_children
        default_extra_beds = max(0, min(item['extra_capacity'], default_occupants - item['base_capacity']))
        row.update({
            'adults': field('adults', default_adults),
            'children': field('children', default_children),
            'paid_children': field('paid_children', default_paid_children),
            'extra_beds': field('extra_beds', default_extra_beds),
            'resident_adults': field('resident_adults', 0),
            'nonresident_adults': field('nonresident_adults', 0),
            'tourist_tax_exempt_adults': field('tourist_tax_exempt_adults', 0),
        })
        row['occupants'] = row['adults'] + row['children']
        out.append(row)
    return out



def _daily_room_options(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Flatten live per-night room stock into physical-room options for manual editing."""
    out: List[Dict[str, Any]] = []
    seen = set()
    for row in rows:
        if not row.get('configured'):
            continue
        room_ids = list(row.get('room_ids') or [])
        room_labels = list(row.get('room_labels') or [])
        limit = min(_ival(row.get('available_rooms'), 0, minimum=0), len(room_ids))
        for idx in range(limit):
            room_id = room_ids[idx]
            token = str(room_id)
            if not token or token in seen:
                continue
            seen.add(token)
            room_label = str(room_labels[idx]) if idx < len(room_labels) else f'RoomID {room_id}'
            out.append({
                'room_id': room_id,
                'room_id_token': token,
                'room_label': room_label,
                'room_type_id': _ival(row.get('room_type_id'), 0),
                'category': str(row.get('guest_label') or row.get('category') or ''),
                'pms_category': str(row.get('category') or ''),
                'base_capacity': _ival(row.get('base_capacity'), 0, minimum=0),
                'extra_capacity': _ival(row.get('extra_capacity'), 0, minimum=0),
                'capacity_per_room': _ival(row.get('capacity_per_room'), 0, minimum=0),
                'room_capacity_rule': _ival(row.get('room_capacity_rule'), 0, minimum=0),
                'bed_capacity_rule': _ival(row.get('bed_capacity_rule'), 0, minimum=0),
                'portable_standard_bed': bool(row.get('portable_standard_bed')),
                'priority': _ival(row.get('priority'), 100),
            })
    out.sort(key=lambda x: (x['priority'], x['room_type_id'], x['room_label']))
    return out


def _manual_day_prefix(day_date: str) -> str:
    return 'dayroom_' + str(day_date or '').replace('-', '')


def _manual_day_room_plan_from_form(
    *, day_date: str, rows: List[Dict[str, Any]], adults: int, children: int,
    paid_children: int, form, require_full: bool = True, allow_composition_change: bool = False,
) -> List[Dict[str, Any]]:
    """Read an exact physical-room plan posted by the per-night manual editor.

    ``require_full`` keeps the historical behavior: the room rows must exactly match the
    already declared day composition.  ``allow_composition_change`` is used by the inline
    manual editor: the physical-room rows themselves become the source of truth for that
    night's adults/children counts.
    """
    prefix = _manual_day_prefix(day_date)
    room_ids = list(form.getlist(prefix + '_room_id'))
    adult_values = list(form.getlist(prefix + '_adults'))
    child_values = list(form.getlist(prefix + '_children'))
    paid_values = list(form.getlist(prefix + '_paid_children'))
    extra_values = list(form.getlist(prefix + '_extra_beds'))
    locked_values = list(form.getlist(prefix + '_locked'))
    early_values = list(form.getlist(prefix + '_early_checkin'))
    late_values = list(form.getlist(prefix + '_late_checkout'))
    options = _daily_room_options(rows)
    catalog = {str(o['room_id_token']): o for o in options}
    selected = set()
    out: List[Dict[str, Any]] = []
    type_positions: Dict[int, int] = {}

    for idx, raw_room_id in enumerate(room_ids):
        token = str(raw_room_id or '').strip()
        if not token:
            continue
        if token in selected:
            raise ValueError(f'{day_date}: фізичний номер {token} вибрано двічі.')
        selected.add(token)
        opt = catalog.get(token)
        if not opt:
            raise ValueError(
                f'{day_date}: номер {token} більше не входить до актуально вільних номерів на цю ніч. '
                'Оновіть ручний план і виберіть інший номер.'
            )
        rid = _ival(opt.get('room_type_id'), 0)
        type_positions[rid] = type_positions.get(rid, 0) + 1
        a = _ival(adult_values[idx] if idx < len(adult_values) else 0, 0, minimum=0)
        c = _ival(child_values[idx] if idx < len(child_values) else 0, 0, minimum=0)
        pc = _ival(paid_values[idx] if idx < len(paid_values) else 0, 0, minimum=0)
        eb = _ival(extra_values[idx] if idx < len(extra_values) else 0, 0, minimum=0)
        locked = str(locked_values[idx] if idx < len(locked_values) else '0').strip() in ('1','true','yes','on')
        early = str(early_values[idx] if idx < len(early_values) else '0').strip() in ('1','true','yes','on')
        late = str(late_values[idx] if idx < len(late_values) else '0').strip() in ('1','true','yes','on')
        row = dict(opt)
        row.update({
            'key': f'manual_{day_date.replace("-", "")}_{idx + 1}',
            'position': type_positions[rid],
            'adults': a,
            'children': c,
            'paid_children': pc,
            'extra_beds': eb,
            'resident_adults': 0,
            'nonresident_adults': 0,
            'tourist_tax_exempt_adults': 0,
            'occupants': a + c,
            'manual_locked': bool(locked),
            'manual_source': 'manual',
            'early_checkin': bool(early),
            'late_checkout': bool(late),
        })
        out.append(row)

    if require_full:
        _validate_room_plan(out, adults, children, paid_children)
    else:
        if not out:
            if allow_composition_change:
                raise ValueError(
                    f'{day_date}: після ручного редагування не залишилося жодного номера. '
                    'Якщо на цю ніч група вже не проживає, змініть дати пропозиції.'
                )
            return []
        total_a = sum(_ival(r.get('adults'), 0, minimum=0) for r in out)
        total_c = sum(_ival(r.get('children'), 0, minimum=0) for r in out)
        total_pc = sum(_ival(r.get('paid_children'), 0, minimum=0) for r in out)
        if not allow_composition_change and (total_a > adults or total_c > children or total_pc > paid_children):
            raise ValueError(
                f'{day_date}: у закріплених вручну номерах гостей більше, ніж у складі групи цієї ночі.'
            )
        # Validate every room and the shared portable-bed pool against the composition
        # that is actually present in the posted rows.  This intentionally does not
        # compare with the old header composition when inline editing is allowed.
        _validate_room_plan(out, total_a, total_c, total_pc)
    return out



def _manual_selected_room_plan_from_form(
    *, day_date: str, rows: List[Dict[str, Any]], adults: int, children: int,
    paid_children: int, form,
) -> List[Dict[str, Any]]:
    """Build an exact physical-room plan from the manager's selected room IDs.

    The manager edits only the physical room list. Guest occupancy is redistributed
    automatically across the selected rooms, so Add/Remove/Replace works without
    forcing the manager to rebalance adults/children by hand.
    """
    prefix = _manual_day_prefix(day_date)
    tokens = [str(x or '').strip() for x in form.getlist(prefix + '_selected_room_id')]
    tokens = [x for x in tokens if x]
    if not tokens:
        # Backward compatibility with v5.305 field names.
        tokens = [str(x or '').strip() for x in form.getlist(prefix + '_room_id')]
        tokens = [x for x in tokens if x]
    if not tokens:
        raise ValueError(f'{day_date}: не вибрано жодного фізичного номера.')
    if len(set(tokens)) != len(tokens):
        raise ValueError(f'{day_date}: один фізичний номер вибрано двічі.')

    options = _daily_room_options(rows)
    catalog = {str(o.get('room_id_token') or o.get('room_id')): o for o in options}
    selected = []
    for token in tokens:
        opt = catalog.get(token)
        if not opt:
            raise ValueError(f'{day_date}: номер {token} вже не є вільним на цю ніч. Оновіть розрахунок і виберіть інший номер.')
        selected.append(dict(opt))

    total_people = max(0, adults) + max(0, children)
    if total_people <= 0:
        raise ValueError(f'{day_date}: кількість гостей повинна бути більше нуля.')
    if len(selected) > total_people:
        raise ValueError(f'{day_date}: вибрано {len(selected)} номерів для {total_people} гостей. Приберіть зайві номери.')
    total_capacity = sum(_ival(o.get('capacity_per_room'), 0, minimum=0) for o in selected)
    if total_capacity < total_people:
        raise ValueError(f'{day_date}: у вибраних номерах лише {total_capacity} місць для {total_people} гостей. Додайте номер або змініть склад.')

    # Every manually selected room receives at least one guest. Then fill ordinary/base
    # places across all rooms before using extra places. This makes a simple Remove/Add
    # operation immediately usable and preserves the shared portable-bed logic.
    targets = [1 for _ in selected]
    left = total_people - len(selected)
    for i, opt in enumerate(selected):
        if left <= 0:
            break
        base = min(_ival(opt.get('base_capacity'), 0, minimum=0), _ival(opt.get('capacity_per_room'), 0, minimum=0))
        take = min(left, max(0, base - targets[i]))
        targets[i] += take
        left -= take
    for i, opt in enumerate(selected):
        if left <= 0:
            break
        cap = _ival(opt.get('capacity_per_room'), 0, minimum=0)
        take = min(left, max(0, cap - targets[i]))
        targets[i] += take
        left -= take
    if left > 0:
        raise ValueError(f'{day_date}: вибрані номери не вміщують ще {left} гостей.')

    remaining_adults = max(0, adults)
    remaining_children = max(0, children)
    remaining_paid = max(0, min(children, paid_children))
    type_positions: Dict[int, int] = {}
    out: List[Dict[str, Any]] = []
    for idx, (opt, target) in enumerate(zip(selected, targets), start=1):
        rid = _ival(opt.get('room_type_id'), 0)
        type_positions[rid] = type_positions.get(rid, 0) + 1
        a = min(remaining_adults, target)
        remaining_adults -= a
        free = target - a
        c = min(remaining_children, free)
        remaining_children -= c
        pc = min(remaining_paid, c)
        remaining_paid -= pc
        base = _ival(opt.get('base_capacity'), 0, minimum=0)
        extra_beds = max(0, target - base)
        row = dict(opt)
        row.update({
            'key': f'manualsel_{day_date.replace("-", "")}_{idx}',
            'position': type_positions[rid],
            'adults': a, 'children': c, 'paid_children': pc,
            'extra_beds': extra_beds, 'resident_adults': 0,
            'nonresident_adults': 0, 'tourist_tax_exempt_adults': 0,
            'occupants': a + c, 'manual_locked': True,
            'manual_source': 'selected_rooms', 'early_checkin': False, 'late_checkout': False,
        })
        out.append(row)
    _validate_room_plan(out, adults, children, paid_children)
    return out

def _manual_room_plan_counts(room_plan: List[Dict[str, Any]]) -> Dict[str, int]:
    adults = sum(_ival(r.get('adults'), 0, minimum=0) for r in room_plan or [])
    children = sum(_ival(r.get('children'), 0, minimum=0) for r in room_plan or [])
    paid_children = sum(_ival(r.get('paid_children'), 0, minimum=0) for r in room_plan or [])
    return {
        'adults': adults,
        'children': children,
        'paid_children': paid_children,
        'guest_count': adults + children,
    }


def _rows_excluding_physical_rooms(rows: List[Dict[str, Any]], excluded_room_ids: Iterable[Any]) -> List[Dict[str, Any]]:
    excluded = {str(x) for x in excluded_room_ids if str(x).strip()}
    out: List[Dict[str, Any]] = []
    for source in rows:
        row = dict(source)
        ids = list(source.get('room_ids') or [])
        labels = list(source.get('room_labels') or [])
        new_ids: List[Any] = []
        new_labels: List[str] = []
        for idx, room_id in enumerate(ids):
            if str(room_id) in excluded:
                continue
            new_ids.append(room_id)
            new_labels.append(str(labels[idx]) if idx < len(labels) else f'RoomID {room_id}')
        row['room_ids'] = new_ids
        row['room_labels'] = new_labels
        row['available_rooms'] = min(_ival(source.get('available_rooms'), 0, minimum=0), len(new_ids))
        row['total_capacity'] = row['available_rooms'] * _ival(row.get('capacity_per_room'), 0, minimum=0) if row.get('configured') else 0
        out.append(row)
    return out


def _manual_summary_from_room_plan(rows: List[Dict[str, Any]], room_plan: List[Dict[str, Any]], guests: int) -> Dict[str, Any]:
    by_type: Dict[int, List[Dict[str, Any]]] = {}
    for room in room_plan:
        by_type.setdefault(_ival(room.get('room_type_id'), 0), []).append(room)
    row_map = {_ival(r.get('room_type_id'), 0): r for r in rows}
    items: List[Dict[str, Any]] = []
    capacity = 0
    for rid, rooms in sorted(by_type.items(), key=lambda kv: (_ival(row_map.get(kv[0], {}).get('priority'), 100), kv[0])):
        source = dict(row_map.get(rid) or {})
        cap = sum(_ival(r.get('capacity_per_room'), 0, minimum=0) for r in rooms)
        capacity += cap
        source.update({
            'allocated_rooms': len(rooms),
            'allocated_capacity': cap,
            'room_ids': [r.get('room_id') for r in rooms],
            'room_labels': [str(r.get('room_label') or r.get('room_id') or '') for r in rooms],
            'category': str(rooms[0].get('pms_category') or rooms[0].get('category') or source.get('category') or ''),
            'guest_label': str(rooms[0].get('category') or source.get('guest_label') or source.get('category') or ''),
            'capacity_per_room': _ival(rooms[0].get('capacity_per_room'), 0, minimum=0),
        })
        items.append(source)
    return {
        'rows': items,
        'used_rooms': len(room_plan),
        'allocated_capacity': capacity,
        'configured_capacity': capacity,
        'shortage': max(0, guests - capacity),
        'spare_places': max(0, capacity - guests),
        'fits': capacity >= guests,
        'standard_extra_beds_used': _portable_standard_bed_usage(room_plan),
        'standard_extra_bed_pool_limit': _standard_extra_bed_pool(),
    }


def _refill_day_around_locked_rooms(
    *, day_date: str, rows: List[Dict[str, Any]], adults: int, children: int, paid_children: int,
    placement_mode: str, strategy: str, form,
) -> List[Dict[str, Any]]:
    posted = _manual_day_room_plan_from_form(
        day_date=day_date, rows=rows, adults=adults, children=children,
        paid_children=paid_children, form=form, require_full=False,
    )
    locked = [dict(x) for x in posted if x.get('manual_locked')]
    locked_a = sum(_ival(x.get('adults'), 0, minimum=0) for x in locked)
    locked_c = sum(_ival(x.get('children'), 0, minimum=0) for x in locked)
    locked_pc = sum(_ival(x.get('paid_children'), 0, minimum=0) for x in locked)
    rem_a = adults - locked_a
    rem_c = children - locked_c
    rem_pc = paid_children - locked_pc
    rem_guests = rem_a + rem_c
    if rem_a < 0 or rem_c < 0 or rem_pc < 0:
        raise ValueError(f'{day_date}: закріплені номери містять більше гостей, ніж задано на цю ніч.')
    if rem_guests <= 0:
        _validate_room_plan(locked, adults, children, paid_children)
        return locked

    reduced_rows = _rows_excluding_physical_rooms(rows, [x.get('room_id') for x in locked])
    portable_remaining = max(0, _standard_extra_bed_pool() - _portable_standard_bed_usage(locked))
    structural_strategy = 'priority' if strategy == 'best_price' else strategy
    allocation = _auto_allocate(
        reduced_rows, rem_guests, structural_strategy, placement_mode,
        portable_extra_bed_pool=portable_remaining,
    )
    summary = _allocation_summary(
        reduced_rows, allocation, rem_guests,
        portable_extra_bed_pool=portable_remaining,
    )
    if not summary.get('fits'):
        raise ValueError(f'{day_date}: після закріплених номерів {_allocation_failure_message(summary)}')
    generated = _build_room_plan(summary, rem_a, rem_c, paid_children_total=rem_pc, form=None)
    for pos, g in enumerate(generated, start=1):
        g['manual_locked'] = False
        g['manual_source'] = 'auto_refill'
        g['key'] = f'refill_{day_date.replace("-", "")}_{len(locked) + pos}'
    combined = locked + generated
    _validate_room_plan(combined, adults, children, paid_children)
    return combined

def _validate_room_plan(room_plan: List[Dict[str, Any]], adults: int, children: int, paid_children_total: int = 0) -> None:
    if not room_plan:
        raise ValueError('Не вибрано жодного фізичного номера для HMS quote.')
    total_adults = sum(_ival(r.get('adults'), 0, minimum=0) for r in room_plan)
    total_children = sum(_ival(r.get('children'), 0, minimum=0) for r in room_plan)
    if total_adults != adults or total_children != children:
        raise ValueError(f'Розподіл гостей по номерах не збігається з заявкою: дорослі {total_adults}/{adults}, діти {total_children}/{children}.')
    total_paid_children = sum(_ival(r.get('paid_children'), 0, minimum=0) for r in room_plan)
    if total_paid_children != paid_children_total:
        raise ValueError(f'Платні діти у розподілі не збігаються із заявкою: {total_paid_children}/{paid_children_total}.')
    for room in room_plan:
        occupants = _ival(room.get('adults'), 0, minimum=0) + _ival(room.get('children'), 0, minimum=0)
        if occupants <= 0:
            raise ValueError(f"{room.get('category')} {room.get('room_label')}: вибраний номер має 0 гостей. Приберіть номер або заповніть розміщення.")
        room_adults = _ival(room.get('adults'), 0, minimum=0)
        if room_adults <= 0:
            raise ValueError(
                f"{room.get('category')} {room.get('room_label')}: у номері є гості, але немає дорослого. "
                'Система бронювання приймає тарифний запит лише коли в кожному зайнятому номері є щонайменше 1 дорослий.'
            )
        cap = _ival(room.get('capacity_per_room'), 0, minimum=0)
        if cap > 0 and occupants > cap:
            raise ValueError(f"{room.get('category')} {room.get('room_label')}: гостей {occupants}, локальна дозволена місткість {cap}.")
        child_count = _ival(room.get('children'), 0, minimum=0)
        paid_children = _ival(room.get('paid_children'), 0, minimum=0)
        if paid_children > child_count:
            raise ValueError(f"{room.get('category')} {room.get('room_label')}: paid_children не може перевищувати children.")
        extra_beds = _ival(room.get('extra_beds'), 0, minimum=0)
        extra_capacity = _ival(room.get('extra_capacity'), 0, minimum=0)
        if extra_beds > occupants:
            raise ValueError(f"{room.get('category')} {room.get('room_label')}: кількість додаткових місць перевищує кількість гостей.")
        if extra_beds > extra_capacity:
            raise ValueError(f"{room.get('category')} {room.get('room_label')}: вказано {extra_beds} дод. місць, а локальна матриця дозволяє максимум {extra_capacity} для цього режиму розміщення.")

    portable_used = _portable_standard_bed_usage(room_plan)
    portable_limit = _standard_extra_bed_pool()
    if portable_used > portable_limit:
        raise ValueError(
            f'Для цього розміщення потрібно {portable_used} переносних додаткових ліжок у стандартних номерах, '
            f'але фізично доступно {portable_limit}. Зменште кількість додаткових місць у стандартах або змініть розміщення.'
        )


def _pms_pricing_occupancy(room: Dict[str, Any]) -> Dict[str, int]:
    """Translate Riverwood guest composition into the commercial occupancy sent to PMS.

    Riverwood business rule:
    - a paid child using a normal/base place is priced as the next adult occupancy;
    - a paid child using an extra place remains a paid child and may receive the child/extra-place charge;
    - the real guest composition is NOT changed for capacity, guest lists or tourist tax.

    Example for a double room: 1 adult + 1 paid child on the two normal places is
    quoted to PMS as 2 adults, not as 1 adult + a 2,000 UAH child supplement.
    """
    adults = _ival(room.get('adults'), 0, minimum=0)
    children = _ival(room.get('children'), 0, minimum=0)
    paid_children = min(children, _ival(room.get('paid_children'), 0, minimum=0))
    base_capacity = _ival(room.get('base_capacity'), 0, minimum=0)
    extra_beds = _ival(room.get('extra_beds'), 0, minimum=0)

    # Adults occupy base places first. Paid children are then promoted to adult occupancy
    # only while a normal/base place is still available. Any remaining paid children stay
    # children in the PMS request and can therefore be charged as extra child placement.
    free_base_after_adults = max(0, base_capacity - adults)
    paid_children_on_base = min(paid_children, free_base_after_adults)
    paid_children_on_extra = max(0, paid_children - paid_children_on_base)

    return {
        'adults': adults + paid_children_on_base,
        'children': max(0, children - paid_children_on_base),
        'paid_children': paid_children_on_extra,
        'extra_beds': extra_beds,
        'paid_children_on_base': paid_children_on_base,
        'paid_children_on_extra': paid_children_on_extra,
    }


def _quote_room_plan(*, arrival: str, departure: str, room_plan: List[Dict[str, Any]], price_list_id: int = 0, rate_plan_id: int = 0, response_cache: Optional[Dict[str, Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Quote every selected room, deduplicating identical HMS requests.

    The sidecar contract is RoomTypeID + stay + occupancy, not physical RoomID. Reusing
    the exact same live response for identical request payloads avoids dozens of duplicate
    HTTP calls for conference groups while preserving a per-room trace in pricing_json.
    """
    rooms: List[Dict[str, Any]] = []
    before_tax = Decimal('0')
    tax_total = Decimal('0')
    stay_total = Decimal('0')
    base_total = Decimal('0')
    currencies = set()
    sources = set()
    generated: List[str] = []
    response_cache = response_cache if response_cache is not None else {}
    selected_by_type: Dict[int, int] = {}
    available_by_type: Dict[int, List[int]] = {}
    _, _, stay_nights = _parse_dates(arrival, departure)
    restriction_items: List[Dict[str, Any]] = []

    prepared: List[Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], str, Dict[str, int]]] = []
    for room in room_plan:
        room_adults = _ival(room.get('adults'), 0, minimum=0)
        room_children = _ival(room.get('children'), 0, minimum=0)
        if room_adults + room_children > 0 and room_adults <= 0:
            raise ValueError(
                f"{room.get('category')} {room.get('room_label')}: для тарифного запиту PMS потрібен щонайменше 1 дорослий у номері."
            )

        # Keep the real composition for the saved quote / UI. Only the PMS pricing
        # occupancy is transformed according to Riverwood's paid-child business rule.
        actual_req: Dict[str, Any] = {
            'arrival': arrival,
            'departure': departure,
            'room_type_id': _ival(room.get('room_type_id'), 0),
            'adults': room_adults,
            'children': room_children,
            'paid_children': _ival(room.get('paid_children'), 0, minimum=0),
            'extra_beds': _ival(room.get('extra_beds'), 0, minimum=0),
            'occupancy_mode': 'operations_allocation',
            'include_tourist_tax': False,
            'resident_adults': 0,
            'nonresident_adults': 0,
            'tourist_tax_exempt_adults': 0,
            'guest_attributes': {},
        }
        pricing_occ = _pms_pricing_occupancy(room)
        pms_req = dict(actual_req)
        pms_req['adults'] = pricing_occ['adults']
        pms_req['children'] = pricing_occ['children']
        pms_req['paid_children'] = pricing_occ['paid_children']
        pms_req['extra_beds'] = pricing_occ['extra_beds']
        if price_list_id > 0:
            actual_req['price_list_id'] = price_list_id
            pms_req['price_list_id'] = price_list_id
        if rate_plan_id > 0:
            actual_req['rate_plan_id'] = rate_plan_id
            pms_req['rate_plan_id'] = rate_plan_id
        signature = json.dumps(pms_req, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        prepared.append((room, actual_req, pms_req, signature, pricing_occ))
        rid = _ival(room.get('room_type_id'), 0)
        selected_by_type[rid] = selected_by_type.get(rid, 0) + 1

    for room, actual_req, pms_req, signature, pricing_occ in prepared:
        if signature not in response_cache:
            response_cache[signature] = _request_pms_room_quote(pms_req)
        resp = response_cache[signature]
        rid = _ival(room.get('room_type_id'), 0)
        if resp.get('available_rooms') not in (None, ''):
            available_by_type.setdefault(rid, []).append(_ival(resp.get('available_rooms'), 0, minimum=0))

        currency = str(resp.get('currency') or '').strip()
        currencies.add(currency)
        source = str(resp.get('source') or 'HMS/SERVIO').strip()
        if source:
            sources.add(source)
        if resp.get('generated_at'):
            generated.append(str(resp.get('generated_at')))
        room_restrictions = _extract_booking_restrictions(resp, stay_nights)
        for restriction in room_restrictions:
            restriction.setdefault('room_type_id', rid)
            restriction.setdefault('category', str(room.get('category') or ''))
            restriction.setdefault('room_label', str(room.get('room_label') or ''))
        restriction_items.extend(room_restrictions)

        resp_before = _response_money_decimal(resp, 'stay_total_before_tourist_tax')
        resp_tax = _response_money_decimal(resp, 'tourist_tax_total')
        resp_total = _response_money_decimal(resp, 'stay_total')
        resp_base = _response_money_decimal(resp, 'base_accommodation_total', fallback_key='base_stay_total')
        if (resp_before + resp_tax).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP) != resp_total.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):
            raise RuntimeError(
                f"PMS Room Quote total не сходиться для RoomTypeID {rid}: "
                f"before_tax {resp_before} + tax {resp_tax} != stay_total {resp_total}."
            )
        before_tax += resp_before
        tax_total += resp_tax
        stay_total += resp_total
        base_total += resp_base
        rooms.append({
            'room_key': room.get('key'),
            'room_id': room.get('room_id'),
            'room_label': room.get('room_label'),
            'category': room.get('category'),
            'room_type_id': room.get('room_type_id'),
            # `request` remains the real guest composition shown to managers/clients.
            # `pms_request` records the commercial occupancy actually used to obtain price.
            'request': actual_req,
            'pms_request': pms_req,
            'pricing_occupancy_adjustment': {
                'paid_children_on_base': pricing_occ['paid_children_on_base'],
                'paid_children_on_extra': pricing_occ['paid_children_on_extra'],
            },
            'response': resp,
            'stay_total_before_tourist_tax': _money_float(resp_before),
            'tourist_tax_total': _money_float(resp_tax),
            'stay_total': _money_float(resp_total),
        })

    # If the quote response reports live availability, reject a stale Operations allocation
    # that asks for more rooms of the category than HMS says are currently available.
    for rid, selected in selected_by_type.items():
        reported = available_by_type.get(rid) or []
        if reported and min(reported) < selected:
            raise RuntimeError(
                f'Live HMS quote показує лише {min(reported)} доступних номерів RoomTypeID {rid}, '
                f'а в allocation вибрано {selected}. Оновіть availability і перерахуйте.'
            )

    if len(currencies) > 1:
        raise RuntimeError(f'PMS Room Quote повернув різні валюти в одному прорахунку: {sorted(currencies)}')
    currency = next(iter(currencies), '')
    if not currency:
        raise RuntimeError('PMS Room Quote не повернув валюту для групового прорахунку.')
    booking_restrictions = _aggregate_booking_restrictions(restriction_items)
    booking_allowed = not any(bool(x.get('blocking')) for x in booking_restrictions)
    min_stay_values = [_ival(x.get('value'), 0) for x in booking_restrictions if x.get('type') == 'min_stay' and _ival(x.get('value'), 0) > 0]
    return {
        'ok': True,
        'rooms': rooms,
        'room_count': len(rooms),
        'unique_quote_requests': len(response_cache),
        'base_accommodation_total': _money_float(base_total),
        'stay_total_before_tourist_tax': _money_float(before_tax),
        'tourist_tax_total': _money_float(tax_total),
        # Commercial work always uses the HMS amount before tourist tax as the
        # immutable source price. stay_total is kept only for traceability.
        'stay_total': _money_float(stay_total),
        'hms_accommodation_total': _money_float(before_tax),
        'currency': currency,
        'source': ' + '.join(sorted(sources)) if sources else 'HMS/SERVIO Room Quote API',
        'generated_at': max(generated) if generated else _now(),
        'price_list_ids': sorted({_ival(x.get('response', {}).get('price_list_id'), 0) for x in rooms if _ival(x.get('response', {}).get('price_list_id'), 0) > 0}),
        'rate_plan_ids': sorted({_ival(x.get('response', {}).get('rate_plan_id'), 0) for x in rooms if _ival(x.get('response', {}).get('rate_plan_id'), 0) > 0}),
        'booking_restrictions': booking_restrictions,
        'booking_allowed': booking_allowed,
        'min_stay_required': max(min_stay_values) if min_stay_values else 0,
    }



def _booking_restriction_failure_message(
    restrictions: Iterable[Dict[str, Any]], *, arrival: str = '', departure: str = '', selected_nights: int = 0
) -> str:
    blocking = [dict(x) for x in restrictions if isinstance(x, dict) and bool(x.get('blocking'))]
    if not blocking:
        return ''
    min_items = [x for x in blocking if str(x.get('type') or '') == 'min_stay' and _ival(x.get('value'), 0) > 0]
    if min_items:
        required = max(_ival(x.get('value'), 0) for x in min_items)
        segment_items = [x for x in min_items if _ival(x.get('segment_nights'), 0) > 0]
        if segment_items:
            chosen = next((x for x in segment_items if _ival(x.get('value'), 0) == required), segment_items[0])
            msg = str(chosen.get('message') or '').strip()
            if msg:
                affected = chosen.get('affected_segments') if isinstance(chosen.get('affected_segments'), list) else []
                if len(affected) > 1:
                    msg += f' Аналогічне порушення ще у {len(affected) - 1} номер(ах).'
                return msg
        nights = max(0, _ival(selected_nights, 0))
        suffix = ''
        try:
            if arrival and required > 0:
                suggested = date.fromisoformat(arrival) + timedelta(days=required)
                suffix = f' Мінімальна дата виїзду для цього заїзду — {suggested.strftime("%d.%m.%Y")}.'
        except Exception:
            pass
        return f'Для вибраних дат діє мінімальний строк проживання — {required} ноч. Обрано {nights} ноч.{suffix}'
    messages: List[str] = []
    seen = set()
    for item in blocking:
        msg = str(item.get('message') or '').strip()
        if msg and msg not in seen:
            seen.add(msg); messages.append(msg)
    if messages:
        return ' '.join(messages[:3])
    return 'Вибрані дати або умови розміщення не відповідають чинним умовам бронювання.'


def _best_price_failure_message(diagnostics: Dict[str, Any], *, arrival: str, departure: str) -> str:
    try:
        nights = max(1, (date.fromisoformat(departure) - date.fromisoformat(arrival)).days)
    except Exception:
        nights = 0
    restriction_message = _booking_restriction_failure_message(
        diagnostics.get('restrictions') or [], arrival=arrival, departure=departure, selected_nights=nights
    )
    if restriction_message:
        return 'Розрахунок не виконано через умови бронювання. ' + restriction_message
    quote_errors = [str(x).strip() for x in diagnostics.get('quote_errors') or [] if str(x).strip()]
    if quote_errors:
        # Show the first concrete manager-safe reason. Repeated occupancy probes commonly
        # fail with the same reason, so a long list would only add noise.
        return 'Не вдалося отримати актуальну вартість: ' + quote_errors[0]
    return 'Не знайдено допустимого варіанта розміщення для заданих умов.'


def _best_price_room_options(
    rows: List[Dict[str, Any]], *, arrival: str, departure: str,
    adults: int, children: int, paid_children: int,
    price_list_id: int, rate_plan_id: int,
    response_cache: Dict[str, Dict[str, Any]],
    diagnostics: Optional[Dict[str, Any]] = None,
    allow_restricted_rates: bool = False,
) -> Dict[int, List[Dict[str, Any]]]:
    """Read live HMS cost for every occupancy pattern that can be used by the optimizer.

    The optimizer never derives price from category priority, ADR or a local tariff table.
    Every option below is a real Room Quote API result for the exact stay + occupancy.
    """
    out: Dict[int, List[Dict[str, Any]]] = {}
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.setdefault('restrictions', [])
    diagnostics.setdefault('quote_errors', [])
    for row in rows:
        if not row.get('configured') or _ival(row.get('available_rooms'), 0) <= 0:
            continue
        rid = _ival(row.get('room_type_id'), 0)
        cap = _ival(row.get('capacity_per_room'), 0, minimum=0)
        if rid <= 0 or cap <= 0:
            continue
        options: List[Dict[str, Any]] = []
        max_a = min(max(0, adults), cap)
        max_c = min(max(0, children), cap)
        # PMS Room Quote rejects occupied rooms with adults=0. Do not probe child-only
        # occupancies: they are not valid tariff candidates and previously surfaced as
        # the raw HTTP 400 `adults must be >= 1` error.
        for a in range(1, max_a + 1):
            for c in range(0, max_c + 1):
                occ = a + c
                if occ <= 0 or occ > cap:
                    continue
                # paid_children is a global count; each room may contain from 0 up to
                # its child count, but never more than the total paid children requested.
                max_p = min(c, max(0, paid_children))
                p_values = range(0, max_p + 1) if children > 0 else (0,)
                for p in p_values:
                    extra_beds = max(0, min(_ival(row.get('extra_capacity'), 0), occ - _ival(row.get('base_capacity'), 0)))
                    room = {
                        'key': f'opt_{rid}_{a}_{c}_{p}',
                        'room_type_id': rid,
                        'category': str(row.get('category') or ''),
                        'room_id': None,
                        'room_label': 'optimizer',
                        'position': 1,
                        'base_capacity': _ival(row.get('base_capacity'), 0),
                        'extra_capacity': _ival(row.get('extra_capacity'), 0),
                        'capacity_per_room': cap,
                        'room_capacity_rule': _ival(row.get('room_capacity_rule'), 0),
                        'bed_capacity_rule': _ival(row.get('bed_capacity_rule'), 0),
                        'adults': a,
                        'children': c,
                        'paid_children': p,
                        'extra_beds': extra_beds,
                        'resident_adults': 0,
                        'nonresident_adults': 0,
                        'tourist_tax_exempt_adults': 0,
                        'occupants': occ,
                    }
                    try:
                        q = _quote_room_plan(
                            arrival=arrival, departure=departure, room_plan=[room],
                            price_list_id=price_list_id, rate_plan_id=rate_plan_id,
                            response_cache=response_cache,
                        )
                    except Exception as exc:
                        # Keep the concrete reason instead of collapsing every rejected
                        # occupancy into a generic "no tariff" message.
                        manager_error = _pricing_error_for_manager(exc)
                        if manager_error and manager_error not in diagnostics['quote_errors']:
                            diagnostics['quote_errors'].append(manager_error)
                        continue
                    if q.get('booking_allowed') is False:
                        diagnostics['restrictions'].extend(q.get('booking_restrictions') or [])
                        if not allow_restricted_rates:
                            continue
                    options.append({
                        'room_type_id': rid,
                        'adults': a,
                        'children': c,
                        'paid_children': p,
                        'occupants': occ,
                        'extra_beds': extra_beds,
                        'portable_extra_beds': extra_beds if _uses_portable_standard_bed(rid) else 0,
                        'cost': _money_decimal(q.get('hms_accommodation_total')),
                    })
        if options:
            # Deterministic order makes equal-price plans stable between runs.
            options.sort(key=lambda x: (x['cost'], -x['occupants'], x['adults'], x['children'], x['paid_children']))
            out[rid] = options
    return out


def _best_price_count_plan(
    rows: List[Dict[str, Any]], *, arrival: str, departure: str,
    adults: int, children: int, paid_children: int,
    price_list_id: int, rate_plan_id: int,
    allow_restricted_rates: bool = False,
) -> Dict[str, Any]:
    """Exact bounded DP for the cheapest live-priced room/occupancy combination.

    The DP carries the number of portable Standard extra beds as a separate resource.
    This lets the optimizer distinguish a cheaper plan that needs 12 physical beds from
    a slightly different valid plan that stays within Riverwood's editable stock.
    """
    target = (max(0, adults), max(0, children), max(0, paid_children))
    if target[0] + target[1] <= 0:
        raise ValueError('Для пошуку найвигіднішого варіанту потрібен хоча б один гість.')
    if target[0] <= 0 and target[1] > 0:
        raise ValueError('Для тарифного прорахунку PMS потрібен щонайменше 1 дорослий; дитячий склад без дорослого не тарифікується.')
    shared_cache: Dict[str, Dict[str, Any]] = {}
    diagnostics: Dict[str, Any] = {'restrictions': [], 'quote_errors': []}
    option_map = _best_price_room_options(
        rows, arrival=arrival, departure=departure,
        adults=target[0], children=target[1], paid_children=target[2],
        price_list_id=price_list_id, rate_plan_id=rate_plan_id,
        response_cache=shared_cache, diagnostics=diagnostics,
        allow_restricted_rates=allow_restricted_rates,
    )
    if not option_map:
        raise ValueError(_best_price_failure_message(diagnostics, arrival=arrival, departure=departure))

    # state=(adults, children, paid_children, portable_standard_beds)
    # value=(cost, room_count, choices). We intentionally keep states above the current
    # stock too; if all exact placements need more beds, we can report the true minimum
    # required instead of a vague "no option" error.
    dp: Dict[Tuple[int, int, int, int], Tuple[Decimal, int, Tuple[Tuple[int, int, int, int, int], ...]]] = {
        (0, 0, 0, 0): (Decimal('0'), 0, tuple())
    }
    row_by_id = {_ival(r.get('room_type_id'), 0): r for r in rows}
    for rid, options in option_map.items():
        row = row_by_id[rid]
        slots = _ival(row.get('available_rooms'), 0, minimum=0)
        slots = min(slots, target[0] + target[1])
        for _slot in range(slots):
            nxt = dict(dp)
            for (sa, sc, sp, sb), (base_cost, used, choices) in dp.items():
                for opt in options:
                    na, nc, np = sa + opt['adults'], sc + opt['children'], sp + opt['paid_children']
                    if na > target[0] or nc > target[1] or np > target[2]:
                        continue
                    nb = sb + _ival(opt.get('portable_extra_beds'), 0, minimum=0)
                    if nb > target[0] + target[1]:
                        continue
                    state = (na, nc, np, nb)
                    cand_cost = base_cost + opt['cost']
                    cand_used = used + 1
                    cand_choices = choices + ((rid, opt['adults'], opt['children'], opt['paid_children'], opt['extra_beds']),)
                    old = nxt.get(state)
                    if old is None or cand_cost < old[0] or (cand_cost == old[0] and cand_used < old[1]):
                        nxt[state] = (cand_cost, cand_used, cand_choices)
            dp = nxt

    exact_candidates = [
        (state, value) for state, value in dp.items()
        if state[0] == target[0] and state[1] == target[1] and state[2] == target[2]
    ]
    if not exact_candidates:
        raise ValueError(_best_price_failure_message(diagnostics, arrival=arrival, departure=departure))

    portable_limit = _standard_extra_bed_pool()
    valid_candidates = [(state, value) for state, value in exact_candidates if state[3] <= portable_limit]
    if not valid_candidates:
        minimum_required = min(state[3] for state, _ in exact_candidates)
        raise ValueError(
            f'Для заданого розміщення потрібно щонайменше {minimum_required} переносних додаткових ліжок у стандартних номерах, '
            f'але фізично доступно {portable_limit}. Змініть розміщення або кількість гостей.'
        )

    final_state, final = min(valid_candidates, key=lambda item: (item[1][0], item[1][1], item[0][3]))
    choices = list(final[2])
    by_rid: Dict[int, List[Tuple[int, int, int, int]]] = {}
    for rid, a, c, p, extra in choices:
        by_rid.setdefault(rid, []).append((a, c, p, extra))
    allocation = {rid: len(items) for rid, items in by_rid.items()}
    summary = _allocation_summary(rows, allocation, target[0] + target[1], portable_extra_bed_pool=portable_limit)

    room_plan: List[Dict[str, Any]] = []
    for row in rows:
        rid = _ival(row.get('room_type_id'), 0)
        opts = by_rid.get(rid) or []
        room_ids = list(row.get('room_ids') or [])
        room_labels = list(row.get('room_labels') or [])
        for idx, (a, c, p, extra) in enumerate(opts):
            room_plan.append({
                'key': _room_plan_key(rid, idx + 1),
                'room_type_id': rid,
                'category': str(row.get('category') or ''),
                'room_id': room_ids[idx] if idx < len(room_ids) else None,
                'room_label': str(room_labels[idx]) if idx < len(room_labels) else (f'RoomID {room_ids[idx]}' if idx < len(room_ids) else f'#{idx + 1}'),
                'position': idx + 1,
                'base_capacity': _ival(row.get('base_capacity'), 0),
                'extra_capacity': _ival(row.get('extra_capacity'), 0),
                'capacity_per_room': _ival(row.get('capacity_per_room'), 0),
                'room_capacity_rule': _ival(row.get('room_capacity_rule'), 0),
                'bed_capacity_rule': _ival(row.get('bed_capacity_rule'), 0),
                'portable_standard_bed': bool(row.get('portable_standard_bed')),
                'adults': a,
                'children': c,
                'paid_children': p,
                'extra_beds': extra,
                'resident_adults': 0,
                'nonresident_adults': 0,
                'tourist_tax_exempt_adults': 0,
                'occupants': a + c,
            })
    _validate_room_plan(room_plan, target[0], target[1], target[2])
    pricing = _quote_room_plan(
        arrival=arrival, departure=departure, room_plan=room_plan,
        price_list_id=price_list_id, rate_plan_id=rate_plan_id,
        response_cache=shared_cache,
    )
    summary['standard_extra_beds_used'] = _portable_standard_bed_usage(room_plan)
    summary['standard_extra_bed_pool_limit'] = portable_limit
    return {
        'allocation': allocation,
        'summary': summary,
        'room_plan': room_plan,
        'pricing': pricing,
        'optimizer': {
            'mode': 'live_price_exact_count',
            'live_signatures': len(shared_cache),
            'candidate_room_types': len(option_map),
            'portable_standard_beds_used': final_state[3],
            'portable_standard_beds_limit': portable_limit,
        },
    }


def _best_price_plan_with_guest_list(
    rows: List[Dict[str, Any]], guests: List[Dict[str, Any]], *,
    arrival: str, departure: str, placement_mode: str,
    adults: int, children: int, paid_children: int,
    price_list_id: int, rate_plan_id: int,
) -> Dict[str, Any]:
    """Best-price mode for a concrete rooming list.

    Start from the exact count optimum.  If concrete roommate/separate-room constraints
    make that plan impossible, compare the regular business-safe alternatives and choose
    the cheapest feasible live-HMS plan.  Preferences never get relaxed silently.
    """
    attempts: List[Dict[str, Any]] = []
    best = None
    try:
        exact = _best_price_count_plan(
            rows, arrival=arrival, departure=departure,
            adults=adults, children=children, paid_children=paid_children,
            price_list_id=price_list_id, rate_plan_id=rate_plan_id,
        )
        plan, unassigned = _assign_guest_list_to_room_plan(exact['room_plan'], guests, placement_mode)
        if not unassigned:
            _validate_room_plan(plan, adults, children, paid_children)
            pricing = _quote_room_plan(
                arrival=arrival, departure=departure, room_plan=plan,
                price_list_id=price_list_id, rate_plan_id=rate_plan_id,
            )
            if pricing.get('booking_allowed') is False:
                attempts.append({'label': 'exact', 'restrictions': pricing.get('booking_restrictions') or []})
            else:
                exact['room_plan'] = plan
                exact['pricing'] = pricing
                exact['unassigned_guests'] = []
                exact['optimizer']['mode'] = 'live_price_rooming_feasible_search'
                exact['optimizer']['selected'] = 'count_optimum'
                score = (_money_decimal(pricing.get('hms_accommodation_total')), _ival(exact['summary'].get('used_rooms'), 0), _ival(exact['summary'].get('spare_places'), 0))
                best = (score, exact)
        else:
            attempts.append({'label': 'exact', 'unassigned': unassigned})
    except Exception as exc:
        attempts.append({'label': 'exact', 'error': str(exc)})

    # Feasible fallback candidates respect the existing placement business rules.
    candidates: List[Tuple[str, Dict[int, int]]] = []
    seen = set()
    for label, strat in [('placement_priority', 'priority'), ('fewest_rooms', 'fewest_rooms')]:
        alloc = _auto_allocate(rows, adults + children, strat, placement_mode)
        sig = tuple(sorted((k, v) for k, v in alloc.items() if v))
        if sig and sig not in seen:
            seen.add(sig); candidates.append((label, alloc))
    # Also try each configured category first, then normal priority.  This catches
    # common cases where VIP/separate-room constraints require a different room mix.
    base_candidates = [r for r in rows if r.get('configured') and _ival(r.get('available_rooms'), 0) > 0]
    for lead in base_candidates:
        ordered = [lead] + [r for r in base_candidates if r is not lead]
        pool_remaining = _standard_extra_bed_pool()
        remaining = adults + children
        alloc: Dict[int, int] = {}
        for row in ordered:
            cap = _ival(row.get('capacity_per_room'), 0)
            if cap <= 0 or remaining <= 0:
                continue
            available = _ival(row.get('available_rooms'), 0, minimum=0)
            base = _ival(row.get('base_capacity'), 0, minimum=0)
            extra = _ival(row.get('extra_capacity'), 0, minimum=0)
            portable = bool(row.get('portable_standard_bed'))
            def eff(n: int) -> int:
                return n * base + min(pool_remaining, n * extra) if portable and extra > 0 else n * cap
            use = available
            for n in range(1, available + 1):
                if eff(n) >= remaining:
                    use = n; break
            alloc[_ival(row.get('room_type_id'), 0)] = use
            if portable and extra > 0:
                base_capacity = use * base
                extra_needed = max(0, min(pool_remaining, use * extra, remaining - base_capacity))
                pool_remaining -= extra_needed
                placed = base_capacity + extra_needed
            else:
                placed = use * cap
            remaining = max(0, remaining - placed)
        sig = tuple(sorted((k, v) for k, v in alloc.items() if v))
        if remaining == 0 and sig and sig not in seen:
            seen.add(sig); candidates.append((f'lead_{lead.get("room_type_id")}', alloc))

    for label, allocation in candidates:
        summary = _allocation_summary(rows, allocation, adults + children)
        if not summary.get('fits'):
            continue
        plan = _build_room_plan(summary, adults, children, paid_children_total=paid_children, form=None)
        plan, unassigned = _assign_guest_list_to_room_plan(plan, guests, placement_mode)
        if unassigned:
            continue
        try:
            _validate_room_plan(plan, adults, children, paid_children)
            pricing = _quote_room_plan(
                arrival=arrival, departure=departure, room_plan=plan,
                price_list_id=price_list_id, rate_plan_id=rate_plan_id,
            )
            if pricing.get('booking_allowed') is False:
                attempts.append({'label': label, 'restrictions': pricing.get('booking_restrictions') or []})
                continue
        except Exception as exc:
            attempts.append({'label': label, 'error': _pricing_error_for_manager(exc)})
            continue
        score = (_money_decimal(pricing.get('hms_accommodation_total')), _ival(summary.get('used_rooms'), 0), _ival(summary.get('spare_places'), 0))
        if best is None or score < best[0]:
            best = (score, {
                'allocation': allocation, 'summary': summary, 'room_plan': plan,
                'pricing': pricing, 'unassigned_guests': [],
                'optimizer': {'mode': 'live_hms_rooming_feasible_search', 'candidate_plans': len(candidates) + (1 if best is not None else 0), 'selected': label},
            })
    if best is None:
        all_restrictions: List[Dict[str, Any]] = []
        for attempt in attempts:
            all_restrictions.extend(attempt.get('restrictions') or [])
        restriction_message = _booking_restriction_failure_message(
            _aggregate_booking_restrictions(all_restrictions),
            arrival=arrival, departure=departure,
            selected_nights=max(1, (date.fromisoformat(departure) - date.fromisoformat(arrival)).days),
        ) if all_restrictions else ''
        if restriction_message:
            raise ValueError('Розрахунок не виконано через умови бронювання. ' + restriction_message)
        for attempt in attempts:
            err = str(attempt.get('error') or '')
            if 'переносних додаткових ліжок' in err:
                raise ValueError(err)
        raise ValueError('Не знайдено варіанта, який одночасно виконує всі умови зі списку гостей, місткість номерів, умови бронювання та ліміт додаткових ліжок.')
    return best[1]

def _next_quote_number(conn) -> str:
    prefix = f"ACC-{date.today().strftime('%Y%m%d')}-"
    row = conn.execute('SELECT quote_number FROM accommodation_quotes WHERE quote_number LIKE ? ORDER BY quote_number DESC LIMIT 1', (prefix + '%',)).fetchone()
    seq = 1
    if row:
        try:
            seq = int(str(row['quote_number']).rsplit('-', 1)[-1]) + 1
        except Exception:
            seq = 1
    return f'{prefix}{seq:03d}'



def _calculate_varying_daily_group(
    *, schedule: List[Dict[str, Any]], strategy: str, price_list_id: int, rate_plan_id: int,
    cached_payloads: Optional[Dict[str, Dict[str, Any]]] = None, manual_mode: str = '',
    manual_day: str = '', form=None, force_live: bool = False,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], Dict[str, Any], List[str]]:
    """Calculate availability, allocation and price independently for every night."""
    day_results: List[Dict[str, Any]] = []
    cache_days: List[Dict[str, Any]] = []
    warnings: List[str] = []
    cached_payloads = cached_payloads or {}
    conn = _db()

    for day in schedule:
        day_date = str(day.get('date') or '')
        next_date = str(day.get('next_date') or '')
        guest_count = _ival(day.get('guest_count'), 0, minimum=0)
        if guest_count <= 0:
            day_results.append({
                **day,
                'rows': [],
                'summary': {'rows': [], 'used_rooms': 0, 'allocated_capacity': 0, 'shortage': 0, 'spare_places': 0, 'configured_capacity': 0, 'fits': True},
                'room_plan': [],
                'pricing': {'ok': True, 'rooms': [], 'hms_accommodation_total': 0, 'stay_total_before_tourist_tax': 0, 'tourist_tax_total': 0, 'stay_total': 0, 'currency': 'UAH', 'booking_restrictions': [], 'booking_allowed': True},
                'pricing_daily': {'days': [{'date': day_date, 'next_date': next_date, 'date_label': day.get('date_label'), 'next_date_label': day.get('next_date_label'), 'lines': [], 'total': 0, 'base_total': 0, 'extra_total': 0}], 'period_extras': [], 'has_daily_rates': True, 'currency': 'UAH'},
                'room_options': [],
            })
            continue

        payload = None if force_live else cached_payloads.get(day_date)
        day_warnings: List[str] = []
        if payload is None:
            raw = _request_pms_live(day_date, next_date)
            payload, day_warnings = _validate_payload(raw, day_date, next_date)
            _sync_categories_from_payload(conn, payload)
        cache_days.append({'date': day_date, 'next_date': next_date, 'payload': payload})
        warnings.extend([f'{day.get("date_label")}: {x}' for x in day_warnings])

        rows = _category_rows(payload, str(day.get('placement_mode') or 'standard'), bool(day.get('include_extra')))
        adults = _ival(day.get('adults'), 0, minimum=0)
        children = _ival(day.get('children'), 0, minimum=0)
        paid_children = _ival(day.get('paid_children'), 0, minimum=0)

        if manual_mode:
            if form is None:
                raise RuntimeError('Ручний денний план не передано у розрахунок.')
            if manual_mode == 'selected_rooms':
                room_plan = _manual_selected_room_plan_from_form(
                    day_date=day_date, rows=rows, adults=adults, children=children, paid_children=paid_children, form=form,
                )
            elif manual_mode == 'refill' and (not manual_day or manual_day == day_date):
                room_plan = _refill_day_around_locked_rooms(
                    day_date=day_date, rows=rows, adults=adults, children=children, paid_children=paid_children,
                    placement_mode=str(day.get('placement_mode') or 'standard'), strategy=strategy, form=form,
                )
            else:
                room_plan = _manual_day_room_plan_from_form(
                    day_date=day_date, rows=rows, adults=adults, children=children, paid_children=paid_children,
                    form=form, require_full=True,
                )
            summary = _manual_summary_from_room_plan(rows, room_plan, guest_count)
            allocation = {}
            for r in room_plan:
                rid = _ival(r.get('room_type_id'), 0)
                allocation[rid] = allocation.get(rid, 0) + 1
            pricing = _quote_room_plan(
                arrival=day_date, departure=next_date, room_plan=room_plan,
                price_list_id=price_list_id, rate_plan_id=rate_plan_id,
            )
            optimizer = {'mode': 'manual_selected_rooms' if manual_mode == 'selected_rooms' else ('manual_daily' if manual_mode != 'refill' else 'manual_locked_refill')}
        elif strategy == 'best_price':
            optimized = _best_price_count_plan(
                rows, arrival=day_date, departure=next_date,
                adults=adults, children=children, paid_children=paid_children,
                price_list_id=price_list_id, rate_plan_id=rate_plan_id,
                allow_restricted_rates=True,
            )
            allocation = optimized['allocation']
            summary = optimized['summary']
            room_plan = optimized['room_plan']
            pricing = optimized['pricing']
            optimizer = optimized.get('optimizer') or {}
        else:
            allocation = _auto_allocate(rows, guest_count, strategy, str(day.get('placement_mode') or 'standard'))
            summary = _allocation_summary(rows, allocation, guest_count)
            if not summary.get('fits'):
                raise ValueError(f'{day.get("date_label")}: {_allocation_failure_message(summary)}')
            room_plan = _build_room_plan(summary, adults, children, paid_children_total=paid_children, form=None)
            _validate_room_plan(room_plan, adults, children, paid_children)
            pricing = _quote_room_plan(
                arrival=day_date, departure=next_date, room_plan=room_plan,
                price_list_id=price_list_id, rate_plan_id=rate_plan_id,
            )
            optimizer = {}

        statement = _pricing_daily_statement(pricing, day_date, next_date)
        if not statement.get('days'):
            raise RuntimeError(f'{day.get("date_label")}: не отримано точну денну вартість.')
        day_results.append({
            **day, 'rows': rows, 'allocation': allocation, 'summary': summary,
            'room_plan': room_plan, 'pricing': pricing, 'pricing_daily': statement,
            'optimizer': optimizer, 'room_options': _daily_room_options(rows),
            'available_whole_stay': _ival(payload.get('available_whole_stay'), 0, minimum=0),
            'active_rooms_whole_stay': _ival(payload.get('active_rooms_whole_stay'), 0, minimum=0),
        })

    conn.commit()
    restrictions = _daily_booking_restrictions(day_results)
    composite_pricing = _compose_daily_pricing(day_results, restrictions)
    cache_payload = {
        '_daily_mode': True,
        'nights': len(schedule),
        'source': 'PMS Availability Sidecar',
        'days': cache_days,
        'daily_schedule': schedule,
    }
    return day_results, composite_pricing, cache_payload, warnings


def _daily_allocation_snapshot(day_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    days = []
    max_rooms = max_capacity = max_spare = 0
    for day in day_results:
        summary = day.get('summary') or {}
        rows_out = []
        for r in summary.get('rows') or []:
            if not isinstance(r, dict):
                continue
            rows_out.append({
                'room_type_id': r.get('room_type_id'), 'category': r.get('category'), 'guest_label': r.get('guest_label'),
                'available_rooms': r.get('available_rooms'), 'allocated_rooms': r.get('allocated_rooms'),
                'base_capacity': r.get('base_capacity'), 'extra_capacity': r.get('extra_capacity'),
                'capacity_per_room': r.get('capacity_per_room'), 'allocated_capacity': r.get('allocated_capacity'),
                'room_ids': r.get('room_ids') or [], 'room_labels': r.get('room_labels') or [],
            })
        max_rooms = max(max_rooms, _ival(summary.get('used_rooms'), 0))
        max_capacity = max(max_capacity, _ival(summary.get('allocated_capacity'), 0))
        max_spare = max(max_spare, _ival(summary.get('spare_places'), 0))
        days.append({
            'date': day.get('date'), 'next_date': day.get('next_date'), 'date_label': day.get('date_label'),
            'next_date_label': day.get('next_date_label'), 'adults': day.get('adults'), 'children': day.get('children'),
            'paid_children': day.get('paid_children'), 'guest_count': day.get('guest_count'), 'placement_mode': day.get('placement_mode'),
            'placement_label': day.get('placement_label'), 'include_extra': day.get('include_extra'),
            'rows': rows_out, 'used_rooms': summary.get('used_rooms'), 'allocated_capacity': summary.get('allocated_capacity'),
            'shortage': summary.get('shortage'), 'spare_places': summary.get('spare_places'),
            'standard_extra_beds_used': _portable_standard_bed_usage(day.get('room_plan') or []),
        })
    return {'daily_mode': True, 'days': days, 'used_rooms': max_rooms, 'allocated_capacity': max_capacity, 'shortage': 0, 'spare_places': max_spare}



def _manual_room_change_summary(previous_occupancy_json: Any, new_occupancy_daily: List[Dict[str, Any]]) -> str:
    """Compact audit text describing physical room replacements per night."""
    try:
        previous = json.loads(previous_occupancy_json or '[]') if not isinstance(previous_occupancy_json, list) else previous_occupancy_json
        if not isinstance(previous, list):
            previous = []
    except Exception:
        previous = []

    def by_day(items: List[Dict[str, Any]]) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for day in items or []:
            if not isinstance(day, dict):
                continue
            d = str(day.get('date') or '')
            labels: List[str] = []
            for room in day.get('room_plan') or []:
                if not isinstance(room, dict):
                    continue
                label = str(room.get('room_label') or room.get('room_id') or '').strip()
                if label:
                    labels.append(label)
            out[d] = labels
        return out

    old_map = by_day(previous)
    new_map = by_day(new_occupancy_daily)
    parts: List[str] = []
    for d in sorted(set(old_map) | set(new_map)):
        old = list(old_map.get(d) or [])
        new = list(new_map.get(d) or [])
        removed = [x for x in old if x not in new]
        added = [x for x in new if x not in old]
        while removed and added:
            parts.append(f'{d}: {removed.pop(0)}->{added.pop(0)}')
        for x in removed:
            parts.append(f'{d}: -{x}')
        for x in added:
            parts.append(f'{d}: +{x}')
    if not parts:
        return 'фізичні номери без змін'
    if len(parts) > 12:
        return '; '.join(parts[:12]) + f'; +ще {len(parts)-12} змін'
    return '; '.join(parts)


def _quote_edit_form_values(row: Any) -> Dict[str, Any]:
    q = _row_dict(row)
    try:
        daily_plan = json.loads(q.get('daily_plan_json') or '[]')
        if not isinstance(daily_plan, list):
            daily_plan = []
    except Exception:
        daily_plan = []
    if not daily_plan:
        try:
            a, _d, nights = _parse_dates(str(q.get('arrival') or ''), str(q.get('departure') or ''))
            for idx in range(nights):
                day = a + timedelta(days=idx)
                next_day = day + timedelta(days=1)
                daily_plan.append({
                    'date': day.isoformat(),
                    'next_date': next_day.isoformat(),
                    'date_label': day.strftime('%d.%m.%Y'),
                    'next_date_label': next_day.strftime('%d.%m.%Y'),
                    'adults': _ival(q.get('adults'), 0, minimum=0),
                    'children': _ival(q.get('children'), 0, minimum=0),
                    'paid_children': 0,
                    'guest_count': _ival(q.get('guest_count'), 0, minimum=0),
                    'placement_mode': str(q.get('placement_mode') or 'standard'),
                    'placement_label': PLACEMENT_LABELS.get(str(q.get('placement_mode') or 'standard'), str(q.get('placement_mode') or 'standard')),
                    'include_extra': _ival(q.get('include_extra'), 0, minimum=0),
                })
        except Exception:
            daily_plan = []
    paid_children = max((_ival(x.get('paid_children'), 0, minimum=0) for x in daily_plan if isinstance(x, dict)), default=0)
    return {
        'arrival': str(q.get('arrival') or ''),
        'departure': str(q.get('departure') or ''),
        'guest_count': _ival(q.get('guest_count'), 0, minimum=0),
        'adults': _ival(q.get('adults'), 0, minimum=0),
        'children': _ival(q.get('children'), 0, minimum=0),
        'paid_children': paid_children,
        'placement_mode': str(q.get('placement_mode') or 'standard'),
        'include_extra': _ival(q.get('include_extra'), 0, minimum=0),
        'early_checkin': _ival(q.get('early_checkin'), 0, minimum=0),
        'late_checkout': _ival(q.get('late_checkout'), 0, minimum=0),
        'strategy': str(q.get('strategy') or 'priority'),
        'capacity_probe_mode': 'max',
        'client_name': str(q.get('client_name') or ''),
        'title': str(q.get('title') or ''),
        'discount_percent': f"{_money_decimal(q.get('commercial_discount_percent')):.2f}",
        'commercial_note': str(q.get('commercial_note') or ''),
        'manager_note': str(q.get('manager_note') or ''),
        'guest_note': str(q.get('guest_note') or ''),
        'guest_input_mode': str(q.get('guest_input_mode') or 'count'),
        'guest_list': _guest_list_from_json(q.get('guest_list_json') or '[]'),
        'guest_list_source': str(q.get('guest_list_source') or ''),
        'daily_composition': daily_plan,
        'edit_quote_id': str(q.get('quote_id') or ''),
        'edit_quote_number': str(q.get('quote_number') or ''),
        'edit_revision_no': _ival(q.get('revision_no'), 1, minimum=1),
        'edit_day': '',
        'edit_current_commercial_total': _money_float(_money_decimal(q.get('commercial_total'))),
        'edit_current_currency': str(q.get('currency') or 'UAH'),
    }

def _render_calculator(*, result: Optional[Dict[str, Any]] = None, error: str = '', form_values: Optional[Dict[str, Any]] = None, capacity_probe: Optional[Dict[str, Any]] = None):
    ensure_accommodation_schema()
    info = _pms_connection_info()
    quote_info = _pms_quote_connection_info()
    conn = _db()
    saved_count = conn.execute('SELECT COUNT(*) AS c FROM accommodation_quotes').fetchone()['c']
    rules = list(_rules_map(conn).values())
    configured_standard = sum(1 for r in rules if _ival(r.get('is_enabled'), 0) and _ival(r.get('standard_capacity'), 0) > 0)
    configured_room = sum(1 for r in rules if _ival(r.get('is_enabled'), 0) and _ival(r.get('room_capacity'), 0) > 0)
    configured_bed = sum(1 for r in rules if _ival(r.get('is_enabled'), 0) and _ival(r.get('bed_capacity'), 0) > 0)
    defaults = {
        'arrival': date.today().isoformat(),
        'departure': (date.today() + timedelta(days=1)).isoformat(),
        'guest_count': 0,
        'adults': 0,
        'children': 0,
        'paid_children': 0,
        'placement_mode': 'standard',
        'include_extra': 0,
        'early_checkin': 0,
        'late_checkout': 0,
        'strategy': 'priority',
        'capacity_probe_mode': 'max',
        'client_name': '',
        'title': '',
        'discount_percent': '0',
        'commercial_note': '',
        'manager_note': '',
        'guest_note': '',
        'guest_input_mode': 'count',
        'guest_list': [],
        'guest_list_source': '',
        'daily_composition': [],
        'edit_quote_id': '',
        'edit_quote_number': '',
        'edit_revision_no': 0,
        'edit_day': '',
        'edit_current_commercial_total': 0,
        'edit_current_currency': 'UAH',
    }
    if form_values:
        defaults.update(form_values)
    return render_template(
        'accommodation_calculator.html',
        title='Прорахунок проживання',
        result=result,
        capacity_probe=capacity_probe,
        error=error,
        form_values=defaults,
        placement_labels=PLACEMENT_LABELS,
        strategy_labels=STRATEGY_LABELS,
        capacity_probe_labels=CAPACITY_PROBE_LABELS,
        connection={k: v for k, v in info.items() if k not in ('token',)},
        quote_connection={k: v for k, v in quote_info.items() if k not in ('token',)},
        saved_count=saved_count,
        configured_standard=configured_standard,
        configured_room=configured_room,
        configured_bed=configured_bed,
        standard_extra_bed_pool=_standard_extra_bed_pool(conn),
        guest_input_mode_labels=GUEST_INPUT_MODE_LABELS,
        guest_type_labels=GUEST_TYPE_LABELS,
        guest_preference_labels=GUEST_PREFERENCE_LABELS,
        money_fmt=_money_text,
    )


@bp.route('/accommodation-calculator', methods=['GET', 'POST'])
def calculator_page():
    ensure_accommodation_schema()
    if request.method == 'GET':
        edit_quote_id = (request.args.get('edit_quote_id') or '').strip()
        if edit_quote_id:
            row = _db().execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (edit_quote_id,)).fetchone()
            if not row:
                abort(404)
            values = _quote_edit_form_values(row)
            values['edit_day'] = (request.args.get('edit_day') or '').strip()
            try:
                saved_result = _saved_quote_result_for_edit(row)
                return _render_calculator(result=saved_result, form_values=values)
            except Exception as exc:
                return _render_calculator(
                    form_values=values,
                    error='Не вдалося відкрити збережені фізичні номери для ручного редагування: ' + _pricing_error_for_manager(exc),
                )
        return _render_calculator()

    action = (request.form.get('action') or 'calculate').strip()
    if action in ('quote', 'save_priced'):
        action = 'save' if action == 'save_priced' else 'manual'

    arrival = (request.form.get('arrival') or '').strip()
    departure = (request.form.get('departure') or '').strip()
    guest_input_mode = (request.form.get('guest_input_mode') or 'count').strip()
    if guest_input_mode not in GUEST_INPUT_MODE_LABELS:
        guest_input_mode = 'count'
    guest_list = _guest_list_from_form()
    guest_list_source = (request.form.get('guest_list_source') or '').strip()
    uploaded = request.files.get('guest_file')
    if uploaded and getattr(uploaded, 'filename', ''):
        try:
            imported_guests = _guest_list_from_upload(uploaded)
            guest_list = _merge_guest_lists(guest_list, imported_guests)
            guest_list_source = f'file:{Path(str(uploaded.filename)).name}'
        except Exception as import_exc:
            guest_list_source = f'error:{import_exc}'

    adults_raw = request.form.get('adults')
    children_raw = request.form.get('children')
    if guest_input_mode == 'list' and guest_list and not guest_list_source.startswith('error:'):
        adults, children, paid_children = _guest_counts(guest_list)
    else:
        if adults_raw in (None, '') and children_raw in (None, ''):
            adults = _ival(request.form.get('guest_count'), 0, minimum=0)
            children = 0
        else:
            adults = _ival(adults_raw, 0, minimum=0)
            children = _ival(children_raw, 0, minimum=0)
        paid_children = _ival(request.form.get('paid_children'), 0, minimum=0)
    guests = adults + children
    placement_mode = (request.form.get('placement_mode') or 'standard').strip()
    include_extra = _bool_form('include_extra')
    early_checkin = _bool_form('early_checkin')
    late_checkout = _bool_form('late_checkout')
    strategy = (request.form.get('strategy') or 'priority').strip()
    capacity_probe_mode = (request.form.get('capacity_probe_mode') or 'max').strip()
    if capacity_probe_mode not in CAPACITY_PROBE_LABELS:
        capacity_probe_mode = 'max'
    discount_percent = _percent_decimal(request.form.get('discount_percent'))
    price_list_id = _default_quote_price_list_id()
    rate_plan_id, rate_plan_resolution = _effective_quote_rate_plan_id(price_list_id)
    edit_quote_id = (request.form.get('edit_quote_id') or '').strip()
    edit_quote_row = None
    if edit_quote_id:
        edit_quote_row = _db().execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (edit_quote_id,)).fetchone()
        if not edit_quote_row:
            return _render_calculator(error='Пропозицію для редагування не знайдено.')

    form_values = {
        'arrival': arrival,
        'departure': departure,
        'guest_count': guests,
        'adults': adults,
        'children': children,
        'paid_children': paid_children,
        'placement_mode': placement_mode,
        'include_extra': include_extra,
        'early_checkin': early_checkin,
        'late_checkout': late_checkout,
        'strategy': strategy,
        'capacity_probe_mode': capacity_probe_mode,
        'client_name': (request.form.get('client_name') or '').strip(),
        'title': (request.form.get('title') or '').strip(),
        'discount_percent': f'{discount_percent:.2f}',
        'commercial_note': (request.form.get('commercial_note') or '').strip(),
        'manager_note': (request.form.get('manager_note') or '').strip(),
        'guest_note': (request.form.get('guest_note') or '').strip(),
        'guest_input_mode': guest_input_mode,
        'guest_list': guest_list,
        'guest_list_source': guest_list_source,
        'daily_composition': [],
        'edit_quote_id': edit_quote_id,
        'edit_quote_number': str(edit_quote_row['quote_number']) if edit_quote_row else '',
        'edit_revision_no': _ival(edit_quote_row['revision_no'], 1, minimum=1) if edit_quote_row else 0,
        'edit_day': (request.form.get('edit_day') or '').strip(),
        'edit_current_commercial_total': _money_float(_money_decimal(edit_quote_row['commercial_total'])) if edit_quote_row else 0,
        'edit_current_currency': str(edit_quote_row['currency'] or 'UAH') if edit_quote_row else 'UAH',
        'rate_plan_resolution': rate_plan_resolution,
    }

    try:
        if guest_list_source.startswith('error:'):
            raise ValueError(guest_list_source.split(':', 1)[1])
        _, _, nights = _parse_dates(arrival, departure)
        if str(request.form.get('capacity_probe') or '').strip() == '1':
            probe = _capacity_probe_for_period(arrival, departure, capacity_probe_mode)
            return _render_calculator(form_values=form_values, capacity_probe=probe)
        if guest_input_mode == 'list' and not guest_list:
            raise ValueError('Для розрахунку за списком додайте гостей вручну або імпортуйте Excel/CSV.')
        if guests <= 0:
            raise ValueError('Кількість гостей повинна бути більше нуля.')
        if paid_children > children:
            raise ValueError('Кількість платних дітей не може перевищувати загальну кількість дітей.')
        if placement_mode not in PLACEMENT_LABELS:
            raise ValueError('Невідомий режим розміщення.')
        if strategy not in STRATEGY_LABELS:
            strategy = 'priority'

        daily_schedule = _daily_schedule_from_form(
            arrival, departure, adults=adults, children=children, paid_children=paid_children,
            placement_mode=placement_mode, include_extra=include_extra, form=request.form,
        )
        form_values['daily_composition'] = daily_schedule
        daily_varies = _daily_schedule_varies(daily_schedule)
        # v5.305: always use the per-night calculation path, even for a one-night stay.
        # The per-night path is the canonical path that exposes the exact physical-room
        # manual editor (add / remove / replace room + recalculate).  Previously a one-night
        # stay fell back to the legacy category-count editor, which effectively removed the
        # manager's physical-room corrections from the main calculator.
        daily_mode = True
        manual_plan_active = str(request.form.get('manual_plan_active') or '').strip() in ('1','true','yes','on')
        manual_day = (request.form.get('manual_day') or '').strip()
        manual_mode = ''
        if action == 'daily_refill':
            manual_mode = 'refill'
        elif action == 'daily_manual_rooms' or (action == 'save' and manual_plan_active and str(request.form.get('manual_editor_mode') or '') == 'selected_rooms'):
            manual_mode = 'selected_rooms'
        elif action == 'daily_manual' or (action == 'save' and manual_plan_active):
            manual_mode = 'manual'

        cache_id = (request.form.get('cache_id') or '').strip()
        source_mode = 'live'
        warnings: List[str] = []

        if daily_mode:
            cached_payloads: Dict[str, Dict[str, Any]] = {}
            cache_obj = None
            use_cached_daily = action in ('manual', 'save') and not manual_mode
            if use_cached_daily:
                cache_obj = _load_cache(cache_id)
                if not cache_obj or not isinstance(cache_obj.get('payload'), dict) or not cache_obj['payload'].get('_daily_mode'):
                    raise ValueError('Денний розрахунок вже недоступний. Натисніть «Розрахувати проживання» ще раз.')
                if cache_obj['row']['arrival'] != arrival or cache_obj['row']['departure'] != departure:
                    raise ValueError('Дати змінилися. Натисніть «Розрахувати проживання» ще раз.')
                for item in cache_obj['payload'].get('days') or []:
                    if isinstance(item, dict) and isinstance(item.get('payload'), dict):
                        cached_payloads[str(item.get('date') or '')] = item['payload']

            day_results, pricing, cache_payload, day_warnings = _calculate_varying_daily_group(
                schedule=daily_schedule,
                strategy=strategy,
                price_list_id=price_list_id,
                rate_plan_id=rate_plan_id,
                cached_payloads=cached_payloads,
                manual_mode=manual_mode, manual_day=manual_day, form=request.form,
                force_live=bool(manual_mode),
            )
            warnings.extend(day_warnings)
            if not use_cached_daily:
                cache_id = _save_cache(cache_payload, arrival, departure, warnings, is_live=True)
            allocation_snapshot = _daily_allocation_snapshot(day_results)
            max_guests = max((_ival(x.get('guest_count'), 0) for x in daily_schedule), default=0)
            max_adults = max((_ival(x.get('adults'), 0) for x in daily_schedule), default=0)
            max_children = max((_ival(x.get('children'), 0) for x in daily_schedule), default=0)
            max_paid_children = max((_ival(x.get('paid_children'), 0) for x in daily_schedule), default=0)
            max_used_rooms = max((_ival((x.get('summary') or {}).get('used_rooms'), 0) for x in day_results), default=0)
            max_capacity = max((_ival((x.get('summary') or {}).get('allocated_capacity'), 0) for x in day_results), default=0)
            max_spare = max((_ival((x.get('summary') or {}).get('spare_places'), 0) for x in day_results), default=0)
            min_available = min((_ival(x.get('available_whole_stay'), 0) for x in day_results if _ival(x.get('guest_count'), 0) > 0), default=0)
            pricing_daily = _pricing_daily_statement(pricing, arrival, departure)
            first_plan = list((day_results[0].get('room_plan') or [])) if day_results else []
            last_plan = list((day_results[-1].get('room_plan') or [])) if day_results else []
            stay_time = _stay_time_availability_for_plans(
                arrival=arrival, departure=departure, first_room_plan=first_plan, last_room_plan=last_plan,
                request_early_all=bool(early_checkin), request_late_all=bool(late_checkout), strict_explicit=bool(manual_mode),
            )
            warnings.extend(stay_time.get('warnings') or [])
            pricing, pricing_daily = _apply_stay_time_surcharges(
                pricing, pricing_daily,
                early_checkin=bool(early_checkin), late_checkout=bool(late_checkout),
                early_room_labels=stay_time.get('early_room_labels') or [],
                late_room_labels=stay_time.get('late_room_labels') or [],
                availability_meta=stay_time,
            )
            pricing.update(_tourist_tax_estimate(daily_schedule))
            result = {
                'cache_id': cache_id,
                'arrival': arrival,
                'departure': departure,
                'nights': nights,
                'guest_count': max_guests,
                'adults': max_adults,
                'children': max_children,
                'paid_children': max_paid_children,
                'guest_input_mode': guest_input_mode,
                'guest_list': guest_list,
                'guest_list_source': guest_list_source,
                'unassigned_guests': [],
                'placement_mode': placement_mode,
                'placement_label': 'Змінюється по днях',
                'include_extra': include_extra,
                'early_checkin': early_checkin,
                'late_checkout': late_checkout,
                'strategy': strategy,
                'strategy_label': STRATEGY_LABELS.get(strategy, strategy),
                'price_list_id': price_list_id,
                'rate_plan_id': (pricing.get('rate_plan_ids') or [rate_plan_id])[0] if len(pricing.get('rate_plan_ids') or []) == 1 else rate_plan_id,
                'rate_plan_resolution': rate_plan_resolution,
                'available_whole_stay': min_available,
                'active_rooms_whole_stay': min_available,
                'occupied_rooms_any_overlap': 0,
                'source': str(pricing.get('source') or 'Система бронювання'),
                'generated_at': str(pricing.get('generated_at') or ''),
                'source_mode': 'live',
                'warnings': warnings,
                'room_plan': [],
                'rows': [],
                'pricing': pricing,
                'pricing_breakdown': [],
                'pricing_daily': pricing_daily,
                'pricing_error': '',
                'commercial': _commercial_terms(pricing.get('commercial_accommodation_total', pricing['hms_accommodation_total']), discount_percent),
                'booking_restrictions': list(pricing.get('booking_restrictions') or []),
                'booking_allowed': bool(pricing.get('booking_allowed', True)),
                'optimizer': {'mode': 'daily_varying_group'},
                'standard_extra_beds_used': max((_portable_standard_bed_usage(x.get('room_plan') or []) for x in day_results), default=0),
                'standard_extra_bed_pool_limit': _standard_extra_bed_pool(),
                'placement_error': '',
                'used_rooms': max_used_rooms,
                'allocated_capacity': max_capacity,
                'configured_capacity': max_capacity,
                'placed_guests': max_guests,
                'shortage': 0,
                'spare_places': max_spare,
                'fits': bool(pricing.get('booking_allowed', True)),
                'daily_mode': True,
                'daily_composition': daily_schedule,
                'daily_calculations': day_results,
                'allocation_snapshot': allocation_snapshot,
                'manual_plan_active': bool(manual_mode),
                'manual_day': manual_day or form_values.get('edit_day') or '',
            }
            if edit_quote_row and result.get('commercial'):
                previous_total = _money_decimal(edit_quote_row['commercial_total'])
                new_total = _money_decimal(result['commercial'].get('commercial_total'))
                result['previous_commercial_total'] = _money_float(previous_total)
                result['commercial_delta'] = _money_float(new_total - previous_total)

            if action == 'save':
                if result.get('booking_allowed') is False:
                    result['pricing_error'] = 'Пропозицію не збережено: ' + (
                        _booking_restriction_failure_message(
                            result.get('booking_restrictions') or [],
                            arrival=arrival,
                            departure=departure,
                            selected_nights=nights,
                        ) or 'вибрані умови бронювання не виконані.'
                    )
                    return _render_calculator(result=result, form_values=form_values)

                conn = _db()
                commercial = result['commercial']
                occupancy_daily = [
                    {
                        'date': x.get('date'),
                        'next_date': x.get('next_date'),
                        'room_plan': x.get('room_plan') or [],
                    }
                    for x in day_results
                ]
                cache_for_save = cache_obj or _load_cache(cache_id)
                quote_data = {
                    'client_name': form_values['client_name'],
                    'title': form_values['title'],
                    'arrival': arrival,
                    'departure': departure,
                    'nights': nights,
                    'guest_count': max_guests,
                    'placement_mode': placement_mode,
                    'include_extra': include_extra,
                    'early_checkin': early_checkin,
                    'late_checkout': late_checkout,
                    'strategy': strategy,
                    'availability_source': result['source'],
                    'availability_fetched_at': cache_for_save['row']['fetched_at'] if cache_for_save else _now(),
                    'availability_json': json.dumps(cache_payload, ensure_ascii=False, separators=(',', ':')),
                    'allocation_json': json.dumps(allocation_snapshot, ensure_ascii=False, separators=(',', ':')),
                    'available_whole_stay': result['available_whole_stay'],
                    'configured_capacity': max_capacity,
                    'placed_guests': max_guests,
                    'shortage': 0,
                    'spare_places': max_spare,
                    'manager_note': form_values['manager_note'],
                    'guest_note': form_values['guest_note'],
                    'tariff_status': 'live_hms',
                    'adults': max_adults,
                    'children': max_children,
                    'occupancy_json': json.dumps(occupancy_daily, ensure_ascii=False, separators=(',', ':')),
                    'pricing_json': json.dumps(pricing, ensure_ascii=False, separators=(',', ':')),
                    'pricing_source': str(pricing.get('source') or ''),
                    'pricing_generated_at': str(pricing.get('generated_at') or '') or None,
                    'price_list_id': price_list_id or None,
                    'rate_plan_id': _ival(result.get('rate_plan_id'), 0, minimum=0) or None,
                    'include_tourist_tax': 0,
                    'stay_total_before_tourist_tax': pricing.get('stay_total_before_tourist_tax'),
                    'tourist_tax_total': pricing.get('tourist_tax_total'),
                    'stay_total': pricing.get('stay_total'),
                    'currency': str(pricing.get('currency') or ''),
                    'commercial_discount_percent': commercial['discount_percent'],
                    'commercial_discount_amount': commercial['discount_amount'],
                    'commercial_total': commercial['commercial_total'],
                    'commercial_note': form_values['commercial_note'],
                    'guest_input_mode': guest_input_mode,
                    'guest_list_json': json.dumps(guest_list, ensure_ascii=False, separators=(',', ':')),
                    'guest_list_source': guest_list_source,
                    'daily_plan_json': _daily_schedule_json(daily_schedule),
                }
                manual_change_summary = _manual_room_change_summary(
                    edit_quote_row['occupancy_json'] if edit_quote_row else '[]', occupancy_daily
                ) if manual_mode else ''
                quote_id, quote_number, revision_no = _persist_quote_version(
                    conn, quote_data, edit_quote_id=edit_quote_id,
                    revision_kind='manual_recalculation' if manual_mode else 'recalculation'
                )
                conn.commit()
                _audit(
                    'accommodation_quote', quote_id, 'recalculated' if edit_quote_id else 'created',
                    new_value=f'{quote_number}; v{revision_no}',
                    reason=(
                        f'{arrival}..{departure}; daily room plan; manual={bool(manual_mode)}; max_guests={max_guests}; discount={commercial["discount_percent"]}%'
                        + (f'; room_changes={manual_change_summary}' if manual_change_summary else '')
                    )
                )
                if edit_quote_id:
                    flash(f'Пропозицію {quote_number} перераховано та збережено як версію {revision_no}. Попередня версія залишилась в історії.', 'success')
                else:
                    flash(f'Пропозицію {quote_number} збережено. Склад групи та розрахунок зафіксовані окремо по кожній ночі.', 'success')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

            return _render_calculator(result=result, form_values=form_values)

        if action in ('manual', 'save'):
            cache_obj = _load_cache(cache_id)
            if not cache_obj:
                raise ValueError('Live availability за цим розрахунком вже недоступна. Натисніть «Розрахувати проживання» ще раз.')
            if cache_obj['row']['arrival'] != arrival or cache_obj['row']['departure'] != departure:
                raise ValueError('Дати змінилися. Натисніть «Розрахувати проживання» ще раз.')
            payload = cache_obj['payload']
            warnings = [x for x in str(cache_obj['row']['warning'] or '').splitlines() if x]
            source_mode = 'live' if int(cache_obj['row']['is_live'] or 0) else 'cache'
        else:
            try:
                raw_payload = _request_pms_live(arrival, departure)
                payload, warnings = _validate_payload(raw_payload, arrival, departure)
                conn = _db()
                _sync_categories_from_payload(conn, payload)
                cache_id = _save_cache(payload, arrival, departure, warnings, is_live=True)
                source_mode = 'live'
            except Exception as live_exc:
                cached = _last_good_cache(arrival, departure)
                if not cached:
                    raise
                payload = cached['payload']
                warnings = [f'Live PMS тимчасово недоступний: {live_exc}. Показана остання успішна наявність від {cached["row"]["fetched_at"]}; ціна не розраховується зі snapshot.']
                cache_id = _save_cache(payload, arrival, departure, warnings, is_live=False)
                source_mode = 'cache'

        rows = _category_rows(payload, placement_mode, bool(include_extra))
        optimizer_meta: Dict[str, Any] = {}
        prepriced: Optional[Dict[str, Any]] = None
        unassigned_guests: List[Dict[str, Any]] = []

        if action not in ('manual', 'save') and strategy == 'best_price' and source_mode == 'live':
            if guest_input_mode == 'list' and guest_list:
                optimized = _best_price_plan_with_guest_list(
                    rows, guest_list, arrival=arrival, departure=departure, placement_mode=placement_mode,
                    adults=adults, children=children, paid_children=paid_children,
                    price_list_id=price_list_id, rate_plan_id=rate_plan_id,
                )
            else:
                optimized = _best_price_count_plan(
                    rows, arrival=arrival, departure=departure,
                    adults=adults, children=children, paid_children=paid_children,
                    price_list_id=price_list_id, rate_plan_id=rate_plan_id,
                )
            allocation = optimized['allocation']
            summary = optimized['summary']
            room_plan = optimized['room_plan']
            prepriced = optimized['pricing']
            optimizer_meta = optimized.get('optimizer') or {}
            unassigned_guests = optimized.get('unassigned_guests') or []
        else:
            if action in ('manual', 'save'):
                allocation = _manual_allocation_from_form(rows)
            else:
                # If live pricing is unavailable, a best-price request may still show
                # physical availability, but it cannot claim a cheapest price.
                structural_strategy = 'priority' if strategy == 'best_price' else strategy
                allocation = _auto_allocate(rows, guests, structural_strategy, placement_mode)
            summary = _allocation_summary(rows, allocation, guests)
            room_plan = _build_room_plan(
                summary,
                adults,
                children,
                paid_children_total=paid_children,
                form=request.form if action in ('manual', 'save') else None,
            )
            if guest_input_mode == 'list' and guest_list:
                room_plan, unassigned_guests = _assign_guest_list_to_room_plan(room_plan, guest_list, placement_mode)

        result: Dict[str, Any] = {
            'cache_id': cache_id,
            'arrival': arrival,
            'departure': departure,
            'nights': nights,
            'guest_count': guests,
            'adults': adults,
            'children': children,
            'paid_children': paid_children,
            'guest_input_mode': guest_input_mode,
            'guest_list': guest_list,
            'guest_list_source': guest_list_source,
            'unassigned_guests': unassigned_guests,
            'placement_mode': placement_mode,
            'placement_label': PLACEMENT_LABELS[placement_mode],
            'include_extra': include_extra,
            'early_checkin': early_checkin,
            'late_checkout': late_checkout,
            'strategy': strategy,
            'strategy_label': STRATEGY_LABELS.get(strategy, strategy),
            'price_list_id': price_list_id,
            'rate_plan_id': rate_plan_id,
            'available_whole_stay': _ival(payload.get('available_whole_stay'), 0, minimum=0),
            'active_rooms_whole_stay': _ival(payload.get('active_rooms_whole_stay'), 0, minimum=0),
            'occupied_rooms_any_overlap': _ival(payload.get('occupied_rooms_any_overlap'), 0, minimum=0),
            'source': str(payload.get('source') or 'PMS Availability Sidecar'),
            'generated_at': str(payload.get('generated_at') or ''),
            'source_mode': source_mode,
            'warnings': warnings,
            'room_plan': room_plan,
            'pricing': None,
            'pricing_breakdown': [],
            'pricing_daily': {'days': [], 'period_extras': [], 'has_daily_rates': False, 'currency': 'UAH'},
            'pricing_error': '',
            'commercial': None,
            'booking_restrictions': [],
            'booking_allowed': True,
            'optimizer': optimizer_meta,
            'standard_extra_beds_used': _portable_standard_bed_usage(room_plan),
            'standard_extra_bed_pool_limit': _standard_extra_bed_pool(),
            'placement_error': _allocation_failure_message(summary),
            'daily_mode': False,
            'daily_composition': daily_schedule,
            **summary,
        }

        if result['fits'] and source_mode == 'live':
            try:
                if guest_input_mode == 'list' and unassigned_guests:
                    names = ', '.join(g.get('full_name') or '?' for g in unassigned_guests[:8])
                    raise ValueError(f'Не вдалося розмістити всіх гостей зі списку за заданими умовами: {names}. Відкоригуйте побажання або вибір номерів.')
                _validate_room_plan(room_plan, adults, children, paid_children)
                result['standard_extra_beds_used'] = _portable_standard_bed_usage(room_plan)
                result['standard_extra_bed_pool_limit'] = _standard_extra_bed_pool()
                pricing = prepriced or _quote_room_plan(
                    arrival=arrival,
                    departure=departure,
                    room_plan=room_plan,
                    price_list_id=price_list_id,
                    rate_plan_id=rate_plan_id,
                )
                result['pricing'] = pricing
                result['pricing_breakdown'] = _pricing_category_breakdown(pricing, nights)
                result['pricing_daily'] = _pricing_daily_statement(pricing, arrival, departure)
                if nights > 1 and not _daily_statement_is_complete(result['pricing_daily'], nights):
                    pricing['daily_statement'] = _exact_daily_statement_for_static_plan(
                        arrival=arrival, departure=departure, room_plan=room_plan,
                        price_list_id=price_list_id, rate_plan_id=rate_plan_id,
                    )
                    result['pricing_daily'] = _pricing_daily_statement(pricing, arrival, departure)
                for idx, day_item in enumerate(result['pricing_daily'].get('days') or []):
                    if idx < len(daily_schedule):
                        day_item['adults'] = daily_schedule[idx]['adults']
                        day_item['children'] = daily_schedule[idx]['children']
                        day_item['guest_count'] = daily_schedule[idx]['guest_count']
                        day_item['placement_mode'] = daily_schedule[idx]['placement_mode']
                        day_item['placement_label'] = daily_schedule[idx]['placement_label']
                        day_item['include_extra'] = daily_schedule[idx]['include_extra']
                if result['pricing_daily'].get('days'):
                    pricing['daily_statement'] = result['pricing_daily']
                stay_time = _stay_time_availability_for_plans(
                    arrival=arrival, departure=departure, first_room_plan=room_plan, last_room_plan=room_plan,
                    request_early_all=bool(early_checkin), request_late_all=bool(late_checkout), strict_explicit=False,
                )
                result['warnings'].extend(stay_time.get('warnings') or [])
                pricing, result['pricing_daily'] = _apply_stay_time_surcharges(
                    pricing, result['pricing_daily'],
                    early_checkin=bool(early_checkin), late_checkout=bool(late_checkout),
                    early_room_labels=stay_time.get('early_room_labels') or [],
                    late_room_labels=stay_time.get('late_room_labels') or [],
                    availability_meta=stay_time,
                )
                result['booking_restrictions'] = list(pricing.get('booking_restrictions') or [])
                result['booking_allowed'] = bool(pricing.get('booking_allowed', True))
                result['fits'] = bool(result.get('fits')) and result['booking_allowed']
                result['commercial'] = _commercial_terms(pricing.get('commercial_accommodation_total', pricing['hms_accommodation_total']), discount_percent)
            except Exception as pricing_exc:
                result['pricing_error'] = _pricing_error_for_manager(pricing_exc)
        elif result['fits'] and source_mode != 'live':
            result['pricing_error'] = 'Наявність показана зі збереженого знімка. Для вартості потрібне актуальне підключення до системи бронювання.'

        if action == 'save':
            if not result.get('pricing') or not result.get('commercial'):
                result['pricing_error'] = result.get('pricing_error') or 'Пропозицію не збережено: немає підтвердженої актуальної вартості.'
                return _render_calculator(result=result, form_values=form_values)
            if result.get('booking_allowed') is False:
                result['pricing_error'] = 'Пропозицію не збережено: ' + (_booking_restriction_failure_message(result.get('booking_restrictions') or [], arrival=arrival, departure=departure, selected_nights=nights) or 'вибрані умови бронювання не виконані.')
                return _render_calculator(result=result, form_values=form_values)

            conn = _db()
            allocation_snapshot = {
                'rows': [
                    {
                        'room_type_id': r['room_type_id'], 'category': r['category'], 'guest_label': r['guest_label'],
                        'available_rooms': r['available_rooms'], 'allocated_rooms': r['allocated_rooms'],
                        'base_capacity': r['base_capacity'], 'extra_capacity': r['extra_capacity'],
                        'capacity_per_room': r['capacity_per_room'], 'allocated_capacity': r['allocated_capacity'],
                        'room_ids': r['room_ids'], 'room_labels': r['room_labels'],
                    } for r in summary['rows']
                ],
                'used_rooms': summary['used_rooms'],
                'allocated_capacity': summary['allocated_capacity'],
                'shortage': summary['shortage'],
                'spare_places': summary['spare_places'],
                'standard_extra_beds_used': _portable_standard_bed_usage(room_plan),
                'standard_extra_bed_pool_limit': _standard_extra_bed_pool(),
            }
            pricing = result['pricing']
            commercial = result['commercial']
            cache_for_save = _load_cache(cache_id)
            quote_data = {
                'client_name': form_values['client_name'],
                'title': form_values['title'],
                'arrival': arrival,
                'departure': departure,
                'nights': nights,
                'guest_count': guests,
                'placement_mode': placement_mode,
                'include_extra': include_extra,
                'early_checkin': early_checkin,
                'late_checkout': late_checkout,
                'strategy': strategy,
                'availability_source': result['source'],
                'availability_fetched_at': cache_for_save['row']['fetched_at'] if cache_for_save else _now(),
                'availability_json': json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                'allocation_json': json.dumps(allocation_snapshot, ensure_ascii=False, separators=(',', ':')),
                'available_whole_stay': result['available_whole_stay'],
                'configured_capacity': result['configured_capacity'],
                'placed_guests': result['placed_guests'],
                'shortage': result['shortage'],
                'spare_places': result['spare_places'],
                'manager_note': form_values['manager_note'],
                'guest_note': form_values['guest_note'],
                'tariff_status': 'live_hms',
                'adults': adults,
                'children': children,
                'occupancy_json': json.dumps(room_plan, ensure_ascii=False, separators=(',', ':')),
                'pricing_json': json.dumps(pricing, ensure_ascii=False, separators=(',', ':')),
                'pricing_source': str(pricing.get('source') or ''),
                'pricing_generated_at': str(pricing.get('generated_at') or '') or None,
                'price_list_id': price_list_id or None,
                'rate_plan_id': rate_plan_id or None,
                'include_tourist_tax': 0,
                'stay_total_before_tourist_tax': pricing.get('stay_total_before_tourist_tax'),
                'tourist_tax_total': pricing.get('tourist_tax_total'),
                'stay_total': pricing.get('stay_total'),
                'currency': str(pricing.get('currency') or ''),
                'commercial_discount_percent': commercial['discount_percent'],
                'commercial_discount_amount': commercial['discount_amount'],
                'commercial_total': commercial['commercial_total'],
                'commercial_note': form_values['commercial_note'],
                'guest_input_mode': guest_input_mode,
                'guest_list_json': json.dumps(guest_list, ensure_ascii=False, separators=(',', ':')),
                'guest_list_source': guest_list_source,
                'daily_plan_json': _daily_schedule_json(daily_schedule),
            }
            quote_id, quote_number, revision_no = _persist_quote_version(
                conn, quote_data, edit_quote_id=edit_quote_id, revision_kind='recalculation'
            )
            conn.commit()
            _audit(
                'accommodation_quote', quote_id, 'recalculated' if edit_quote_id else 'created',
                new_value=f'{quote_number}; v{revision_no}',
                reason=f'{arrival}..{departure}; guests={guests}; live_hms; discount={commercial["discount_percent"]}%'
            )
            if edit_quote_id:
                flash(f'Пропозицію {quote_number} перераховано та збережено як версію {revision_no}. Попередня версія залишилась в історії.', 'success')
            else:
                flash(f'Пропозицію {quote_number} збережено. Базова актуальна ціна зафіксована; комерційну знижку можна змінювати у збереженій пропозиції.', 'success')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

        return _render_calculator(result=result, form_values=form_values)
    except Exception as exc:
        return _render_calculator(error=_pricing_error_for_manager(exc), form_values=form_values)



@bp.route('/accommodation-calculator/timetable', methods=['GET'])
def timetable_data():
    ensure_accommodation_schema()
    arrival = (request.args.get('arrival') or '').strip()
    departure = (request.args.get('departure') or '').strip()
    try:
        pad_days = _ival(request.args.get('pad_days'), 1, minimum=0, maximum=7)
        payload = _request_pms_timetable(arrival, departure, pad_days=pad_days)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({'ok': False, 'error': _pricing_error_for_manager(exc)}), 502

@bp.route('/accommodation-calculator/timetable-view', methods=['GET'])
def timetable_view():
    ensure_accommodation_schema()
    arrival = (request.args.get('arrival') or '').strip()
    departure = (request.args.get('departure') or '').strip()
    edit_day = (request.args.get('edit_day') or '').strip()
    mode = (request.args.get('mode') or 'view').strip().lower()
    if mode not in ('view', 'select'):
        mode = 'view'
    try:
        _parse_dates(arrival, departure)
    except Exception:
        today = date.today()
        arrival = today.isoformat()
        departure = (today + timedelta(days=1)).isoformat()
    if edit_day:
        try:
            date.fromisoformat(edit_day)
        except Exception:
            edit_day = ''
    return render_template(
        'accommodation_timetable.html',
        title='Шахматка номерів',
        arrival=arrival,
        departure=departure,
        edit_day=edit_day,
        mode=mode,
    )

@bp.route('/accommodation-calculator/settings', methods=['GET', 'POST'])
def settings_page():
    ensure_accommodation_schema()
    conn = _db()
    if request.method == 'POST':
        actor = _actor()
        now = _now()
        standard_extra_bed_pool = _ival(request.form.get('standard_extra_bed_pool'), DEFAULT_STANDARD_EXTRA_BED_POOL, minimum=0, maximum=100)
        conn.execute('''
            INSERT INTO accommodation_settings(setting_key,value_text,updated_at,updated_by) VALUES('standard_extra_bed_pool',?,?,?)
            ON CONFLICT(setting_key) DO UPDATE SET value_text=excluded.value_text, updated_at=excluded.updated_at, updated_by=excluded.updated_by
        ''', (str(standard_extra_bed_pool), now, actor))
        rows = conn.execute('SELECT room_type_id FROM accommodation_room_type_rules WHERE room_type_id<>13 ORDER BY room_type_id').fetchall()
        for row in rows:
            rid = int(row['room_type_id'])
            prefix = f'r{rid}_'
            enabled = 1 if request.form.get(prefix + 'enabled') == '1' else 0
            extra_enabled = 1 if request.form.get(prefix + 'extra_enabled') == '1' else 0
            guest_label = (request.form.get(prefix + 'guest_label') or '').strip()
            standard_capacity = _ival(request.form.get(prefix + 'standard_capacity'), 0, minimum=0, maximum=30)
            room_capacity = _ival(request.form.get(prefix + 'room_capacity'), 0, minimum=0, maximum=20)
            bed_capacity = _ival(request.form.get(prefix + 'bed_capacity'), 0, minimum=0, maximum=20)
            extra_capacity = _ival(request.form.get(prefix + 'extra_capacity'), 0, minimum=0, maximum=10)
            max_override = _ival(request.form.get(prefix + 'max_override'), 0, minimum=0, maximum=30)
            priority = _ival(request.form.get(prefix + 'priority'), rid * 10, minimum=1, maximum=9999)
            extra_label = (request.form.get(prefix + 'extra_label') or 'Додаткове місце / диван').strip()
            structure_note = (request.form.get(prefix + 'structure_note') or '').strip()
            manager_note = (request.form.get(prefix + 'manager_note') or '').strip()
            conn.execute('''
                UPDATE accommodation_room_type_rules
                SET guest_label=?, is_enabled=?, standard_capacity=?, room_capacity=?, bed_capacity=?, extra_capacity=?,
                    extra_label=?, extra_enabled=?, priority=?, max_capacity_override=?,
                    structure_note=?, manager_note=?, updated_at=?, updated_by=?
                WHERE room_type_id=? AND room_type_id<>13
            ''', (
                guest_label, enabled, standard_capacity, room_capacity, bed_capacity, extra_capacity,
                extra_label, extra_enabled, priority, max_override,
                structure_note, manager_note, now, actor, rid,
            ))
        conn.commit()
        _audit('accommodation_rules', 'room_types', 'updated', reason='Ручна матриця місткості по RoomTypeID')
        flash('Матрицю розміщення та фонд додаткових ліжок збережено.', 'success')
        return redirect(url_for('accommodation.settings_page'))

    rules = conn.execute('SELECT * FROM accommodation_room_type_rules WHERE room_type_id<>13 ORDER BY priority, room_type_id').fetchall()
    return render_template(
        'accommodation_rules.html', title='Налаштування проживання', rules=rules,
        standard_extra_bed_pool=_standard_extra_bed_pool(conn),
        standard_portable_room_type_ids=STANDARD_PORTABLE_EXTRA_BED_ROOM_TYPE_IDS,
    )


@bp.route('/accommodation-calculator/quotes')
def quotes_page():
    ensure_accommodation_schema()
    q = (request.args.get('q') or '').strip()
    conn = _db()
    if q:
        like = f'%{q}%'
        rows = conn.execute('''
            SELECT * FROM accommodation_quotes
            WHERE quote_number LIKE ? OR client_name LIKE ? OR title LIKE ?
            ORDER BY created_at DESC LIMIT 200
        ''', (like, like, like)).fetchall()
    else:
        rows = conn.execute('SELECT * FROM accommodation_quotes ORDER BY created_at DESC LIMIT 200').fetchall()
    return render_template('accommodation_quotes.html', title='Прорахунки проживання', rows=rows, q=q, placement_labels=PLACEMENT_LABELS)



def _quote_daily_schedule_from_row(row: Any) -> List[Dict[str, Any]]:
    q = _row_dict(row)
    try:
        schedule = json.loads(q.get('daily_plan_json') or '[]')
        if isinstance(schedule, list) and schedule:
            return [dict(x) for x in schedule if isinstance(x, dict)]
    except Exception:
        pass
    return list(_quote_edit_form_values(row).get('daily_composition') or [])


def _quote_occupancy_by_day(row: Any) -> Dict[str, List[Dict[str, Any]]]:
    q = _row_dict(row)
    try:
        items = json.loads(q.get('occupancy_json') or '[]')
        if not isinstance(items, list):
            items = []
    except Exception:
        items = []
    out: Dict[str, List[Dict[str, Any]]] = {}
    wrapped = False
    for item in items:
        if not isinstance(item, dict):
            continue
        day = str(item.get('date') or '')
        plan = item.get('room_plan') if isinstance(item.get('room_plan'), list) else []
        if day:
            wrapped = True
            out[day] = [dict(x) for x in plan if isinstance(x, dict)]
    if wrapped:
        return out

    # Legacy saved quotes (including one-night quotes) stored occupancy_json as a flat
    # physical-room plan.  That plan is the exact room set for the whole saved stay.
    # Rehydrate it for every night so Edit -> manual room editor can show the rooms
    # immediately instead of forcing a fresh autocalculation first.
    flat = [dict(x) for x in items if isinstance(x, dict) and (x.get('room_id') not in (None, ''))]
    if flat:
        for day in _quote_daily_schedule_from_row(row):
            d = str(day.get('date') or '')
            if d:
                out[d] = [dict(x) for x in flat]
    return out


def _quote_saved_availability_by_day(row: Any) -> Dict[str, Dict[str, Any]]:
    q = _row_dict(row)
    try:
        payload = json.loads(q.get('availability_json') or '{}')
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    out: Dict[str, Dict[str, Any]] = {}
    if payload.get('_daily_mode'):
        for item in payload.get('days') or []:
            if not isinstance(item, dict) or not isinstance(item.get('payload'), dict):
                continue
            d = str(item.get('date') or '')
            if d:
                out[d] = dict(item['payload'])
        return out
    # Legacy whole-stay availability is valid as the saved stock snapshot for every
    # night of that quote.  Live availability is checked again when manager recalculates.
    if payload:
        for day in _quote_daily_schedule_from_row(row):
            d = str(day.get('date') or '')
            if d:
                out[d] = dict(payload)
    return out


def _saved_quote_result_for_edit(row: Any) -> Dict[str, Any]:
    """Build the calculator result from the exact saved quote snapshot for edit mode.

    No PMS request is made here: opening Edit must always show the physical rooms that
    belong to this saved quote.  Add/remove/replace then posts through the normal manual
    recalc path, which performs the live PMS/rate/restriction validation.
    """
    q = _row_dict(row)
    schedule = _quote_daily_schedule_from_row(row)
    if not schedule:
        raise ValueError('У збереженій пропозиції немає денного плану для редагування.')
    occupancy_by_day = _quote_occupancy_by_day(row)
    availability_by_day = _quote_saved_availability_by_day(row)
    day_results: List[Dict[str, Any]] = []
    warnings: List[str] = [
        'Показано фізичні номери зі збереженої версії. Після ручної зміни система повторно перевірить актуальну наявність, тарифи та обмеження бронювання.'
    ]

    for day_src in schedule:
        day = dict(day_src)
        d = str(day.get('date') or '')
        nd = str(day.get('next_date') or '')
        adults = _ival(day.get('adults'), 0, minimum=0)
        children = _ival(day.get('children'), 0, minimum=0)
        paid_children = _ival(day.get('paid_children'), 0, minimum=0)
        guest_count = adults + children
        placement_mode = str(day.get('placement_mode') or q.get('placement_mode') or 'standard')
        include_extra = bool(_ival(day.get('include_extra'), _ival(q.get('include_extra'), 0), minimum=0))
        payload = availability_by_day.get(d) or {}
        rows = _category_rows(payload, placement_mode, include_extra) if payload else []
        plan = [dict(x) for x in occupancy_by_day.get(d, [])]

        # Ensure every saved physical room remains visible in the selector even if it is
        # absent from the old availability list.  Recalculation will validate it live.
        options = _daily_room_options(rows)
        option_ids = {str(x.get('room_id_token') or x.get('room_id') or '') for x in options}
        for old in plan:
            token = str(old.get('room_id') or '').strip()
            if not token or token in option_ids:
                continue
            options.append({
                'room_id': old.get('room_id'), 'room_id_token': token,
                'room_label': str(old.get('room_label') or token),
                'room_type_id': _ival(old.get('room_type_id'), 0),
                'category': str(old.get('category') or old.get('pms_category') or ''),
                'pms_category': str(old.get('pms_category') or old.get('category') or ''),
                'base_capacity': _ival(old.get('base_capacity'), 0, minimum=0),
                'extra_capacity': _ival(old.get('extra_capacity'), 0, minimum=0),
                'capacity_per_room': _ival(old.get('capacity_per_room'), 0, minimum=0),
                'room_capacity_rule': _ival(old.get('room_capacity_rule'), 0, minimum=0),
                'bed_capacity_rule': _ival(old.get('bed_capacity_rule'), 0, minimum=0),
                'portable_standard_bed': bool(old.get('portable_standard_bed')),
                'priority': 9999,
                'saved_only': True,
            })
            option_ids.add(token)
        options.sort(key=lambda x: (_ival(x.get('priority'), 100), _ival(x.get('room_type_id'), 0), str(x.get('room_label') or '')))

        # Old snapshots already contain capacities/occupants on each exact physical room.
        # If rules/availability rows are missing, summary still comes from that saved plan.
        summary = _manual_summary_from_room_plan(rows, plan, guest_count)
        day_results.append({
            **day,
            'date': d, 'next_date': nd,
            'date_label': str(day.get('date_label') or d),
            'next_date_label': str(day.get('next_date_label') or nd),
            'adults': adults, 'children': children, 'paid_children': paid_children,
            'guest_count': guest_count,
            'placement_mode': placement_mode,
            'placement_label': str(day.get('placement_label') or PLACEMENT_LABELS.get(placement_mode, placement_mode)),
            'include_extra': include_extra,
            'rows': rows, 'room_plan': plan, 'room_options': options,
            'summary': summary,
            'available_whole_stay': _ival(payload.get('available_whole_stay'), q.get('available_whole_stay') or 0, minimum=0),
            'active_rooms_whole_stay': _ival(payload.get('active_rooms_whole_stay'), 0, minimum=0),
        })

    try:
        pricing = json.loads(q.get('pricing_json') or '{}')
        if not isinstance(pricing, dict):
            pricing = {}
    except Exception:
        pricing = {}
    try:
        allocation_snapshot = json.loads(q.get('allocation_json') or '{}')
        if not isinstance(allocation_snapshot, dict):
            allocation_snapshot = {}
    except Exception:
        allocation_snapshot = {}
    pricing_daily = _pricing_daily_statement(pricing, str(q.get('arrival') or ''), str(q.get('departure') or ''))
    booking_restrictions = list(pricing.get('booking_restrictions') or []) if isinstance(pricing, dict) else []
    booking_allowed = bool(pricing.get('booking_allowed', True)) if isinstance(pricing, dict) else True
    max_guests = max((_ival(x.get('guest_count'), 0) for x in schedule), default=_ival(q.get('guest_count'), 0, minimum=0))
    max_adults = max((_ival(x.get('adults'), 0) for x in schedule), default=_ival(q.get('adults'), 0, minimum=0))
    max_children = max((_ival(x.get('children'), 0) for x in schedule), default=_ival(q.get('children'), 0, minimum=0))
    max_paid_children = max((_ival(x.get('paid_children'), 0) for x in schedule), default=0)
    max_used_rooms = max((_ival((x.get('summary') or {}).get('used_rooms'), 0) for x in day_results), default=0)
    max_capacity = max((_ival((x.get('summary') or {}).get('allocated_capacity'), 0) for x in day_results), default=0)
    max_shortage = max((_ival((x.get('summary') or {}).get('shortage'), 0) for x in day_results), default=0)
    max_spare = max((_ival((x.get('summary') or {}).get('spare_places'), 0) for x in day_results), default=0)
    min_available = min((_ival(x.get('available_whole_stay'), 0) for x in day_results if _ival(x.get('guest_count'), 0) > 0), default=_ival(q.get('available_whole_stay'), 0, minimum=0))
    commercial = {
        'base_total': _money_float(_money_decimal(q.get('stay_total_before_tourist_tax') or q.get('stay_total') or 0)),
        'discount_percent': _money_float(_money_decimal(q.get('commercial_discount_percent') or 0)),
        'discount_amount': _money_float(_money_decimal(q.get('commercial_discount_amount') or 0)),
        'commercial_total': _money_float(_money_decimal(q.get('commercial_total') or 0)),
    }
    return {
        'cache_id': '',
        'arrival': str(q.get('arrival') or ''), 'departure': str(q.get('departure') or ''),
        'nights': _ival(q.get('nights'), len(schedule), minimum=1),
        'guest_count': max_guests, 'adults': max_adults, 'children': max_children, 'paid_children': max_paid_children,
        'guest_input_mode': str(q.get('guest_input_mode') or 'count'),
        'guest_list': _guest_list_from_json(q.get('guest_list_json') or '[]'),
        'guest_list_source': str(q.get('guest_list_source') or ''), 'unassigned_guests': [],
        'placement_mode': str(q.get('placement_mode') or 'standard'),
        'placement_label': 'Змінюється по днях' if len(schedule) > 1 else str(day_results[0].get('placement_label') or ''),
        'include_extra': bool(_ival(q.get('include_extra'), 0)),
        'early_checkin': bool(_ival(q.get('early_checkin'), 0)), 'late_checkout': bool(_ival(q.get('late_checkout'), 0)),
        'strategy': str(q.get('strategy') or 'priority'),
        'strategy_label': STRATEGY_LABELS.get(str(q.get('strategy') or 'priority'), str(q.get('strategy') or 'priority')),
        'price_list_id': q.get('price_list_id'), 'rate_plan_id': q.get('rate_plan_id'),
        'available_whole_stay': min_available, 'active_rooms_whole_stay': min_available,
        'occupied_rooms_any_overlap': 0, 'source': str(q.get('availability_source') or 'Збережена пропозиція'),
        'generated_at': str(q.get('availability_fetched_at') or q.get('updated_at') or ''),
        'source_mode': 'saved_edit', 'warnings': warnings,
        'room_plan': [], 'rows': [], 'pricing': pricing, 'pricing_breakdown': [], 'pricing_daily': pricing_daily,
        'pricing_error': '', 'commercial': commercial,
        'booking_restrictions': booking_restrictions, 'booking_allowed': booking_allowed,
        'optimizer': {'mode': 'saved_edit'},
        'standard_extra_beds_used': max((_portable_standard_bed_usage(x.get('room_plan') or []) for x in day_results), default=0),
        'standard_extra_bed_pool_limit': _standard_extra_bed_pool(),
        'placement_error': '', 'used_rooms': max_used_rooms, 'allocated_capacity': max_capacity,
        'configured_capacity': max_capacity, 'placed_guests': max_guests,
        'shortage': max_shortage, 'spare_places': max_spare,
        'fits': bool(max_shortage == 0 and booking_allowed),
        'daily_mode': True, 'daily_composition': schedule, 'daily_calculations': day_results,
        'allocation_snapshot': allocation_snapshot, 'manual_plan_active': False, 'manual_day': '',
        'previous_commercial_total': _money_float(_money_decimal(q.get('commercial_total') or 0)),
        'commercial_delta': 0.0,
    }


def _hydrate_saved_room_plan_live(
    *, day_date: str, rows: List[Dict[str, Any]], saved_plan: List[Dict[str, Any]],
    adults: int, children: int, paid_children: int,
) -> List[Dict[str, Any]]:
    """Map a stored exact room plan onto today's live physical-room catalog."""
    options = _daily_room_options(rows)
    catalog = {str(x.get('room_id_token') or ''): x for x in options}
    out: List[Dict[str, Any]] = []
    seen = set()
    for idx, old in enumerate(saved_plan or []):
        token = str(old.get('room_id') or '').strip()
        if not token:
            continue
        if token in seen:
            raise ValueError(f'{day_date}: номер {token} дублюється у збереженому плані.')
        seen.add(token)
        live = catalog.get(token)
        if not live:
            label = str(old.get('room_label') or token)
            raise ValueError(
                f'{day_date}: номер {label} із поточної пропозиції вже не входить до актуально вільних номерів на цю ніч. '
                'Відкрийте «Змінити номери цього дня» та виберіть заміну.'
            )
        item = dict(live)
        item.update({
            'key': str(old.get('key') or f'existing_{day_date.replace("-", "")}_{idx + 1}'),
            'position': _ival(old.get('position'), idx + 1, minimum=1),
            'adults': _ival(old.get('adults'), 0, minimum=0),
            'children': _ival(old.get('children'), 0, minimum=0),
            'paid_children': _ival(old.get('paid_children'), 0, minimum=0),
            'extra_beds': _ival(old.get('extra_beds'), 0, minimum=0),
            'resident_adults': _ival(old.get('resident_adults'), 0, minimum=0),
            'nonresident_adults': _ival(old.get('nonresident_adults'), 0, minimum=0),
            'tourist_tax_exempt_adults': _ival(old.get('tourist_tax_exempt_adults'), 0, minimum=0),
            'occupants': _ival(old.get('adults'), 0, minimum=0) + _ival(old.get('children'), 0, minimum=0),
            'manual_locked': bool(old.get('manual_locked')),
            'manual_source': str(old.get('manual_source') or 'saved'),
            **({'early_checkin': bool(old.get('early_checkin'))} if 'early_checkin' in old else {}),
            **({'late_checkout': bool(old.get('late_checkout'))} if 'late_checkout' in old else {}),
        })
        out.append(item)
    _validate_room_plan(out, adults, children, paid_children)
    return out


def _manual_editor_context_for_quote(row: Any, day_date: str, *, room_plan: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    schedule = _quote_daily_schedule_from_row(row)
    day = next((dict(x) for x in schedule if str(x.get('date') or '') == day_date), None)
    if not day:
        raise ValueError('Вибрану ніч не знайдено у цій пропозиції.')
    raw = _request_pms_live(day_date, str(day.get('next_date') or ''))
    payload, warnings = _validate_payload(raw, day_date, str(day.get('next_date') or ''))
    conn = _db()
    _sync_categories_from_payload(conn, payload)
    conn.commit()
    rows = _category_rows(payload, str(day.get('placement_mode') or 'standard'), bool(day.get('include_extra')))
    options = _daily_room_options(rows)
    saved_map = _quote_occupancy_by_day(row)
    current = [dict(x) for x in (room_plan if room_plan is not None else saved_map.get(day_date, []))]
    q = _row_dict(row)
    is_first = str(day_date) == str(q.get('arrival') or '')
    is_last = str(day.get('next_date') or '') == str(q.get('departure') or '')
    early_set: set = set()
    late_set: set = set()
    early_period = late_period = ''
    early_error = late_error = ''
    if is_first:
        a = date.fromisoformat(day_date)
        ep_a, ep_d = (a - timedelta(days=1)).isoformat(), a.isoformat()
        early_period = f'{ep_a} → {ep_d}'
        try:
            early_set, _labels, ww = _available_room_tokens_for_period(ep_a, ep_d)
            warnings.extend(ww)
        except Exception as exc:
            early_error = _pricing_error_for_manager(exc)
    if is_last:
        dep = date.fromisoformat(str(day.get('next_date') or ''))
        lp_a, lp_d = dep.isoformat(), (dep + timedelta(days=1)).isoformat()
        late_period = f'{lp_a} → {lp_d}'
        try:
            late_set, _labels, ww = _available_room_tokens_for_period(lp_a, lp_d)
            warnings.extend(ww)
        except Exception as exc:
            late_error = _pricing_error_for_manager(exc)

    for opt in options:
        token = str(opt.get('room_id') or '')
        opt['early_checkin_available'] = bool(is_first and not early_error and token in early_set)
        opt['late_checkout_available'] = bool(is_last and not late_error and token in late_set)
        opt['early_checkin_period'] = early_period
        opt['late_checkout_period'] = late_period
    option_by_id = {str(x.get('room_id')): x for x in options}
    option_ids = set(option_by_id)
    missing = []
    for r in current:
        token = str(r.get('room_id') or '')
        live = option_by_id.get(token) or {}
        r['early_checkin_available'] = bool(live.get('early_checkin_available'))
        r['late_checkout_available'] = bool(live.get('late_checkout_available'))
        # Old v5.308 quote-level flags become a safe per-room default only when the room
        # passes the new adjacent-night availability check.
        if is_first and 'early_checkin' not in r:
            r['early_checkin'] = bool(_ival(q.get('early_checkin'), 0) and r['early_checkin_available'])
        if is_last and 'late_checkout' not in r:
            r['late_checkout'] = bool(_ival(q.get('late_checkout'), 0) and r['late_checkout_available'])
        if token and token not in option_ids:
            missing.append({
                'room_id': r.get('room_id'), 'room_label': str(r.get('room_label') or token),
                'category': str(r.get('category') or ROOM_TYPE_NAMES.get(_ival(r.get('room_type_id'), 0), '')),
            })
    return {
        'day': day,
        'day_date': day_date,
        'next_date': str(day.get('next_date') or ''),
        'current_plan': current,
        'room_options': options,
        'missing_rooms': missing,
        'warnings': warnings,
        'next_revision_no': _ival(q.get('revision_no'), 1, minimum=1) + 1,
        'is_first_night': is_first,
        'is_last_night': is_last,
        'early_checkin_period': early_period,
        'late_checkout_period': late_period,
        'early_checkin_error': early_error,
        'late_checkout_error': late_error,
    }



def _posted_manual_plan_for_editor(day_date: str, form, room_options: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    prefix = _manual_day_prefix(day_date)
    room_ids = list(form.getlist(prefix + '_room_id'))
    adults = list(form.getlist(prefix + '_adults'))
    children = list(form.getlist(prefix + '_children'))
    paid = list(form.getlist(prefix + '_paid_children'))
    extras = list(form.getlist(prefix + '_extra_beds'))
    locked = list(form.getlist(prefix + '_locked'))
    early = list(form.getlist(prefix + '_early_checkin'))
    late = list(form.getlist(prefix + '_late_checkout'))
    catalog = {str(x.get('room_id')): x for x in room_options}
    out: List[Dict[str, Any]] = []
    for idx, raw in enumerate(room_ids):
        token = str(raw or '').strip()
        if not token:
            continue
        base = dict(catalog.get(token) or {'room_id': token, 'room_label': token, 'category': 'Номер більше не доступний'})
        base.update({
            'adults': _ival(adults[idx] if idx < len(adults) else 0, 0, minimum=0),
            'children': _ival(children[idx] if idx < len(children) else 0, 0, minimum=0),
            'paid_children': _ival(paid[idx] if idx < len(paid) else 0, 0, minimum=0),
            'extra_beds': _ival(extras[idx] if idx < len(extras) else 0, 0, minimum=0),
            'manual_locked': str(locked[idx] if idx < len(locked) else '0').strip() in ('1','true','yes','on'),
            'early_checkin': str(early[idx] if idx < len(early) else '0').strip() in ('1','true','yes','on'),
            'late_checkout': str(late[idx] if idx < len(late) else '0').strip() in ('1','true','yes','on'),
        })
        out.append(base)
    return out


def _recalculate_quote_with_manual_day(row: Any, day_date: str, form, *, refill: bool = False) -> Dict[str, Any]:
    """Revalidate every night live, while changing one day's exact physical-room plan."""
    q = _row_dict(row)
    schedule = _quote_daily_schedule_from_row(row)
    if not schedule:
        raise ValueError('У пропозиції немає збереженого денного плану.')
    saved_by_day = _quote_occupancy_by_day(row)
    price_list_id = _ival(q.get('price_list_id'), DEFAULT_BASE_PRICE_LIST_ID, minimum=0)
    rate_plan_id = _ival(q.get('rate_plan_id'), 0, minimum=0)
    if rate_plan_id <= 0:
        rate_plan_id, _rate_plan_resolution = _effective_quote_rate_plan_id(price_list_id)
    conn = _db()
    day_results: List[Dict[str, Any]] = []
    cache_days: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for day_src in schedule:
        day = dict(day_src)
        d = str(day.get('date') or '')
        nd = str(day.get('next_date') or '')
        adults = _ival(day.get('adults'), 0, minimum=0)
        children = _ival(day.get('children'), 0, minimum=0)
        paid_children = _ival(day.get('paid_children'), 0, minimum=0)
        guest_count = adults + children
        original_composition = {
            'adults': adults, 'children': children, 'paid_children': paid_children,
            'guest_count': guest_count,
        }
        if not d or not nd:
            raise ValueError('У денному плані відсутні дати.')
        if guest_count <= 0:
            day_results.append({
                **day, 'rows': [], 'room_plan': [], 'room_options': [],
                'summary': {'rows': [], 'used_rooms': 0, 'allocated_capacity': 0, 'configured_capacity': 0, 'shortage': 0, 'spare_places': 0, 'fits': True},
                'pricing': {'ok': True, 'rooms': [], 'hms_accommodation_total': 0, 'stay_total_before_tourist_tax': 0, 'tourist_tax_total': 0, 'stay_total': 0, 'currency': str(q.get('currency') or 'UAH'), 'booking_restrictions': [], 'booking_allowed': True},
                'pricing_daily': {'days': [{'date': d, 'next_date': nd, 'date_label': day.get('date_label'), 'next_date_label': day.get('next_date_label'), 'lines': [], 'total': 0, 'base_total': 0, 'extra_total': 0}], 'period_extras': [], 'has_daily_rates': True, 'currency': str(q.get('currency') or 'UAH')},
                'available_whole_stay': 0, 'active_rooms_whole_stay': 0,
            })
            continue

        raw = _request_pms_live(d, nd)
        payload, day_warnings = _validate_payload(raw, d, nd)
        _sync_categories_from_payload(conn, payload)
        cache_days.append({'date': d, 'next_date': nd, 'payload': payload})
        warnings.extend([f'{day.get("date_label")}: {x}' for x in day_warnings])
        rows = _category_rows(payload, str(day.get('placement_mode') or 'standard'), bool(day.get('include_extra')))

        if d == day_date:
            if refill:
                # Refill means: preserve the declared composition for this night, keep
                # locked rooms, and automatically allocate the remaining guests.
                room_plan = _refill_day_around_locked_rooms(
                    day_date=d, rows=rows, adults=adults, children=children, paid_children=paid_children,
                    placement_mode=str(day.get('placement_mode') or 'standard'), strategy=str(q.get('strategy') or 'priority'), form=form,
                )
            else:
                # Inline manual editing is composition-aware.  Removing a room removes
                # the guests entered in that row from this night; adding/editing a row
                # changes this night's composition.  The header schedule is synchronized
                # from the room rows instead of rejecting the edit as a mismatch.
                room_plan = _manual_day_room_plan_from_form(
                    day_date=d, rows=rows, adults=adults, children=children, paid_children=paid_children,
                    form=form, require_full=False, allow_composition_change=True,
                )
                manual_counts = _manual_room_plan_counts(room_plan)
                adults = manual_counts['adults']
                children = manual_counts['children']
                paid_children = manual_counts['paid_children']
                guest_count = manual_counts['guest_count']
                day.update(manual_counts)
                day_src.update(manual_counts)
                day['composition_changed_manually'] = (
                    adults != original_composition['adults'] or
                    children != original_composition['children'] or
                    paid_children != original_composition['paid_children']
                )
                day['original_composition'] = original_composition
        else:
            room_plan = _hydrate_saved_room_plan_live(
                day_date=d, rows=rows, saved_plan=saved_by_day.get(d, []),
                adults=adults, children=children, paid_children=paid_children,
            )

        summary = _manual_summary_from_room_plan(rows, room_plan, guest_count)
        pricing = _quote_room_plan(
            arrival=d, departure=nd, room_plan=room_plan,
            price_list_id=price_list_id, rate_plan_id=rate_plan_id,
        )
        statement = _pricing_daily_statement(pricing, d, nd)
        if not statement.get('days'):
            raise RuntimeError(f'{day.get("date_label")}: не отримано точну денну вартість.')
        day_results.append({
            **day, 'rows': rows, 'room_plan': room_plan, 'room_options': _daily_room_options(rows),
            'summary': summary, 'pricing': pricing, 'pricing_daily': statement,
            'available_whole_stay': _ival(payload.get('available_whole_stay'), 0, minimum=0),
            'active_rooms_whole_stay': _ival(payload.get('active_rooms_whole_stay'), 0, minimum=0),
        })

    conn.commit()
    if not any(str(x.get('date') or '') == day_date for x in day_results):
        raise ValueError('Вибрану ніч не знайдено у розрахунку.')
    restrictions = _daily_booking_restrictions(day_results)
    pricing = _compose_daily_pricing(day_results, restrictions)
    pricing_daily = _pricing_daily_statement(pricing, str(q.get('arrival') or ''), str(q.get('departure') or ''))
    first_plan = list((day_results[0].get('room_plan') or [])) if day_results else []
    last_plan = list((day_results[-1].get('room_plan') or [])) if day_results else []
    stay_time = _stay_time_availability_for_plans(
        arrival=str(q.get('arrival') or ''), departure=str(q.get('departure') or ''),
        first_room_plan=first_plan, last_room_plan=last_plan,
        request_early_all=bool(_ival(q.get('early_checkin'), 0)),
        request_late_all=bool(_ival(q.get('late_checkout'), 0)),
        strict_explicit=True,
    )
    pricing, pricing_daily = _apply_stay_time_surcharges(
        pricing, pricing_daily,
        early_checkin=bool(stay_time.get('early_requested_count')),
        late_checkout=bool(stay_time.get('late_requested_count')),
        early_room_labels=stay_time.get('early_room_labels') or [],
        late_room_labels=stay_time.get('late_room_labels') or [],
        availability_meta=stay_time,
    )
    pricing.update(_tourist_tax_estimate(schedule))
    commercial = _commercial_terms(
        pricing.get('commercial_accommodation_total', pricing.get('hms_accommodation_total')),
        q.get('commercial_discount_percent')
    )
    allocation = _daily_allocation_snapshot(day_results)
    occupancy_daily = [
        {'date': x.get('date'), 'next_date': x.get('next_date'), 'room_plan': x.get('room_plan') or []}
        for x in day_results
    ]
    max_guests = max((_ival(x.get('guest_count'), 0) for x in schedule), default=0)
    max_adults = max((_ival(x.get('adults'), 0) for x in schedule), default=0)
    max_children = max((_ival(x.get('children'), 0) for x in schedule), default=0)
    max_capacity = max((_ival((x.get('summary') or {}).get('allocated_capacity'), 0) for x in day_results), default=0)
    max_spare = max((_ival((x.get('summary') or {}).get('spare_places'), 0) for x in day_results), default=0)
    min_available = min((_ival(x.get('available_whole_stay'), 0) for x in day_results if _ival(x.get('guest_count'), 0) > 0), default=0)
    cache_payload = {
        '_daily_mode': True, 'nights': len(schedule), 'source': 'PMS Availability Sidecar',
        'days': cache_days, 'daily_schedule': schedule,
    }
    selected = next(x for x in day_results if str(x.get('date') or '') == day_date)
    return {
        'day_results': day_results,
        'selected_day_result': selected,
        'pricing': pricing,
        'commercial': commercial,
        'allocation_snapshot': allocation,
        'occupancy_daily': occupancy_daily,
        'cache_payload': cache_payload,
        'daily_schedule': schedule,
        'warnings': warnings,
        'max_guests': max_guests,
        'max_adults': max_adults,
        'max_children': max_children,
        'max_capacity': max_capacity,
        'max_spare': max_spare,
        'min_available': min_available,
    }


def _quote_data_from_manual_recalculation(row: Any, calc: Dict[str, Any]) -> Dict[str, Any]:
    q = _row_dict(row)
    pricing = calc['pricing']
    commercial = calc['commercial']
    return {
        'client_name': q.get('client_name'), 'title': q.get('title'),
        'arrival': q.get('arrival'), 'departure': q.get('departure'), 'nights': q.get('nights'),
        'guest_count': calc['max_guests'], 'placement_mode': q.get('placement_mode'),
        'include_extra': q.get('include_extra'),
        'early_checkin': 1 if pricing.get('early_checkin_requested') else 0,
        'late_checkout': 1 if pricing.get('late_checkout_requested') else 0,
        'strategy': q.get('strategy'),
        'availability_source': str(pricing.get('source') or q.get('availability_source') or 'Система бронювання'),
        'availability_fetched_at': _now(),
        'availability_json': json.dumps(calc['cache_payload'], ensure_ascii=False, separators=(',', ':')),
        'allocation_json': json.dumps(calc['allocation_snapshot'], ensure_ascii=False, separators=(',', ':')),
        'available_whole_stay': calc['min_available'], 'configured_capacity': calc['max_capacity'],
        'placed_guests': calc['max_guests'], 'shortage': 0, 'spare_places': calc['max_spare'],
        'manager_note': q.get('manager_note'), 'guest_note': q.get('guest_note'), 'tariff_status': 'live_hms',
        'adults': calc['max_adults'], 'children': calc['max_children'],
        'occupancy_json': json.dumps(calc['occupancy_daily'], ensure_ascii=False, separators=(',', ':')),
        'pricing_json': json.dumps(pricing, ensure_ascii=False, separators=(',', ':')),
        'pricing_source': str(pricing.get('source') or ''), 'pricing_generated_at': str(pricing.get('generated_at') or '') or None,
        'price_list_id': q.get('price_list_id'), 'rate_plan_id': q.get('rate_plan_id'), 'include_tourist_tax': 0,
        'stay_total_before_tourist_tax': pricing.get('stay_total_before_tourist_tax'),
        'tourist_tax_total': pricing.get('tourist_tax_total'), 'stay_total': pricing.get('stay_total'),
        'currency': str(pricing.get('currency') or q.get('currency') or 'UAH'),
        'commercial_discount_percent': commercial['discount_percent'],
        'commercial_discount_amount': commercial['discount_amount'], 'commercial_total': commercial['commercial_total'],
        'commercial_note': q.get('commercial_note'), 'guest_input_mode': q.get('guest_input_mode'),
        'guest_list_json': q.get('guest_list_json'), 'guest_list_source': q.get('guest_list_source'),
        'daily_plan_json': json.dumps(calc.get('daily_schedule') or _quote_daily_schedule_from_row(row), ensure_ascii=False, separators=(',', ':')),
    }


def _clear_hms_booking_preflight(conn, quote_id: str) -> None:
    """Invalidate non-final HMS booking state when the quote snapshot changes.

    Legacy v5.315-v5.321 GroupIDs were temporary GroupCard handles, not persisted
    reservations.  They must never lock a quote or survive into a new revision.
    Only a completed booking or an ambiguous post-Reserve transaction is protected.
    """
    row = conn.execute(
        'SELECT hms_booking_status,hms_booking_group_id FROM accommodation_quotes WHERE quote_id=?',
        (quote_id,),
    ).fetchone()
    status = str(row['hms_booking_status'] or '').strip() if row else ''
    if status == 'booked':
        # Preserve the final HMS link. Existing behaviour permits a later quote revision;
        # the booking remains auditable instead of being silently forgotten.
        return
    if status == 'booking_uncertain':
        raise ValueError(
            'Редагування заблоковано: попередня HMS booking-транзакція має невизначений результат після ReserveGroup. '
            'Спочатку потрібна ручна перевірка HMS.'
        )
    conn.execute("""
        UPDATE accommodation_quotes
        SET hms_booking_status='', hms_booking_preflight_json='{}', hms_booking_preflight_at=NULL,
            hms_booking_preflight_by='', hms_booking_quote_revision=NULL,
            hms_booking_idempotency_key='', hms_booking_payload_json='{}', hms_booking_last_error='',
            hms_booking_group_id='', hms_booking_created_at=NULL, hms_booking_created_by='',
            hms_booking_bridge_job_id='', hms_booking_bridge_state='', hms_booking_bridge_started_at=NULL,
            hms_booking_bridge_started_by='', hms_booking_bridge_seen_at=NULL,
            hms_booking_bridge_group_id='', hms_booking_bridge_login_id='',
            hms_booking_bridge_diagnostic_json='{}', hms_booking_bridge_error=''
        WHERE quote_id=?
    """, (quote_id,))

def _booking_parse_time(value: Any, fallback: str) -> Tuple[int, int]:
    text = str(value or fallback or '').strip()
    m = re.match(r'^(\d{1,2}):(\d{2})', text)
    if not m:
        text = fallback
        m = re.match(r'^(\d{1,2}):(\d{2})', text)
    if not m:
        raise ValueError(f'Некоректний час HMS: {value!r}')
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f'Некоректний час HMS: {value!r}')
    return hour, minute


def _booking_dt(day: str, hhmm: Any, fallback: str) -> datetime:
    h, m = _booking_parse_time(hhmm, fallback)
    return datetime.combine(date.fromisoformat(str(day)), datetime.min.time()).replace(hour=h, minute=m)


def _booking_parse_iso_dt(value: Any) -> Optional[datetime]:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _booking_intervals_overlap(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    return a_start < b_end and a_end > b_start



def _hms_room_signature(room: Dict[str, Any]) -> Tuple[int, int, int, int]:
    """Composition fields that make one HMS reservation-card stay distinct."""
    return (
        _ival(room.get('adults'), 0, minimum=0),
        _ival(room.get('children'), 0, minimum=0),
        _ival(room.get('paid_children'), 0, minimum=0),
        _ival(room.get('extra_beds'), 0, minimum=0),
    )


def _hms_signature_label(signature: Tuple[int, int, int, int]) -> str:
    adults, children, paid_children, extra_beds = signature
    parts = [f'{adults} дор.']
    if children:
        child = f'{children} діт.'
        if paid_children:
            child += f' ({paid_children} плат.)'
        parts.append(child)
    if extra_beds:
        parts.append(f'{extra_beds} дод. місц.')
    return ' + '.join(parts)


def _hms_short_date(value: Any) -> str:
    try:
        return date.fromisoformat(str(value)).strftime('%d.%m')
    except Exception:
        return str(value or '')


def _hms_compatibility_from_plans(
    schedule: List[Dict[str, Any]], plans_by_day: Dict[str, List[Dict[str, Any]]]
) -> Dict[str, Any]:
    """Detect room reuse that forces the writer to create the same RoomID more than once.

    The confirmed one-transaction writer creates one HMS reservation-card per continuous
    room-stay segment. A physical RoomID is therefore HMS-safe only when it is used in one
    contiguous segment with one stable adults/children/paid-child/extra-bed composition.
    """
    appearances: Dict[int, List[Dict[str, Any]]] = {}
    room_labels: Dict[int, str] = {}
    room_types: Dict[int, int] = {}
    day_index = {str(day.get('date') or ''): idx for idx, day in enumerate(schedule or [])}

    for day in schedule or []:
        d = str(day.get('date') or '')
        nd = str(day.get('next_date') or '')
        for room in plans_by_day.get(d, []) or []:
            if not isinstance(room, dict):
                continue
            rid = _ival(room.get('room_id'), 0, minimum=0)
            if rid <= 0:
                continue
            room_labels[rid] = str(room.get('room_label') or rid)
            room_types[rid] = _ival(room.get('room_type_id'), 0, minimum=0)
            appearances.setdefault(rid, []).append({
                'date': d,
                'next_date': nd,
                'day_index': day_index.get(d, 999999),
                'signature': _hms_room_signature(room),
            })

    issues: List[Dict[str, Any]] = []
    all_segments: List[Dict[str, Any]] = []
    day_issue_map: Dict[str, List[str]] = {}
    for rid, items in appearances.items():
        items.sort(key=lambda x: (x['day_index'], x['date']))
        segments: List[Dict[str, Any]] = []
        current: Optional[Dict[str, Any]] = None
        for item in items:
            if (
                current is not None
                and str(current.get('next_date') or '') == str(item.get('date') or '')
                and tuple(current.get('signature') or ()) == tuple(item.get('signature') or ())
            ):
                current['next_date'] = item['next_date']
                current['nights'] = _ival(current.get('nights'), 1, minimum=1) + 1
            else:
                if current is not None:
                    segments.append(current)
                current = {
                    'room_id': rid,
                    'room_label': room_labels.get(rid, str(rid)),
                    'room_type_id': room_types.get(rid, 0),
                    'date': item['date'],
                    'next_date': item['next_date'],
                    'signature': tuple(item['signature']),
                    'nights': 1,
                }
        if current is not None:
            segments.append(current)
        all_segments.extend(segments)

        if len(segments) <= 1:
            continue
        segment_text = '; '.join(
            f"{_hms_short_date(seg['date'])}→{_hms_short_date(seg['next_date'])}: {_hms_signature_label(tuple(seg['signature']))}"
            for seg in segments
        )
        message = (
            f"№{room_labels.get(rid, rid)} розбивається на {len(segments)} HMS stay-картки: {segment_text}. "
            'Один фізичний RoomID треба залишити в одному безперервному stay зі сталим складом.'
        )
        issue = {
            'type': 'room_reused_as_multiple_hms_stays',
            'room_id': rid,
            'room_label': room_labels.get(rid, str(rid)),
            'room_type_id': room_types.get(rid, 0),
            'segments': segments,
            'message': message,
        }
        issues.append(issue)
        for item in items:
            day_issue_map.setdefault(str(item.get('date') or ''), []).append(message)

    unique_rooms = len(appearances)
    segment_count = len(all_segments)
    return {
        'compatible': not issues,
        'issues': issues,
        'issue_count': len(issues),
        'rooms_unique': unique_rooms,
        'room_stays_count': segment_count,
        'extra_room_stays': max(0, segment_count - unique_rooms),
        'segments': all_segments,
        'day_issue_map': day_issue_map,
    }


def _hms_saved_options_for_day(row: Any, day: Dict[str, Any]) -> List[Dict[str, Any]]:
    payload = _quote_saved_availability_by_day(row).get(str(day.get('date') or '')) or {}
    if not payload:
        return []
    rows = _category_rows(
        payload,
        str(day.get('placement_mode') or _row_dict(row).get('placement_mode') or 'standard'),
        bool(_ival(day.get('include_extra'), _ival(_row_dict(row).get('include_extra'), 0), minimum=0)),
    )
    return _daily_room_options(rows)


def _hms_live_options_for_day(row: Any, day: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    d = str(day.get('date') or '')
    nd = str(day.get('next_date') or '')
    raw = _request_pms_live(d, nd)
    payload, _warnings = _validate_payload(raw, d, nd)
    rows = _category_rows(
        payload,
        str(day.get('placement_mode') or _row_dict(row).get('placement_mode') or 'standard'),
        bool(_ival(day.get('include_extra'), _ival(_row_dict(row).get('include_extra'), 0), minimum=0)),
    )
    return _daily_room_options(rows), payload


def _hms_profile_fits_option(profile: Dict[str, Any], option: Dict[str, Any]) -> bool:
    if _ival(profile.get('room_type_id'), 0) != _ival(option.get('room_type_id'), 0):
        return False
    adults = _ival(profile.get('adults'), 0, minimum=0)
    children = _ival(profile.get('children'), 0, minimum=0)
    paid = _ival(profile.get('paid_children'), 0, minimum=0)
    extra = _ival(profile.get('extra_beds'), 0, minimum=0)
    occupants = adults + children
    if adults <= 0 or paid > children:
        return False
    # The saved quote already carries the accepted capacity semantics used when it was
    # priced. For a replacement inside the same RoomTypeID, preserve that exact profile
    # instead of reinterpreting an old quote through today's editable capacity matrix.
    cap = _ival(profile.get('capacity_per_room'), 0, minimum=0)
    if cap > 0 and occupants > cap:
        return False
    if extra > _ival(profile.get('extra_capacity'), 0, minimum=0):
        return False
    return True


def _hms_assign_profiles_for_type(
    *, profiles: List[Dict[str, Any]], options: List[Dict[str, Any]], day_idx: int,
    room_state: Dict[int, Dict[str, Any]], early_allowed: Optional[set] = None,
    late_allowed: Optional[set] = None,
) -> Dict[int, Dict[str, Any]]:
    """Small exact bipartite assignment. Riverwood room-type groups are small (<=9 now)."""
    option_by_id: Dict[int, Dict[str, Any]] = {}
    option_order: Dict[int, int] = {}
    for idx, opt in enumerate(options or []):
        rid = _ival(opt.get('room_id'), _ival(opt.get('room_id_token'), 0), minimum=0)
        if rid <= 0:
            continue
        option_by_id[rid] = dict(opt)
        option_order[rid] = idx

    candidate_rows: Dict[int, List[Tuple[int, int]]] = {}
    for pidx, profile in enumerate(profiles):
        sig = _hms_room_signature(profile)
        original_id = _ival(profile.get('room_id'), 0, minimum=0)
        locked = bool(profile.get('manual_locked'))
        require_early = bool(profile.get('_hms_requires_early'))
        require_late = bool(profile.get('_hms_requires_late'))
        candidates: List[Tuple[int, int]] = []
        for rid, opt in option_by_id.items():
            if locked and rid != original_id:
                continue
            if not _hms_profile_fits_option(profile, opt):
                continue
            if require_early and early_allowed is not None and str(rid) not in early_allowed:
                continue
            if require_late and late_allowed is not None and str(rid) not in late_allowed:
                continue
            state = room_state.get(rid)
            if state is not None:
                # Reuse is allowed only as direct continuation of the SAME HMS stay.
                if _ival(state.get('last_day_idx'), -999) != day_idx - 1:
                    continue
                if tuple(state.get('signature') or ()) != sig:
                    continue
                continuity = True
            else:
                continuity = False

            # Cost: preserve exact current RoomID when safe; otherwise preserve a same-signature
            # continuous stay; only then consume a new free physical room.
            if rid == original_id and continuity:
                cost = 0
            elif rid == original_id and not continuity:
                cost = 1
            elif continuity:
                cost = 2
            else:
                cost = 10 + option_order.get(rid, 999)
            candidates.append((cost, rid))
        candidates.sort(key=lambda x: (x[0], x[1]))
        if not candidates:
            label = str(profile.get('room_label') or original_id or '?')
            raise ValueError(
                f'Не знайдено HMS-сумісної заміни для №{label} ({_hms_signature_label(sig)}). '
                'Потрібно вручну змінити фізичний номер або склад цієї ночі.'
            )
        candidate_rows[pidx] = candidates

    # Most constrained profiles first; locked profiles naturally have one candidate.
    order = sorted(range(len(profiles)), key=lambda i: (len(candidate_rows[i]), candidate_rows[i][0][0], i))
    best_cost: Optional[int] = None
    best: Dict[int, int] = {}

    def search(pos: int, used: set, cost: int, assignment: Dict[int, int]) -> None:
        nonlocal best_cost, best
        if best_cost is not None and cost >= best_cost:
            return
        if pos >= len(order):
            best_cost = cost
            best = dict(assignment)
            return
        pidx = order[pos]
        for cand_cost, rid in candidate_rows[pidx]:
            if rid in used:
                continue
            assignment[pidx] = rid
            used.add(rid)
            search(pos + 1, used, cost + cand_cost, assignment)
            used.remove(rid)
            assignment.pop(pidx, None)

    search(0, set(), 0, {})
    if len(best) != len(profiles):
        rt = _ival(profiles[0].get('room_type_id'), 0) if profiles else 0
        raise ValueError(
            f'Не вдалося побудувати однозначний HMS-сумісний набір фізичних номерів для RoomTypeID {rt}. '
            'Спробуйте ручну заміну одного з проблемних номерів.'
        )
    return {pidx: option_by_id[rid] for pidx, rid in best.items()}


def _hms_autofix_plan(row: Any, *, force_live: bool = False) -> Dict[str, Any]:
    """Build an HMS-compatible physical-room plan without changing nightly headcount.

    Occupancy profiles stay within the same RoomTypeID. A RoomID can be consumed only once,
    or continued on the immediately next night with the same composition. This prevents the
    writer from creating a second reservation-card for the same physical room.
    """
    q = _row_dict(row)
    schedule = _quote_daily_schedule_from_row(row)
    saved_by_day = _quote_occupancy_by_day(row)
    if not schedule:
        raise ValueError('У пропозиції немає денного плану для HMS-сумісного перерозподілу.')

    first_day = str(schedule[0].get('date') or '')
    last_day = str(schedule[-1].get('date') or '')
    first_plan = [dict(x) for x in saved_by_day.get(first_day, []) if isinstance(x, dict)]
    last_plan = [dict(x) for x in saved_by_day.get(last_day, []) if isinstance(x, dict)]
    quote_early = bool(_ival(q.get('early_checkin'), 0))
    quote_late = bool(_ival(q.get('late_checkout'), 0))
    explicit_early = any(bool(x.get('early_checkin')) for x in first_plan)
    explicit_late = any(bool(x.get('late_checkout')) for x in last_plan)

    early_allowed: Optional[set] = None
    late_allowed: Optional[set] = None
    if force_live and quote_early:
        a = date.fromisoformat(str(q.get('arrival') or first_day))
        early_allowed, _labels, _warnings = _available_room_tokens_for_period((a - timedelta(days=1)).isoformat(), a.isoformat())
    if force_live and quote_late:
        d = date.fromisoformat(str(q.get('departure') or schedule[-1].get('next_date') or ''))
        late_allowed, _labels, _warnings = _available_room_tokens_for_period(d.isoformat(), (d + timedelta(days=1)).isoformat())

    room_state: Dict[int, Dict[str, Any]] = {}
    plans_by_day: Dict[str, List[Dict[str, Any]]] = {}
    changes: List[Dict[str, Any]] = []
    availability_source = 'live' if force_live else 'saved_snapshot'

    for day_idx, day in enumerate(schedule):
        d = str(day.get('date') or '')
        original_plan = [dict(x) for x in saved_by_day.get(d, []) if isinstance(x, dict)]
        if not original_plan:
            raise ValueError(f'{d}: у збереженій пропозиції немає фізичних номерів.')
        if force_live:
            options, _payload = _hms_live_options_for_day(row, day)
        else:
            options = _hms_saved_options_for_day(row, day)
        if not options:
            raise ValueError(f'{d}: немає каталогу фізичних номерів для HMS-сумісного перерозподілу.')

        # Mark stay-time requirements on the occupancy profile. The selected service follows
        # the guest/room stay to the replacement physical room and is live-checked on APPLY.
        profiles = []
        for source in original_plan:
            profile = dict(source)
            if day_idx == 0 and quote_early:
                profile['_hms_requires_early'] = bool(source.get('early_checkin')) if explicit_early else True
            else:
                profile['_hms_requires_early'] = False
            if day_idx == len(schedule) - 1 and quote_late:
                profile['_hms_requires_late'] = bool(source.get('late_checkout')) if explicit_late else True
            else:
                profile['_hms_requires_late'] = False
            profiles.append(profile)

        by_type: Dict[int, List[Tuple[int, Dict[str, Any]]]] = {}
        for idx, profile in enumerate(profiles):
            by_type.setdefault(_ival(profile.get('room_type_id'), 0), []).append((idx, profile))
        day_result: List[Optional[Dict[str, Any]]] = [None] * len(profiles)

        for rt, indexed_profiles in by_type.items():
            type_profiles = [p for _idx, p in indexed_profiles]
            type_options = [o for o in options if _ival(o.get('room_type_id'), 0) == rt]
            assigned = _hms_assign_profiles_for_type(
                profiles=type_profiles,
                options=type_options,
                day_idx=day_idx,
                room_state=room_state,
                early_allowed=early_allowed,
                late_allowed=late_allowed,
            )
            for local_idx, profile in enumerate(type_profiles):
                global_idx = indexed_profiles[local_idx][0]
                opt = dict(assigned[local_idx])
                original_id = _ival(profile.get('room_id'), 0, minimum=0)
                new_id = _ival(opt.get('room_id'), _ival(opt.get('room_id_token'), 0), minimum=0)
                item = dict(opt)
                for key in (
                    'adults', 'children', 'paid_children', 'extra_beds',
                    'resident_adults', 'nonresident_adults', 'tourist_tax_exempt_adults',
                    'manual_locked', 'manual_source',
                    'base_capacity', 'extra_capacity', 'capacity_per_room',
                    'room_capacity_rule', 'bed_capacity_rule', 'portable_standard_bed',
                ):
                    if key in profile:
                        item[key] = profile.get(key)
                item['key'] = str(profile.get('key') or f'hmsfix_{d.replace("-", "")}_{global_idx + 1}')
                item['position'] = _ival(profile.get('position'), global_idx + 1, minimum=1)
                item['occupants'] = _ival(profile.get('adults'), 0, minimum=0) + _ival(profile.get('children'), 0, minimum=0)
                if day_idx == 0:
                    item['early_checkin'] = bool(profile.get('_hms_requires_early'))
                elif 'early_checkin' in profile:
                    item['early_checkin'] = bool(profile.get('early_checkin'))
                if day_idx == len(schedule) - 1:
                    item['late_checkout'] = bool(profile.get('_hms_requires_late'))
                elif 'late_checkout' in profile:
                    item['late_checkout'] = bool(profile.get('late_checkout'))
                day_result[global_idx] = item

                sig = _hms_room_signature(profile)
                room_state[new_id] = {
                    'signature': sig,
                    'last_day_idx': day_idx,
                    'room_type_id': rt,
                }
                if new_id != original_id:
                    changes.append({
                        'date': d,
                        'from_room_id': original_id,
                        'from_room': str(profile.get('room_label') or original_id),
                        'to_room_id': new_id,
                        'to_room': str(item.get('room_label') or new_id),
                        'room_type_id': rt,
                        'composition': _hms_signature_label(sig),
                    })

        finished = [dict(x) for x in day_result if isinstance(x, dict)]
        expected_a = _ival(day.get('adults'), 0, minimum=0)
        expected_c = _ival(day.get('children'), 0, minimum=0)
        expected_pc = _ival(day.get('paid_children'), 0, minimum=0)
        _validate_room_plan(finished, expected_a, expected_c, expected_pc)
        plans_by_day[d] = finished

    after = _hms_compatibility_from_plans(schedule, plans_by_day)
    if not after.get('compatible'):
        raise ValueError('Автоперерозподіл не прибрав усі повторні HMS stay-картки одного RoomID.')

    change_map: Dict[str, List[str]] = {}
    for ch in changes:
        text = f"№{ch['from_room']} → №{ch['to_room']} · {ch['composition']}"
        change_map.setdefault(str(ch['date']), []).append(text)
    return {
        'ok': True,
        'source': availability_source,
        'plans_by_day': plans_by_day,
        'changes': changes,
        'change_count': len(changes),
        'change_map': change_map,
        'compatible_after': True,
        'rooms_unique_after': after.get('rooms_unique'),
        'room_stays_count_after': after.get('room_stays_count'),
    }


def _hms_compatibility_report(row: Any, *, include_proposal: bool = True) -> Dict[str, Any]:
    schedule = _quote_daily_schedule_from_row(row)
    plans = _quote_occupancy_by_day(row)
    report = _hms_compatibility_from_plans(schedule, plans)
    proposal: Dict[str, Any] = {'ok': False, 'changes': [], 'change_map': {}, 'error': ''}
    if include_proposal and not report.get('compatible'):
        try:
            proposal = _hms_autofix_plan(row, force_live=False)
            # Do not expose the full room plans to the template/browser; APPLY recomputes live.
            proposal = {k: v for k, v in proposal.items() if k != 'plans_by_day'}
        except Exception as exc:
            proposal = {
                'ok': False,
                'changes': [],
                'change_map': {},
                'error': _pricing_error_for_manager(exc),
                'source': 'saved_snapshot',
            }
    report['proposal'] = proposal
    return report


def _hms_autofix_change_summary(changes: List[Dict[str, Any]], limit: int = 12) -> str:
    parts = [
        f"{_hms_short_date(ch.get('date'))}: №{ch.get('from_room')}→№{ch.get('to_room')}"
        for ch in (changes or [])
    ]
    if len(parts) > limit:
        return '; '.join(parts[:limit]) + f'; +ще {len(parts) - limit}'
    return '; '.join(parts) if parts else 'фізичні номери без змін'


def _hms_hydrate_autofix_room_plan_live(
    *, day_date: str, rows: List[Dict[str, Any]], desired_plan: List[Dict[str, Any]],
    adults: int, children: int, paid_children: int,
) -> List[Dict[str, Any]]:
    """Reconfirm replacement RoomIDs live while preserving the saved quote's capacity profile."""
    options = _daily_room_options(rows)
    catalog = {str(x.get('room_id_token') or x.get('room_id') or ''): dict(x) for x in options}
    out: List[Dict[str, Any]] = []
    seen = set()
    for idx, desired in enumerate(desired_plan or []):
        token = str(desired.get('room_id') or '').strip()
        if not token or token in seen:
            raise ValueError(f'{day_date}: автоплан містить порожній або дубльований RoomID {token!r}.')
        seen.add(token)
        live = catalog.get(token)
        if not live:
            raise ValueError(
                f"{day_date}: запропонований номер №{desired.get('room_label') or token} уже не вільний. "
                'Запустіть автовиправлення ще раз — система підбере інший live-номер.'
            )
        expected_rt = _ival(desired.get('room_type_id'), 0)
        actual_rt = _ival(live.get('room_type_id'), 0)
        if expected_rt != actual_rt:
            raise ValueError(
                f"{day_date}: номер №{live.get('room_label') or token} змінив RoomTypeID {expected_rt}→{actual_rt}."
            )
        item = dict(live)
        for key in (
            'key', 'position', 'adults', 'children', 'paid_children', 'extra_beds',
            'resident_adults', 'nonresident_adults', 'tourist_tax_exempt_adults',
            'manual_locked', 'manual_source', 'early_checkin', 'late_checkout',
            'base_capacity', 'extra_capacity', 'capacity_per_room',
            'room_capacity_rule', 'bed_capacity_rule', 'portable_standard_bed',
        ):
            if key in desired:
                item[key] = desired.get(key)
        item['occupants'] = _ival(item.get('adults'), 0, minimum=0) + _ival(item.get('children'), 0, minimum=0)
        out.append(item)
    _validate_room_plan(out, adults, children, paid_children)
    return out


def _hms_recalculate_exact_daily_plans(row: Any, plans_by_day: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    """Live-reprice and validate an already chosen exact physical-room plan for every night."""
    q = _row_dict(row)
    schedule = [dict(x) for x in _quote_daily_schedule_from_row(row)]
    if not schedule:
        raise ValueError('У пропозиції немає збереженого денного плану.')
    price_list_id = _ival(q.get('price_list_id'), DEFAULT_BASE_PRICE_LIST_ID, minimum=0)
    rate_plan_id = _ival(q.get('rate_plan_id'), 0, minimum=0)
    if rate_plan_id <= 0:
        rate_plan_id, _resolution = _effective_quote_rate_plan_id(price_list_id)

    conn = _db()
    day_results: List[Dict[str, Any]] = []
    cache_days: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for day in schedule:
        d = str(day.get('date') or '')
        nd = str(day.get('next_date') or '')
        adults = _ival(day.get('adults'), 0, minimum=0)
        children = _ival(day.get('children'), 0, minimum=0)
        paid_children = _ival(day.get('paid_children'), 0, minimum=0)
        guest_count = adults + children
        if guest_count <= 0:
            day_results.append({
                **day, 'rows': [], 'room_plan': [], 'room_options': [],
                'summary': {'rows': [], 'used_rooms': 0, 'allocated_capacity': 0, 'configured_capacity': 0, 'shortage': 0, 'spare_places': 0, 'fits': True},
                'pricing': {'ok': True, 'rooms': [], 'hms_accommodation_total': 0, 'stay_total_before_tourist_tax': 0, 'tourist_tax_total': 0, 'stay_total': 0, 'currency': str(q.get('currency') or 'UAH'), 'booking_restrictions': [], 'booking_allowed': True},
                'pricing_daily': {'days': [{'date': d, 'next_date': nd, 'date_label': day.get('date_label'), 'next_date_label': day.get('next_date_label'), 'lines': [], 'total': 0, 'base_total': 0, 'extra_total': 0}], 'period_extras': [], 'has_daily_rates': True, 'currency': str(q.get('currency') or 'UAH')},
                'available_whole_stay': 0, 'active_rooms_whole_stay': 0,
            })
            continue
        raw = _request_pms_live(d, nd)
        payload, day_warnings = _validate_payload(raw, d, nd)
        _sync_categories_from_payload(conn, payload)
        cache_days.append({'date': d, 'next_date': nd, 'payload': payload})
        warnings.extend([f'{day.get("date_label")}: {x}' for x in day_warnings])
        rows = _category_rows(payload, str(day.get('placement_mode') or 'standard'), bool(day.get('include_extra')))
        desired = [dict(x) for x in (plans_by_day.get(d) or []) if isinstance(x, dict)]
        if not desired:
            raise ValueError(f'{d}: автоперерозподіл не повернув фізичні номери.')
        room_plan = _hms_hydrate_autofix_room_plan_live(
            day_date=d,
            rows=rows,
            desired_plan=desired,
            adults=adults,
            children=children,
            paid_children=paid_children,
        )
        summary = _manual_summary_from_room_plan(rows, room_plan, guest_count)
        pricing = _quote_room_plan(
            arrival=d, departure=nd, room_plan=room_plan,
            price_list_id=price_list_id, rate_plan_id=rate_plan_id,
        )
        statement = _pricing_daily_statement(pricing, d, nd)
        if not statement.get('days'):
            raise RuntimeError(f'{day.get("date_label")}: не отримано точну денну вартість.')
        day_results.append({
            **day, 'rows': rows, 'room_plan': room_plan, 'room_options': _daily_room_options(rows),
            'summary': summary, 'pricing': pricing, 'pricing_daily': statement,
            'available_whole_stay': _ival(payload.get('available_whole_stay'), 0, minimum=0),
            'active_rooms_whole_stay': _ival(payload.get('active_rooms_whole_stay'), 0, minimum=0),
        })

    conn.commit()
    restrictions = _daily_booking_restrictions(day_results)
    pricing = _compose_daily_pricing(day_results, restrictions)
    pricing_daily = _pricing_daily_statement(pricing, str(q.get('arrival') or ''), str(q.get('departure') or ''))
    first_plan = list((day_results[0].get('room_plan') or [])) if day_results else []
    last_plan = list((day_results[-1].get('room_plan') or [])) if day_results else []
    stay_time = _stay_time_availability_for_plans(
        arrival=str(q.get('arrival') or ''), departure=str(q.get('departure') or ''),
        first_room_plan=first_plan, last_room_plan=last_plan,
        request_early_all=bool(_ival(q.get('early_checkin'), 0)),
        request_late_all=bool(_ival(q.get('late_checkout'), 0)),
        strict_explicit=True,
    )
    pricing, pricing_daily = _apply_stay_time_surcharges(
        pricing, pricing_daily,
        early_checkin=bool(stay_time.get('early_requested_count')),
        late_checkout=bool(stay_time.get('late_requested_count')),
        early_room_labels=stay_time.get('early_room_labels') or [],
        late_room_labels=stay_time.get('late_room_labels') or [],
        availability_meta=stay_time,
    )
    pricing.update(_tourist_tax_estimate(schedule))
    commercial = _commercial_terms(
        pricing.get('commercial_accommodation_total', pricing.get('hms_accommodation_total')),
        q.get('commercial_discount_percent'),
    )
    allocation = _daily_allocation_snapshot(day_results)
    occupancy_daily = [
        {'date': x.get('date'), 'next_date': x.get('next_date'), 'room_plan': x.get('room_plan') or []}
        for x in day_results
    ]
    after_plans = {str(x.get('date') or ''): list(x.get('room_plan') or []) for x in day_results}
    after_compat = _hms_compatibility_from_plans(schedule, after_plans)
    if not after_compat.get('compatible'):
        raise ValueError('Після live-перевірки розміщення знову стало несумісним з HMS. Зміни не збережено.')

    max_guests = max((_ival(x.get('guest_count'), 0) for x in schedule), default=0)
    max_adults = max((_ival(x.get('adults'), 0) for x in schedule), default=0)
    max_children = max((_ival(x.get('children'), 0) for x in schedule), default=0)
    max_capacity = max((_ival((x.get('summary') or {}).get('allocated_capacity'), 0) for x in day_results), default=0)
    max_spare = max((_ival((x.get('summary') or {}).get('spare_places'), 0) for x in day_results), default=0)
    min_available = min((_ival(x.get('available_whole_stay'), 0) for x in day_results if _ival(x.get('guest_count'), 0) > 0), default=0)
    cache_payload = {
        '_daily_mode': True,
        'nights': len(schedule),
        'source': 'PMS Availability Sidecar',
        'days': cache_days,
        'daily_schedule': schedule,
    }
    return {
        'day_results': day_results,
        'pricing': pricing,
        'commercial': commercial,
        'allocation_snapshot': allocation,
        'occupancy_daily': occupancy_daily,
        'cache_payload': cache_payload,
        'daily_schedule': schedule,
        'warnings': warnings,
        'max_guests': max_guests,
        'max_adults': max_adults,
        'max_children': max_children,
        'max_capacity': max_capacity,
        'max_spare': max_spare,
        'min_available': min_available,
        'hms_compatibility': after_compat,
    }


def _hms_booking_payload(row: Any) -> Dict[str, Any]:
    """Build a traceable booking payload from the exact saved quote snapshot.

    This function is non-destructive. It never creates an HMS reservation and never reads
    the latest calculator session/global result. The future writer must use this frozen
    payload plus a fresh preflight.
    """
    q = _row_dict(row)
    if not q:
        raise ValueError('Порожня пропозиція.')
    if str(q.get('tariff_status') or '') != 'live_hms' or q.get('stay_total_before_tourist_tax') is None:
        raise ValueError('Для передачі в HMS потрібна збережена пропозиція з актуальною live HMS ціною.')
    if _ival(q.get('shortage'), 0, minimum=0) > 0:
        raise ValueError('Пропозиція має дефіцит місць і не може бути підготовлена до бронювання.')

    arrival = str(q.get('arrival') or '')
    departure = str(q.get('departure') or '')
    _parse_dates(arrival, departure)
    schedule = _quote_daily_schedule_from_row(q)
    occupancy_by_day = _quote_occupancy_by_day(q)
    if not schedule:
        raise ValueError('У пропозиції немає нічного складу групи.')
    nights_out: List[Dict[str, Any]] = []
    all_room_ids = set()
    for day in schedule:
        day_date = str(day.get('date') or '')
        next_date = str(day.get('next_date') or '')
        if not day_date or not next_date:
            raise ValueError('У нічному плані пропозиції відсутня дата.')
        plan = [dict(x) for x in (occupancy_by_day.get(day_date) or []) if isinstance(x, dict)]
        if not plan:
            raise ValueError(f'{day_date}: у збереженому snapshot немає фізичних номерів.')
        seen = set()
        actual_adults = actual_children = actual_paid = 0
        rooms: List[Dict[str, Any]] = []
        for room in plan:
            token = str(room.get('room_id') or '').strip()
            if not token:
                raise ValueError(f'{day_date}: знайдено номер без RoomID.')
            try:
                room_id = int(token)
            except Exception as exc:
                raise ValueError(f'{day_date}: некоректний RoomID {token!r}.') from exc
            if room_id in seen:
                raise ValueError(f'{day_date}: фізичний RoomID {room_id} використано двічі.')
            seen.add(room_id)
            all_room_ids.add(room_id)
            rt = _ival(room.get('room_type_id'), 0, minimum=0)
            if rt <= 0 or rt == 13:
                raise ValueError(f'{day_date}: номер {room.get("room_label") or room_id} має некоректний RoomTypeID {rt}.')
            adults = _ival(room.get('adults'), 0, minimum=0)
            children = _ival(room.get('children'), 0, minimum=0)
            paid_children = _ival(room.get('paid_children'), 0, minimum=0)
            extra_beds = _ival(room.get('extra_beds'), 0, minimum=0)
            if adults <= 0:
                raise ValueError(f'{day_date}: у номері {room.get("room_label") or room_id} немає дорослого.')
            if paid_children > children:
                raise ValueError(f'{day_date}: платних дітей більше, ніж дітей у номері {room.get("room_label") or room_id}.')
            actual_adults += adults
            actual_children += children
            actual_paid += paid_children
            pricing_occ = _pms_pricing_occupancy(room)
            rooms.append({
                'room_id': room_id,
                'room_number': str(room.get('room_label') or room_id),
                'room_type_id': rt,
                'category': str(room.get('category') or ROOM_TYPE_NAMES.get(rt) or ''),
                'adults': adults,
                'children': children,
                'paid_children': paid_children,
                'extra_beds': extra_beds,
                'base_capacity': _ival(room.get('base_capacity'), 0, minimum=0),
                'extra_capacity': _ival(room.get('extra_capacity'), 0, minimum=0),
                'pricing_occupancy': {
                    'adults': pricing_occ['adults'],
                    'children': pricing_occ['children'],
                    'paid_children': pricing_occ['paid_children'],
                    'extra_beds': pricing_occ['extra_beds'],
                },
                'early_checkin': bool(room.get('early_checkin')),
                'late_checkout': bool(room.get('late_checkout')),
            })
        expected_adults = _ival(day.get('adults'), 0, minimum=0)
        expected_children = _ival(day.get('children'), 0, minimum=0)
        expected_paid = _ival(day.get('paid_children'), 0, minimum=0)
        if (actual_adults, actual_children, actual_paid) != (expected_adults, expected_children, expected_paid):
            raise ValueError(
                f'{day_date}: склад фізичних номерів {actual_adults}+{actual_children} (платних дітей {actual_paid}) '
                f'не збігається зі складом ночі {expected_adults}+{expected_children} (платних дітей {expected_paid}).'
            )
        nights_out.append({
            'date': day_date,
            'next_date': next_date,
            'adults': expected_adults,
            'children': expected_children,
            'paid_children': expected_paid,
            'guest_count': expected_adults + expected_children,
            'placement_mode': str(day.get('placement_mode') or q.get('placement_mode') or ''),
            'include_extra': _ival(day.get('include_extra'), _ival(q.get('include_extra'), 0), minimum=0, maximum=1),
            'rooms': rooms,
        })

    revision_no = _ival(q.get('revision_no'), 1, minimum=1)
    guest_list = _guest_list_from_json(q.get('guest_list_json') or '[]')
    hms_price_list = _hms_booking_price_list_mapping(q.get('commercial_discount_percent') or 0)
    core: Dict[str, Any] = {
        'contract_version': 'riverwood-hms-booking-v1',
        'quote_id': str(q.get('quote_id') or ''),
        'quote_number': str(q.get('quote_number') or ''),
        'quote_revision': revision_no,
        'client_name': str(q.get('client_name') or ''),
        'title': str(q.get('title') or ''),
        'arrival': arrival,
        'departure': departure,
        'nights': _ival(q.get('nights'), len(nights_out), minimum=1),
        'early_checkin': bool(q.get('early_checkin')),
        'late_checkout': bool(q.get('late_checkout')),
        'price_list_id': _ival(q.get('price_list_id'), 0, minimum=0),
        'rate_plan_id': _ival(q.get('rate_plan_id'), 0, minimum=0),
        'currency': str(q.get('currency') or 'UAH'),
        'commercial_total_reference': _money_float(_money_decimal(q.get('commercial_total') or q.get('stay_total_before_tourist_tax'))),
        'commercial_discount_percent': _money_float(_money_decimal(q.get('commercial_discount_percent') or 0)),
        'commercial_discount_amount': _money_float(_money_decimal(q.get('commercial_discount_amount') or 0)),
        'hms_booking_price_list_id': int(hms_price_list['price_list_id']) if hms_price_list else 0,
        'hms_booking_price_list_name': str(hms_price_list['price_list_name']) if hms_price_list else '',
        'commercial_note': str(q.get('commercial_note') or ''),
        'manager_note': str(q.get('manager_note') or ''),
        'guest_note': str(q.get('guest_note') or ''),
        'guest_list': guest_list,
        'guest_list_count': len(guest_list),
        'guest_list_complete': len(guest_list) >= _ival(q.get('guest_count'), 0, minimum=0),
        'rooms_unique': len(all_room_ids),
        'nights_plan': nights_out,
    }
    canonical = json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    core['snapshot_sha256'] = hashlib.sha256(canonical).hexdigest()
    core['idempotency_key'] = f"{core['quote_id']}:{revision_no}:{core['snapshot_sha256'][:16]}"
    return core


def _booking_room_stays(payload: Dict[str, Any], check_in_time: str, check_out_time: str) -> List[Dict[str, Any]]:
    """Merge consecutive nights in the same physical room into continuous stay windows."""
    by_room: Dict[int, List[Dict[str, Any]]] = {}
    for night in payload.get('nights_plan') or []:
        for room in night.get('rooms') or []:
            item = dict(room)
            item['date'] = str(night.get('date') or '')
            item['next_date'] = str(night.get('next_date') or '')
            by_room.setdefault(_ival(room.get('room_id'), 0), []).append(item)
    out: List[Dict[str, Any]] = []
    for room_id, items in by_room.items():
        items.sort(key=lambda x: x['date'])
        current: Optional[Dict[str, Any]] = None
        for item in items:
            start = _booking_dt(item['date'], check_in_time, '15:00')
            end = _booking_dt(item['next_date'], check_out_time, '12:00')
            if current is not None and str(current.get('next_date')) == item['date']:
                current['next_date'] = item['next_date']
                current['end'] = end
                current['nights'] += 1
                continue
            if current is not None:
                out.append(current)
            current = {
                'room_id': room_id,
                'room_number': item.get('room_number'),
                'room_type_id': item.get('room_type_id'),
                'category': item.get('category'),
                'date': item['date'], 'next_date': item['next_date'],
                'start': start, 'end': end, 'nights': 1,
            }
        if current is not None:
            out.append(current)
    return out


def _hms_booking_preflight(row: Any, timetable: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Live non-destructive verification of the exact physical rooms before HMS write."""
    q = _row_dict(row)
    payload = _hms_booking_payload(q)
    compatibility = _hms_compatibility_report(q, include_proposal=False)
    if not compatibility.get('compatible'):
        conflicts = [
            {
                'type': 'hms_room_stay_split',
                'room_id': issue.get('room_id'),
                'room_number': issue.get('room_label'),
                'message': issue.get('message'),
            }
            for issue in compatibility.get('issues') or []
        ]
        return {
            'ok': True,
            'status': 'blocked',
            'booking_write_enabled': False,
            'booking_write_reason': 'Спочатку виправте розміщення: один фізичний RoomID не може розбиватися на кілька HMS stay-карток.',
            'quote_id': payload['quote_id'], 'quote_number': payload['quote_number'],
            'quote_revision': payload['quote_revision'], 'idempotency_key': payload['idempotency_key'],
            'snapshot_sha256': payload['snapshot_sha256'],
            'checked_at': _now(), 'timetable_generated_at': '',
            'source': 'operations_hms_compatibility',
            'checked_windows': [], 'checked_windows_count': 0,
            'conflicts': conflicts, 'conflict_count': len(conflicts), 'warnings': [],
            'rooms_unique': payload.get('rooms_unique'), 'nights': payload.get('nights'),
            'hms_compatibility': compatibility,
            'payload': payload,
        }
    tt = timetable if timetable is not None else _request_pms_timetable(payload['arrival'], payload['departure'], pad_days=1)
    if not isinstance(tt, dict) or not tt.get('ok'):
        raise ValueError('Не отримано актуальну шахматку HMS для preflight.')
    room_catalog = {_ival(x.get('room_id'), 0): dict(x) for x in (tt.get('rooms') or []) if isinstance(x, dict)}
    occupancies = [dict(x) for x in (tt.get('occupancies') or []) if isinstance(x, dict)]
    check_in_time = str(tt.get('check_in_time') or '15:00')
    check_out_time = str(tt.get('check_out_time') or '12:00')
    conflicts: List[Dict[str, Any]] = []
    checked: List[Dict[str, Any]] = []

    def check_window(room: Dict[str, Any], start: datetime, end: datetime, check_kind: str) -> None:
        room_id = _ival(room.get('room_id'), 0)
        meta = room_catalog.get(room_id)
        if not meta:
            conflicts.append({
                'type': 'room_missing', 'room_id': room_id, 'room_number': room.get('room_number'),
                'message': f"Номер {room.get('room_number') or room_id}: RoomID відсутній в актуальній шахматці HMS.",
            })
            return
        expected_rt = _ival(room.get('room_type_id'), 0)
        actual_rt = _ival(meta.get('room_type_id'), 0)
        if expected_rt != actual_rt:
            conflicts.append({
                'type': 'room_type_changed', 'room_id': room_id, 'room_number': room.get('room_number'),
                'message': f"Номер {room.get('room_number') or room_id}: RoomTypeID змінився {expected_rt} → {actual_rt}.",
            })
            return
        overlaps: List[Dict[str, Any]] = []
        for occ in occupancies:
            if _ival(occ.get('room_id'), 0) != room_id:
                continue
            occ_start = _booking_parse_iso_dt(occ.get('start'))
            occ_end = _booking_parse_iso_dt(occ.get('end'))
            if not occ_start or not occ_end:
                continue
            if _booking_intervals_overlap(start, end, occ_start, occ_end):
                overlaps.append(occ)
        if overlaps:
            first = overlaps[0]
            conflicts.append({
                'type': 'occupied' if str(first.get('kind') or '') == 'booking' else 'service_block',
                'room_id': room_id, 'room_number': str(meta.get('room_number') or room.get('room_number') or room_id),
                'check_kind': check_kind, 'start': start.isoformat(timespec='minutes'), 'end': end.isoformat(timespec='minutes'),
                'message': (
                    f"Номер {meta.get('room_number') or room.get('room_number') or room_id} недоступний: "
                    f"{first.get('label') or ('бронювання' if first.get('kind') == 'booking' else 'службове блокування')} "
                    f"{first.get('start') or ''} → {first.get('end') or ''}."
                ),
            })
            return
        checked.append({
            'room_id': room_id, 'room_number': str(meta.get('room_number') or room.get('room_number') or room_id),
            'room_type_id': expected_rt, 'check_kind': check_kind,
            'start': start.isoformat(timespec='minutes'), 'end': end.isoformat(timespec='minutes'),
        })

    stays = _booking_room_stays(payload, check_in_time, check_out_time)
    for stay in stays:
        check_window(stay, stay['start'], stay['end'], 'stay')

    nights = payload.get('nights_plan') or []
    if nights:
        first_rooms = [dict(x) for x in (nights[0].get('rooms') or [])]
        last_rooms = [dict(x) for x in (nights[-1].get('rooms') or [])]
        if payload.get('early_checkin'):
            explicitly_selected = [x for x in first_rooms if x.get('early_checkin')]
            targets = explicitly_selected or first_rooms
            prev_day = (date.fromisoformat(payload['arrival']) - timedelta(days=1)).isoformat()
            for room in targets:
                start = _booking_dt(prev_day, check_in_time, '15:00')
                end = _booking_dt(payload['arrival'], check_out_time, '12:00')
                check_window(room, start, end, 'early_checkin_previous_night')
        if payload.get('late_checkout'):
            explicitly_selected = [x for x in last_rooms if x.get('late_checkout')]
            targets = explicitly_selected or last_rooms
            next_day = (date.fromisoformat(payload['departure']) + timedelta(days=1)).isoformat()
            for room in targets:
                start = _booking_dt(payload['departure'], check_in_time, '15:00')
                end = _booking_dt(next_day, check_out_time, '12:00')
                check_window(room, start, end, 'late_checkout_next_night')

    ready = not conflicts
    warnings: List[str] = []
    if not payload.get('guest_list_complete'):
        warnings.append(
            f"Список гостей заповнено {payload.get('guest_list_count') or 0} із {q.get('guest_count') or 0}. "
            'Це не блокує групове бронювання: HMS reservation-card склад формується з фактичного розміщення по номерах; ПІБ можна доповнити окремо.'
        )
    return {
        'ok': True,
        'status': 'ready' if ready else 'blocked',
        'booking_write_enabled': True,
        'booking_write_reason': (
            'Live preflight готовий. Dedicated writer :8085 виконує fail-closed транзакцію: Reservation POST → GroupCard → точні RoomID → ValidateRoom → ReserveGroup 1/2/3.'
        ),
        'quote_id': payload['quote_id'], 'quote_number': payload['quote_number'],
        'quote_revision': payload['quote_revision'], 'idempotency_key': payload['idempotency_key'],
        'snapshot_sha256': payload['snapshot_sha256'],
        'checked_at': _now(), 'timetable_generated_at': str(tt.get('generated_at') or ''),
        'source': str(tt.get('source') or 'hms_timetable'),
        'checked_windows': checked, 'checked_windows_count': len(checked),
        'conflicts': conflicts, 'conflict_count': len(conflicts), 'warnings': warnings,
        'rooms_unique': payload.get('rooms_unique'), 'nights': payload.get('nights'),
        'payload': payload,
    }


def _hms_booking_state(row: Any) -> Dict[str, Any]:
    q = _row_dict(row)
    raw_status = str(q.get('hms_booking_status') or '').strip()
    try:
        preflight = json.loads(q.get('hms_booking_preflight_json') or '{}')
        if not isinstance(preflight, dict):
            preflight = {}
    except Exception:
        preflight = {}
    try:
        diagnostic = json.loads(q.get('hms_booking_bridge_diagnostic_json') or '{}')
        if not isinstance(diagnostic, dict):
            diagnostic = {}
    except Exception:
        diagnostic = {}

    current_revision = _ival(q.get('revision_no'), 1, minimum=1)
    checked_revision = _ival(q.get('hms_booking_quote_revision'), 0, minimum=0)
    legacy_statuses = {
        'bridge_waiting','bridge_error','bridge_group_ready','draft_error','draft_ready',
        'draft_unverified','draft_verified','snapshot_prepared'
    }
    legacy_temp_group_id = ''
    if raw_status in legacy_statuses:
        legacy_temp_group_id = str(q.get('hms_booking_group_id') or q.get('hms_booking_bridge_group_id') or '').strip()

    if raw_status == 'booked':
        status = 'booked'
    elif raw_status == 'booking_uncertain':
        status = 'booking_uncertain'
    elif checked_revision and checked_revision != current_revision:
        status = 'stale'
    elif raw_status == 'error':
        status = 'error'
    elif isinstance(preflight, dict) and str(preflight.get('status') or '') in ('ready', 'blocked') and checked_revision == current_revision:
        status = str(preflight.get('status') or '')
    elif raw_status in ('ready','blocked','stale'):
        status = raw_status
    elif raw_status in legacy_statuses:
        status = 'not_checked'
    else:
        status = raw_status or 'not_checked'

    final_group_id = str(q.get('hms_booking_group_id') or '').strip() if status in ('booked','booking_uncertain') else ''
    compatibility = _hms_compatibility_report(q, include_proposal=True)
    if not compatibility.get('compatible') and status not in ('booked', 'booking_uncertain', 'error'):
        status = 'blocked'
    write_blockers: List[str] = []
    if not compatibility.get('compatible'):
        write_blockers.append(
            f"Розміщення несумісне з HMS: {compatibility.get('issue_count') or 0} фізичний(і) номер(и) "
            'розбиваються на кілька stay-карток. Скористайтесь «Автовиправити розміщення для HMS».'
        )
    hms_price_list = _hms_booking_price_list_mapping(q.get('commercial_discount_percent') or 0)
    if not hms_price_list:
        try:
            discount_text = f"{float(q.get('commercial_discount_percent') or 0):g}%"
        except Exception:
            discount_text = str(q.get('commercial_discount_percent') or '')
        write_blockers.append(
            f'Для комерційної знижки {discount_text} немає підтвердженого HMS PriceList. '
            'Автозапис залишається заблокованим, щоб не змінити фінансові умови.'
        )
    return {
        'status': status,
        'preflight': preflight,
        'preflight_at': q.get('hms_booking_preflight_at') or '',
        'preflight_by': q.get('hms_booking_preflight_by') or '',
        'checked_revision': checked_revision,
        'current_revision': current_revision,
        'stale': status == 'stale',
        'group_id': final_group_id,
        'group_account': (f"G{_ival(final_group_id, 0, minimum=0):010d}" if _ival(final_group_id, 0, minimum=0) > 0 else ''),
        'booking_created_at': q.get('hms_booking_created_at') or '',
        'booking_created_by': str(q.get('hms_booking_created_by') or ''),
        'last_error': str(q.get('hms_booking_last_error') or ''),
        'direct_diagnostic': diagnostic,
        'direct_has_diagnostic': bool(diagnostic),
        'legacy_temp_group_id': legacy_temp_group_id,
        'legacy_state_ignored': bool(raw_status in legacy_statuses),
        'write_blockers': write_blockers,
        'hms_compatibility': compatibility,
        'hms_price_list': hms_price_list or {},
        'can_book': bool(status == 'ready' and not write_blockers),
    }

def _hms_booking_new_reservation_url() -> str:
    base = (os.environ.get('HMS_BOOKING_BASE_URL') or os.environ.get('HMS_TIMETABLE_BASE_URL') or 'http://192.168.88.67').strip().rstrip('/')
    if not base.startswith(('http://', 'https://')):
        base = 'http://' + base
    return base + '/HMS/Base/Reservation.aspx?Action=1&hotelid=1&valuteid=1&cpw=true'


def _hms_bridge_pending_row(conn, actor: str):
    if not actor:
        return None
    return conn.execute("""
        SELECT * FROM accommodation_quotes
        WHERE hms_booking_bridge_started_by=?
          AND hms_booking_bridge_state IN ('waiting','opened')
          AND hms_booking_status='bridge_waiting'
        ORDER BY COALESCE(hms_booking_bridge_started_at, updated_at, created_at) DESC
        LIMIT 1
    """, (actor,)).fetchone()


@bp.get('/accommodation-calculator/api/hms-booking-bridge/pending')
def hms_booking_bridge_pending():
    """Authenticated browser-extension poll. Returns only a minimal launch envelope.

    v5.315 deliberately does not expose the full frozen reservation payload to the
    extension yet. The first live milestone is to prove that the already authenticated
    browser can open HMS NewReservation and return the newly allocated GroupID without
    executing ReserveGroupFirst/Second/Third.
    """
    ensure_accommodation_schema()
    actor = _actor()
    if not actor:
        return jsonify({'ok': False, 'error': 'not_authenticated'}), 401
    conn = _db()
    row = _hms_bridge_pending_row(conn, actor)
    if not row:
        return jsonify({'ok': True, 'job': None})
    q = _row_dict(row)
    current_revision = _ival(q.get('revision_no'), 1, minimum=1)
    checked_revision = _ival(q.get('hms_booking_quote_revision'), 0, minimum=0)
    if checked_revision != current_revision:
        return jsonify({'ok': False, 'error': 'stale_preflight'}), 409
    try:
        payload = json.loads(q.get('hms_booking_payload_json') or '{}')
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    try:
        preflight = json.loads(q.get('hms_booking_preflight_json') or '{}')
        if not isinstance(preflight, dict):
            preflight = {}
    except Exception:
        preflight = {}
    if not payload or str(payload.get('snapshot_sha256') or '') != str(preflight.get('snapshot_sha256') or ''):
        return jsonify({'ok': False, 'error': 'invalid_snapshot'}), 409
    return jsonify({
        'ok': True,
        'job': {
            'job_id': str(q.get('hms_booking_bridge_job_id') or ''),
            'quote_id': str(q.get('quote_id') or ''),
            'quote_number': str(q.get('quote_number') or ''),
            'quote_revision': current_revision,
            'idempotency_key': str(q.get('hms_booking_idempotency_key') or ''),
            'snapshot_sha256': str(payload.get('snapshot_sha256') or ''),
            'new_reservation_url': _hms_booking_new_reservation_url(),
            'phase': 'group_id_handshake',
        },
    })


@bp.post('/accommodation-calculator/api/hms-booking-bridge/report')
def hms_booking_bridge_report():
    ensure_accommodation_schema()
    actor = _actor()
    if not actor:
        return jsonify({'ok': False, 'error': 'not_authenticated'}), 401
    data = request.get_json(silent=True) or {}
    job_id = str(data.get('job_id') or '').strip()
    state = str(data.get('state') or '').strip().lower()
    message = str(data.get('message') or '').strip()[:1200]
    group_id = str(data.get('group_id') or '').strip()
    login_id = str(data.get('login_id') or '').strip()
    diagnostic = data.get('diagnostic')
    diagnostic_json = '{}'
    if isinstance(diagnostic, (dict, list)):
        try:
            encoded = json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':'))
            if len(encoded.encode('utf-8')) <= 250000:
                diagnostic_json = encoded
            else:
                diagnostic_json = json.dumps({'truncated': True, 'bytes': len(encoded.encode('utf-8'))}, separators=(',', ':'))
        except Exception:
            diagnostic_json = '{}'
    if not job_id or state not in ('opened', 'login_required', 'group_ready', 'error', 'timeout'):
        return jsonify({'ok': False, 'error': 'invalid_report'}), 400
    conn = _db()
    row = conn.execute('SELECT * FROM accommodation_quotes WHERE hms_booking_bridge_job_id=?', (job_id,)).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'job_not_found'}), 404
    q = _row_dict(row)
    if str(q.get('hms_booking_bridge_started_by') or '') != actor:
        return jsonify({'ok': False, 'error': 'job_owner_mismatch'}), 403
    now = _now()
    if state == 'group_ready':
        if not re.fullmatch(r'\d{1,12}', group_id) or int(group_id) <= 0:
            return jsonify({'ok': False, 'error': 'invalid_group_id'}), 400
        if login_id and (not re.fullmatch(r'\d{1,12}', login_id) or int(login_id) <= 0):
            return jsonify({'ok': False, 'error': 'invalid_login_id'}), 400
        conn.execute("""
            UPDATE accommodation_quotes
            SET hms_booking_status='bridge_group_ready', hms_booking_bridge_state='group_ready',
                hms_booking_bridge_seen_at=?, hms_booking_bridge_group_id=?, hms_booking_bridge_login_id=?,
                hms_booking_bridge_diagnostic_json=?, hms_booking_bridge_error='', hms_booking_last_error='', updated_at=?, updated_by=?
            WHERE quote_id=?
        """, (now, group_id, login_id, diagnostic_json, now, actor, q['quote_id']))
        audit_action = 'hms_booking_bridge_group_ready'
        reason = f'GroupID={group_id}; loginID={login_id or "?"}; job={job_id}'
    elif state == 'opened':
        conn.execute("""
            UPDATE accommodation_quotes
            SET hms_booking_bridge_state='opened', hms_booking_bridge_seen_at=?, hms_booking_bridge_error='',
                updated_at=?, updated_by=? WHERE quote_id=?
        """, (now, now, actor, q['quote_id']))
        audit_action = 'hms_booking_bridge_opened'
        reason = f'job={job_id}'
    else:
        err = message or ('Потрібен вхід у HMS у цьому браузері.' if state == 'login_required' else 'HMS browser bridge не завершив handshake.')
        conn.execute("""
            UPDATE accommodation_quotes
            SET hms_booking_status='bridge_error', hms_booking_bridge_state=?, hms_booking_bridge_seen_at=?,
                hms_booking_bridge_error=?, hms_booking_last_error=?, updated_at=?, updated_by=?
            WHERE quote_id=?
        """, (state, now, err, err, now, actor, q['quote_id']))
        audit_action = 'hms_booking_bridge_failed'
        reason = f'{state}; {err}'
    conn.commit()
    _audit('accommodation_quote', str(q.get('quote_id') or ''), audit_action, new_value=state, reason=reason)
    return jsonify({'ok': True, 'state': state, 'quote_id': str(q.get('quote_id') or ''), 'group_id': group_id if state == 'group_ready' else ''})


@bp.get('/accommodation-calculator/quotes/<quote_id>/hms-bridge-diagnostic.json')
def hms_booking_bridge_diagnostic(quote_id: str):
    ensure_accommodation_schema()
    actor = _actor()
    if not actor:
        return jsonify({'ok': False, 'error': 'not_authenticated'}), 401
    conn = _db()
    row = conn.execute('SELECT quote_number,hms_booking_group_id,hms_booking_bridge_group_id,hms_booking_bridge_login_id,hms_booking_bridge_diagnostic_json FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
    if not row:
        abort(404)
    q = _row_dict(row)
    try:
        diagnostic = json.loads(q.get('hms_booking_bridge_diagnostic_json') or '{}')
    except Exception:
        diagnostic = {}
    body = json.dumps({
        'quote_number': str(q.get('quote_number') or ''),
        'group_id': str(q.get('hms_booking_group_id') or q.get('hms_booking_bridge_group_id') or ''),
        'login_id': str(q.get('hms_booking_bridge_login_id') or ''),
        'diagnostic': diagnostic,
    }, ensure_ascii=False, indent=2)
    filename = re.sub(r'[^A-Za-z0-9_.-]+', '_', str(q.get('quote_number') or quote_id)) + '_HMS_DIRECT_DIAGNOSTIC.json'
    return body, 200, {
        'Content-Type': 'application/json; charset=utf-8',
        'Content-Disposition': f'attachment; filename="{filename}"',
        'Cache-Control': 'no-store',
    }


def _render_quote_detail_response(
    *, conn, current_row: Any, row: Any, is_current_revision: bool, selected_revision: Any = None,
    manual_editor: Optional[Dict[str, Any]] = None, manual_preview: Optional[Dict[str, Any]] = None,
    manual_editor_error: str = '',
):
    current_row = _row_dict(current_row)
    row = _row_dict(row)
    try:
        allocation = json.loads(row['allocation_json'])
    except Exception:
        allocation = {'rows': []}
    try:
        occupancy = json.loads(row['occupancy_json'] or '[]')
    except Exception:
        occupancy = []
    try:
        pricing = json.loads(row['pricing_json'] or '{}')
    except Exception:
        pricing = {}
    try:
        daily_plan = json.loads(row['daily_plan_json'] or '[]')
        if not isinstance(daily_plan, list):
            daily_plan = []
    except Exception:
        daily_plan = []
    if not pricing.get('tourist_tax_estimate'):
        schedule_for_tax = daily_plan or _quote_daily_schedule_from_row(row)
        pricing.update(_tourist_tax_estimate(schedule_for_tax))
    guest_list = _guest_list_from_json(row['guest_list_json'] or '[]')
    revisions = conn.execute('SELECT * FROM accommodation_quote_revisions WHERE quote_id=? ORDER BY revision_no DESC LIMIT 50', (row['quote_id'],)).fetchall()
    if not pricing.get('tourist_tax_estimate'):
        schedule_for_tax = daily_plan or _quote_daily_schedule_from_row(row)
        pricing.update(_tourist_tax_estimate(schedule_for_tax))
    pricing_breakdown = _pricing_category_breakdown(pricing, _ival(row['nights'], 0, minimum=0))
    pricing_daily = _pricing_daily_statement(pricing, row['arrival'], row['departure'])
    manager_name = _employee_full_name(row['created_by'])
    booking_state = _hms_booking_state(row)
    version_created_at = row.get('_version_created_at') if isinstance(row, dict) else None
    return render_template(
        'accommodation_quote_detail.html', title=row['quote_number'], quote=row,
        allocation=allocation, occupancy=occupancy, pricing=pricing,
        pricing_breakdown=pricing_breakdown, pricing_daily=pricing_daily, daily_plan=daily_plan,
        manager_name=manager_name, guest_list=guest_list, revisions=revisions,
        is_current_revision=is_current_revision, selected_revision=selected_revision,
        current_revision_no=_ival(current_row['revision_no'], 1, minimum=1),
        version_created_at=version_created_at or row.get('updated_at') or row.get('created_at'),
        placement_labels=PLACEMENT_LABELS, strategy_labels=STRATEGY_LABELS,
        guest_type_labels=GUEST_TYPE_LABELS, guest_preference_labels=GUEST_PREFERENCE_LABELS,
        money_fmt=_money_text, manual_editor=manual_editor, manual_preview=manual_preview,
        manual_editor_error=manual_editor_error, booking_state=booking_state,
    )

@bp.route('/accommodation-calculator/quotes/<quote_id>', methods=['GET', 'POST'])
def quote_detail(quote_id: str):
    ensure_accommodation_schema()
    conn = _db()
    row = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
    if not row:
        abort(404)
    if request.method == 'POST':
        detail_action = (request.form.get('detail_action') or 'commercial').strip()
        if detail_action == 'hms_booking_reserve':
            current_status = str(row['hms_booking_status'] or '').strip()
            if current_status == 'booked':
                flash('Ця пропозиція вже заброньована в HMS. Повторне бронювання заблоковане.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            if current_status == 'booking_uncertain':
                flash('Повторне бронювання заблоковане: попередня HMS-транзакція має невизначений результат. Спочатку перевірте HMS вручну.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                # The destructive transaction always gets a brand-new live preflight and a
                # freshly reconstructed payload from this exact saved quote revision.
                result = _hms_booking_preflight(row)
                if str(result.get('status') or '') != 'ready':
                    raise ValueError(f"Live preflight більше не READY: конфліктів {result.get('conflict_count') or 0}.")
                payload = result.get('payload') or {}
                if not isinstance(payload, dict) or not payload:
                    raise ValueError('Live preflight не сформував booking snapshot.')
                now = _now()
                # Persist the exact preflight/payload before the long HMS transaction. Legacy
                # temporary GroupIDs are deliberately cleared and are never reused.
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status='ready', hms_booking_preflight_json=?, hms_booking_preflight_at=?,
                        hms_booking_preflight_by=?, hms_booking_quote_revision=?, hms_booking_idempotency_key=?,
                        hms_booking_payload_json=?, hms_booking_group_id='', hms_booking_created_at=NULL,
                        hms_booking_created_by='', hms_booking_last_error='',
                        hms_booking_bridge_diagnostic_json='{}', hms_booking_bridge_group_id='',
                        hms_booking_bridge_state='', hms_booking_bridge_error='', updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (
                    json.dumps({k:v for k,v in result.items() if k != 'payload'}, ensure_ascii=False, separators=(',', ':')),
                    result.get('checked_at') or now, _actor(), _ival(row['revision_no'], 1, minimum=1),
                    result.get('idempotency_key') or '', json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                    now, _actor(), quote_id,
                ))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_transaction_started',
                       new_value='ready', reason=str(result.get('snapshot_sha256') or ''))

                booked = _request_hms_booking_reserve(payload)
                group_id = _ival(booked.get('group_id'), 0, minimum=0)
                if group_id <= 0 or not bool(booked.get('reservation_confirmed')) or _ival(booked.get('reserve_steps_executed'), 0, minimum=0) != 3:
                    raise ValueError('HMS sidecar не підтвердив повне завершення бронювання.')
                booked_at = str(booked.get('booked_at') or _now())
                diagnostic = {
                    'adapter_version': str(booked.get('adapter_version') or ''),
                    'reservation_confirmed': True,
                    'reserve_steps_executed': 3,
                    'deduplicated': bool(booked.get('deduplicated')),
                    'group_id': group_id,
                    'group_account': str(booked.get('group_account') or f'G{group_id:010d}'),
                    'booked_at': booked_at,
                    'room_stays_count': _ival(booked.get('room_stays_count'), 0, minimum=0),
                    'rooms_unique': _ival(booked.get('rooms_unique'), 0, minimum=0),
                    'guest_ids_count': len(booked.get('guest_ids') or []),
                    'room_results': booked.get('room_results') if isinstance(booked.get('room_results'), list) else [],
                }
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status='booked', hms_booking_group_id=?, hms_booking_created_at=?,
                        hms_booking_created_by=?, hms_booking_last_error='', hms_booking_bridge_diagnostic_json=?,
                        updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (
                    str(group_id), booked_at, _actor(), json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')),
                    _now(), _actor(), quote_id,
                ))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_confirmed',
                       new_value=f'G{group_id:010d}', reason=f"ReserveGroup=3; snapshot={payload.get('snapshot_sha256') or ''}")
                flash(f'HMS бронювання підтверджено: G{group_id:010d}. Усі 3 ReserveGroup-кроки завершено.', 'success')
            except Exception as exc:
                booking_result = getattr(exc, 'booking_result', {})
                if not isinstance(booking_result, dict):
                    booking_result = {}
                uncertain = bool(booking_result.get('uncertain') or booking_result.get('automatic_retry_blocked'))
                group_id = _ival(booking_result.get('group_id'), 0, minimum=0)
                message = _pricing_error_for_manager(exc)
                diagnostic = dict(booking_result)
                diagnostic['error'] = str(booking_result.get('error') or message)
                diagnostic['uncertain'] = uncertain
                diagnostic['automatic_retry_blocked'] = uncertain
                if uncertain:
                    conn.execute("""
                        UPDATE accommodation_quotes
                        SET hms_booking_status='booking_uncertain', hms_booking_group_id=?,
                            hms_booking_last_error=?, hms_booking_bridge_diagnostic_json=?, updated_at=?, updated_by=?
                        WHERE quote_id=?
                    """, (
                        str(group_id) if group_id > 0 else '', message,
                        json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')), _now(), _actor(), quote_id,
                    ))
                    conn.commit()
                    _audit('accommodation_quote', quote_id, 'hms_booking_uncertain',
                           new_value=(f'GroupID={group_id}' if group_id else 'GroupID=unknown'), reason=message)
                    flash('HMS booking має невизначений результат після запуску ReserveGroup. Автоматичний повтор заблоковано; потрібна ручна перевірка HMS. ' + message, 'error')
                else:
                    # Failure happened before a confirmed Reserve mutation (or was an explicit
                    # FirstStep rejection). The temporary GroupCard can disappear; next attempt
                    # starts from a fresh live preflight and a fresh temporary GroupID.
                    conn.execute("""
                        UPDATE accommodation_quotes
                        SET hms_booking_status='error', hms_booking_group_id='', hms_booking_created_at=NULL,
                            hms_booking_created_by='', hms_booking_last_error=?, hms_booking_bridge_diagnostic_json=?,
                            updated_at=?, updated_by=?
                        WHERE quote_id=?
                    """, (
                        message, json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')),
                        _now(), _actor(), quote_id,
                    ))
                    conn.commit()
                    _audit('accommodation_quote', quote_id, 'hms_booking_failed_safe', new_value='error', reason=message)
                    flash('HMS бронювання не створено: ' + message, 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

        if detail_action == 'hms_booking_draft_create':
            if str(row['hms_booking_status'] or '') == 'booked':
                flash('Ця пропозиція вже заброньована в HMS. Повторне створення заблоковане.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            existing_diag = {}
            try:
                existing_diag = json.loads(row['hms_booking_bridge_diagnostic_json'] or '{}')
                if not isinstance(existing_diag, dict): existing_diag = {}
            except Exception:
                existing_diag = {}
            existing_proof = existing_diag.get('creation_proof') if isinstance(existing_diag.get('creation_proof'), dict) else {}
            existing_gid = _ival(row['hms_booking_group_id'], 0, minimum=0)
            if existing_gid > 0 and str(existing_proof.get('kind') or '') == 'reservation_post_302' and _ival(existing_proof.get('group_id'), 0, minimum=0) == existing_gid:
                flash(f'HMS група G{existing_gid:010d} уже створена цією версією пропозиції. Повторне створення заблоковане.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                result = _hms_booking_preflight(row)
                if str(result.get('status') or '') != 'ready':
                    raise ValueError(f"Live preflight більше не READY: конфліктів {result.get('conflict_count') or 0}.")
                payload = result.get('payload') or {}
                draft = _request_hms_booking_draft(payload)
                group_id = str(_ival(draft.get('group_id'), 0, minimum=0))
                if group_id == '0':
                    raise ValueError('HMS sidecar не повернув кандидат GroupID.')
                # v5.319 accepts the candidate only after the sidecar proves it came from the direct Reservation POST -> HTTP 302 Location.
                # GroupCard/Ping are secondary current-state checks, never creation evidence on their own.
                verify = _request_hms_booking_verify(payload, group_id)
                diagnostic = verify.get('diagnostic') if isinstance(verify.get('diagnostic'), dict) else {}
                diagnostic['verification_reason'] = str(verify.get('reason') or '')
                creation_proof = verify.get('creation_proof') if isinstance(verify.get('creation_proof'), dict) else (draft.get('creation_proof') if isinstance(draft.get('creation_proof'), dict) else {})
                diagnostic['creation_proof'] = creation_proof
                diagnostic['group_account'] = str(draft.get('group_account') or creation_proof.get('group_account') or (f'G{int(group_id):010d}' if group_id.isdigit() else ''))
                now = _now()
                verified = bool(verify.get('verified'))
                status = 'draft_verified' if verified else 'draft_unverified'
                err = '' if verified else ('HMS не підтвердив GroupID подвійною перевіркою: ' + str(verify.get('reason') or 'unknown'))
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status=?, hms_booking_preflight_json=?, hms_booking_preflight_at=?,
                        hms_booking_preflight_by=?, hms_booking_quote_revision=?, hms_booking_idempotency_key=?,
                        hms_booking_payload_json=?, hms_booking_group_id=?, hms_booking_last_error=?,
                        hms_booking_bridge_diagnostic_json=?, hms_booking_bridge_state='', hms_booking_bridge_error='', updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (
                    status, json.dumps({k:v for k,v in result.items() if k != 'payload'}, ensure_ascii=False, separators=(',', ':')),
                    result.get('checked_at') or now, _actor(), _ival(row['revision_no'], 1, minimum=1),
                    result.get('idempotency_key') or '', json.dumps(payload, ensure_ascii=False, separators=(',', ':')),
                    group_id, err, json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')),
                    now, _actor(), quote_id,
                ))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_draft_verified' if verified else 'hms_booking_draft_unverified', new_value=f'GroupID={group_id}',
                       reason=f"idempotency={result.get('idempotency_key') or ''}; creation=reservation_post_302; verify={verify.get('reason') or ''}; ping={bool(verify.get('ping_ok'))}")
                account = str(draft.get('group_account') or creation_proof.get('group_account') or (f'G{int(group_id):010d}' if group_id.isdigit() else group_id))
                if verified:
                    flash(f'HMS штатно створив нову групу {account} (GroupID {group_id}) через Reservation POST → HTTP 302 → GroupCard. ReserveGroup не виконувався.', 'success')
                else:
                    flash(f'HMS створив кандидата {account}, але поточна GroupCard/Ping перевірка не завершилась. ReserveGroup не виконувався; повторно нову групу не створюйте.', 'error')
            except Exception as exc:
                message = _pricing_error_for_manager(exc)
                conn.execute("UPDATE accommodation_quotes SET hms_booking_status='draft_error', hms_booking_last_error=?, updated_at=?, updated_by=? WHERE quote_id=?",
                             (message, _now(), _actor(), quote_id))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_draft_failed', new_value='error', reason=message)
                flash('Не вдалося створити/перевірити HMS-чернетку через sidecar: ' + message, 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

        if detail_action == 'hms_booking_draft_verify':
            group_id = _ival(row['hms_booking_group_id'], 0, minimum=0)
            if group_id <= 0:
                flash('Немає GroupID для перевірки.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                payload = json.loads(row['hms_booking_payload_json'] or '{}')
                if not isinstance(payload, dict) or not payload:
                    payload = _hms_booking_payload(row)
                verify = _request_hms_booking_verify(payload, group_id)
                diagnostic = verify.get('diagnostic') if isinstance(verify.get('diagnostic'), dict) else {}
                diagnostic['verification_reason'] = str(verify.get('reason') or '')
                verified = bool(verify.get('verified'))
                status = 'draft_verified' if verified else 'draft_unverified'
                err = '' if verified else ('HMS не підтвердив GroupID: ' + str(verify.get('reason') or 'unknown'))
                conn.execute("UPDATE accommodation_quotes SET hms_booking_status=?, hms_booking_last_error=?, hms_booking_bridge_diagnostic_json=?, updated_at=?, updated_by=? WHERE quote_id=?",
                             (status, err, json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')), _now(), _actor(), quote_id))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_group_verify', new_value=status, reason=f'GroupID={group_id}; {verify.get("reason") or ""}')
                flash((f'HMS GroupID {group_id} підтверджено. ReserveGroup не виконувався.' if verified else f'HMS GroupID {group_id} НЕ підтверджено. Жоден ReserveGroup не виконувався.'), 'success' if verified else 'error')
            except Exception as exc:
                message = _pricing_error_for_manager(exc)
                conn.execute("UPDATE accommodation_quotes SET hms_booking_status='draft_unverified', hms_booking_last_error=?, updated_at=?, updated_by=? WHERE quote_id=?", (message, _now(), _actor(), quote_id))
                conn.commit()
                flash('Перевірка HMS GroupID не виконана: ' + message, 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

        if detail_action == 'hms_booking_prepare_snapshot':
            group_id = _ival(row['hms_booking_group_id'], 0, minimum=0)
            if group_id <= 0 or str(row['hms_booking_status'] or '') != 'draft_verified':
                flash('Спочатку HMS-група має бути створена через штатний Reservation POST → HTTP 302 і підтверджена sidecar.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                payload = json.loads(row['hms_booking_payload_json'] or '{}')
                if not isinstance(payload, dict) or not payload:
                    raise ValueError('Збережений booking snapshot відсутній.')
                prepared = _request_hms_booking_prepare(payload, group_id)
                diagnostic = {}
                try:
                    diagnostic = json.loads(row['hms_booking_bridge_diagnostic_json'] or '{}')
                    if not isinstance(diagnostic, dict): diagnostic = {}
                except Exception:
                    diagnostic = {}
                diagnostic['snapshot_prepare'] = {
                    'prepared_at': prepared.get('prepared_at') or _now(),
                    'room_stays_count': _ival(prepared.get('room_stays_count'), 0, minimum=0),
                    'rooms_unique': _ival(prepared.get('rooms_unique'), 0, minimum=0),
                    'guest_slots_seen_in_group_card': _ival(prepared.get('guest_slots_seen_in_group_card'), 0, minimum=0),
                    'missing_guest_slots': _ival(prepared.get('missing_guest_slots'), 0, minimum=0),
                    'hms_write_ready': bool(prepared.get('hms_write_ready')),
                    'hms_write_executed': False,
                }
                conn.execute("UPDATE accommodation_quotes SET hms_booking_status='snapshot_prepared', hms_booking_last_error='', hms_booking_bridge_diagnostic_json=?, updated_at=?, updated_by=? WHERE quote_id=?",
                             (json.dumps(diagnostic, ensure_ascii=False, separators=(',', ':')), _now(), _actor(), quote_id))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_snapshot_prepared', new_value='snapshot_prepared', reason=f"GroupID={group_id}; stays={prepared.get('room_stays_count')}; guest_slots={prepared.get('guest_slots_seen_in_group_card')}")
                flash(f"Booking snapshot підготовлено: {prepared.get('room_stays_count') or 0} room-stay сегментів. HMS ще не змінювався; ReserveGroup не виконувався.", 'success')
            except Exception as exc:
                message = _pricing_error_for_manager(exc)
                conn.execute("UPDATE accommodation_quotes SET hms_booking_last_error=?, updated_at=?, updated_by=? WHERE quote_id=?", (message, _now(), _actor(), quote_id))
                conn.commit()
                flash('Не вдалося підготувати booking snapshot: ' + message, 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

        if detail_action == 'hms_booking_bridge_cancel':
            if str(row['hms_booking_bridge_group_id'] or '').strip():
                flash('Скасування заблоковано: HMS вже видав чернетковий GroupID. Не можна втратити зв’язок із цією HMS-операцією.', 'error')
            elif str(row['hms_booking_status'] or '') in ('bridge_waiting', 'bridge_error'):
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status='ready', hms_booking_bridge_job_id='', hms_booking_bridge_state='',
                        hms_booking_bridge_started_at=NULL, hms_booking_bridge_started_by='', hms_booking_bridge_seen_at=NULL,
                        hms_booking_bridge_error='', hms_booking_last_error='', updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (_now(), _actor(), quote_id))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_bridge_cancelled', new_value='ready')
                flash('Очікування HMS browser bridge скасовано. Live preflight збережено.', 'success')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
        if detail_action == 'hms_booking_bridge_start':
            if str(row['hms_booking_group_id'] or '').strip() or str(row['hms_booking_status'] or '') == 'booked':
                flash('Ця пропозиція вже прив’язана до бронювання HMS.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            if str(row['hms_booking_bridge_group_id'] or '').strip():
                flash('HMS GroupID для цієї пропозиції вже отримано. Повторний запуск заблокований.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                # Re-run the destructive-free preflight immediately before browser handoff.
                result = _hms_booking_preflight(row)
                if str(result.get('status') or '') != 'ready':
                    raise ValueError(f"Live preflight більше не READY: конфліктів {result.get('conflict_count') or 0}.")
                job_id = uuid.uuid4().hex
                now = _now()
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status='bridge_waiting', hms_booking_preflight_json=?, hms_booking_preflight_at=?,
                        hms_booking_preflight_by=?, hms_booking_quote_revision=?, hms_booking_idempotency_key=?,
                        hms_booking_payload_json=?, hms_booking_last_error='',
                        hms_booking_bridge_job_id=?, hms_booking_bridge_state='waiting', hms_booking_bridge_started_at=?,
                        hms_booking_bridge_started_by=?, hms_booking_bridge_seen_at=NULL, hms_booking_bridge_group_id='',
                        hms_booking_bridge_login_id='', hms_booking_bridge_diagnostic_json='{}', hms_booking_bridge_error='', updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (
                    json.dumps({k:v for k,v in result.items() if k != 'payload'}, ensure_ascii=False, separators=(',', ':')),
                    result.get('checked_at') or now, _actor(), _ival(row['revision_no'], 1, minimum=1),
                    result.get('idempotency_key') or '', json.dumps(result.get('payload') or {}, ensure_ascii=False, separators=(',', ':')),
                    job_id, now, _actor(), now, _actor(), quote_id,
                ))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_bridge_started', new_value=f'job={job_id}', reason=str(result.get('snapshot_sha256') or ''))
                flash('HMS browser bridge запущено. Авторизований браузер має відкрити нове бронювання та повернути GroupID.', 'success')
            except Exception as exc:
                message = _pricing_error_for_manager(exc)
                conn.execute("UPDATE accommodation_quotes SET hms_booking_status='bridge_error', hms_booking_bridge_state='error', hms_booking_bridge_error=?, hms_booking_last_error=?, updated_at=?, updated_by=? WHERE quote_id=?",
                             (message, message, _now(), _actor(), quote_id))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_bridge_start_failed', new_value='error', reason=message)
                flash('Не вдалося передати snapshot у HMS browser bridge: ' + message, 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
        if detail_action == 'hms_booking_preflight':
            if str(row['hms_booking_status'] or '') in ('booked', 'booking_uncertain'):
                flash('Для цієї пропозиції повторний HMS preflight/запис заблокований: бронювання вже завершене або має невизначений post-Reserve статус.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                result = _hms_booking_preflight(row)
                status = str(result.get('status') or 'blocked')
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status=?, hms_booking_preflight_json=?, hms_booking_preflight_at=?,
                        hms_booking_preflight_by=?, hms_booking_quote_revision=?, hms_booking_idempotency_key=?,
                        hms_booking_payload_json=?, hms_booking_last_error='', updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (
                    status, json.dumps({k:v for k,v in result.items() if k != 'payload'}, ensure_ascii=False, separators=(',', ':')),
                    result.get('checked_at') or _now(), _actor(), _ival(row['revision_no'], 1, minimum=1),
                    result.get('idempotency_key') or '', json.dumps(result.get('payload') or {}, ensure_ascii=False, separators=(',', ':')),
                    _now(), _actor(), quote_id,
                ))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_preflight',
                       new_value=f"{status}; conflicts={result.get('conflict_count') or 0}; revision={row['revision_no']}",
                       reason=str(result.get('snapshot_sha256') or ''))
                if status == 'ready':
                    flash('HMS preflight пройдено: усі зафіксовані фізичні номери вільні. Snapshot готовий до booking writer.', 'success')
                else:
                    flash(f"HMS preflight заблокував передачу: конфліктів {result.get('conflict_count') or 0}.", 'error')
            except Exception as exc:
                message = _pricing_error_for_manager(exc)
                conn.execute("""
                    UPDATE accommodation_quotes
                    SET hms_booking_status='error', hms_booking_preflight_json='{}', hms_booking_preflight_at=?,
                        hms_booking_preflight_by=?, hms_booking_quote_revision=?, hms_booking_last_error=?, updated_at=?, updated_by=?
                    WHERE quote_id=?
                """, (_now(), _actor(), _ival(row['revision_no'], 1, minimum=1), message, _now(), _actor(), quote_id))
                conn.commit()
                _audit('accommodation_quote', quote_id, 'hms_booking_preflight_failed', new_value='error', reason=message)
                flash('HMS preflight не виконано: ' + message, 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
        if detail_action == 'hms_compatibility_autofix':
            current_status = str(row['hms_booking_status'] or '').strip()
            if current_status == 'booked':
                flash('Ця пропозиція вже заброньована в HMS. Розміщення після бронювання автоматично не змінюється.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            if current_status == 'booking_uncertain':
                flash('Автовиправлення заблоковано: попередня HMS-транзакція має невизначений результат. Спочатку перевірте HMS вручну.', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            try:
                before = _hms_compatibility_report(row, include_proposal=False)
                if before.get('compatible'):
                    flash('Розміщення вже сумісне з HMS: повторних stay-карток одного RoomID немає.', 'success')
                    return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
                proposal = _hms_autofix_plan(row, force_live=True)
                if not proposal.get('ok') or not proposal.get('plans_by_day'):
                    raise ValueError('Не вдалося побудувати HMS-сумісний live-план.')
                calc = _hms_recalculate_exact_daily_plans(row, proposal['plans_by_day'])
                if not bool((calc.get('pricing') or {}).get('booking_allowed', True)):
                    reason = _booking_restriction_failure_message(
                        list((calc.get('pricing') or {}).get('booking_restrictions') or []),
                        arrival=str(row['arrival']), departure=str(row['departure']),
                        selected_nights=_ival(row['nights'], 1, minimum=1),
                    ) or 'вибрані умови бронювання не виконані.'
                    raise ValueError('HMS-сумісний варіант знайдено, але нову версію не збережено: ' + reason)
                quote_data = _quote_data_from_manual_recalculation(row, calc)
                quote_id2, quote_number, revision_no = _persist_quote_version(
                    conn, quote_data, edit_quote_id=quote_id, revision_kind='hms_compatibility_fix'
                )
                conn.commit()
                change_summary = _hms_autofix_change_summary(proposal.get('changes') or [])
                _audit(
                    'accommodation_quote', quote_id2, 'hms_compatibility_autofixed',
                    new_value=f'{quote_number}; v{revision_no}',
                    reason=(
                        f"issues_before={before.get('issue_count') or 0}; changes={change_summary}; "
                        f"stays_after={(calc.get('hms_compatibility') or {}).get('room_stays_count') or 0}"
                    ),
                )
                flash(
                    f'HMS-сумісне розміщення збережено як версію {revision_no}. '
                    f'Заміни: {change_summary}. Перед бронюванням ще раз виконайте live preflight.',
                    'success',
                )
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id2))
            except Exception as exc:
                flash('Автовиправлення HMS-розміщення не виконано: ' + _pricing_error_for_manager(exc), 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

        if detail_action in ('manual_day_preview', 'manual_day_refill', 'manual_day_save'):
            day_date = (request.form.get('manual_day') or '').strip()
            refill = detail_action == 'manual_day_refill'
            try:
                calc = _recalculate_quote_with_manual_day(row, day_date, request.form, refill=refill)
                selected_plan = list((calc.get('selected_day_result') or {}).get('room_plan') or [])
                editor = _manual_editor_context_for_quote(row, day_date, room_plan=selected_plan)
                old_total = _money_decimal(row['commercial_total'])
                new_total = _money_decimal((calc.get('commercial') or {}).get('commercial_total'))
                selected_day_result = calc.get('selected_day_result') or {}
                original_day = next((x for x in _quote_daily_schedule_from_row(row) if str(x.get('date') or '') == day_date), {})
                preview_plans = {
                    str(x.get('date') or ''): list(x.get('room_plan') or [])
                    for x in (calc.get('occupancy_daily') or []) if isinstance(x, dict)
                }
                preview_hms_compat = _hms_compatibility_from_plans(calc.get('daily_schedule') or [], preview_plans)
                preview = {
                    'base_total': (calc.get('pricing') or {}).get('stay_total_before_tourist_tax'),
                    'commercial_total': (calc.get('commercial') or {}).get('commercial_total'),
                    'discount_percent': (calc.get('commercial') or {}).get('discount_percent'),
                    'delta': _money_float(new_total - old_total),
                    'currency': str((calc.get('pricing') or {}).get('currency') or row['currency'] or 'UAH'),
                    'booking_allowed': bool((calc.get('pricing') or {}).get('booking_allowed', True)),
                    'booking_restrictions': list((calc.get('pricing') or {}).get('booking_restrictions') or []),
                    'warnings': list(calc.get('warnings') or []),
                    'refilled': refill,
                    'old_adults': _ival(original_day.get('adults'), 0, minimum=0),
                    'old_children': _ival(original_day.get('children'), 0, minimum=0),
                    'old_paid_children': _ival(original_day.get('paid_children'), 0, minimum=0),
                    'new_adults': _ival(selected_day_result.get('adults'), 0, minimum=0),
                    'new_children': _ival(selected_day_result.get('children'), 0, minimum=0),
                    'new_paid_children': _ival(selected_day_result.get('paid_children'), 0, minimum=0),
                    'early_checkin_total': (calc.get('pricing') or {}).get('early_checkin_total'),
                    'late_checkout_total': (calc.get('pricing') or {}).get('late_checkout_total'),
                    'early_checkin_availability': dict((calc.get('pricing') or {}).get('early_checkin_availability') or {}),
                    'late_checkout_availability': dict((calc.get('pricing') or {}).get('late_checkout_availability') or {}),
                    'hms_compatibility': preview_hms_compat,
                }
                preview['composition_changed'] = (
                    preview['old_adults'] != preview['new_adults'] or
                    preview['old_children'] != preview['new_children'] or
                    preview['old_paid_children'] != preview['new_paid_children']
                )
                if detail_action == 'manual_day_save':
                    if not preview['booking_allowed']:
                        reason = _booking_restriction_failure_message(
                            preview['booking_restrictions'], arrival=str(row['arrival']), departure=str(row['departure']),
                            selected_nights=_ival(row['nights'], 1, minimum=1),
                        ) or 'вибрані умови бронювання не виконані.'
                        raise ValueError('Нову версію не збережено: ' + reason)
                    quote_data = _quote_data_from_manual_recalculation(row, calc)
                    change_summary = _manual_room_change_summary(row['occupancy_json'], calc['occupancy_daily'])
                    quote_id2, quote_number, revision_no = _persist_quote_version(
                        conn, quote_data, edit_quote_id=quote_id, revision_kind='manual_recalculation'
                    )
                    conn.commit()
                    composition_note = (
                        f"{preview['old_adults']}+{preview['old_children']} -> {preview['new_adults']}+{preview['new_children']}"
                        if preview.get('composition_changed') else 'без зміни складу'
                    )
                    _audit(
                        'accommodation_quote', quote_id2, 'manual_rooms_recalculated',
                        new_value=f'{quote_number}; v{revision_no}',
                        reason=f'day={day_date}; room_changes={change_summary}; composition={composition_note}',
                    )
                    flash(f'Ручні зміни номерів збережено як версію {revision_no}. Попередня версія залишилась в історії.', 'success')
                    return redirect(url_for('accommodation.quote_detail', quote_id=quote_id2))
                return _render_quote_detail_response(
                    conn=conn, current_row=row, row=row, is_current_revision=True, selected_revision=None,
                    manual_editor=editor, manual_preview=preview, manual_editor_error='',
                )
            except Exception as exc:
                try:
                    editor = _manual_editor_context_for_quote(row, day_date)
                    posted = _posted_manual_plan_for_editor(day_date, request.form, editor.get('room_options') or [])
                    if posted:
                        editor['current_plan'] = posted
                except Exception:
                    editor = None
                return _render_quote_detail_response(
                    conn=conn, current_row=row, row=row, is_current_revision=True, selected_revision=None,
                    manual_editor=editor, manual_preview=None, manual_editor_error=_pricing_error_for_manager(exc),
                )
        if detail_action == 'guest_list':
            guest_list = _guest_list_from_form()
            source = (request.form.get('guest_list_source') or row['guest_list_source'] or '').strip()
            uploaded = request.files.get('guest_file')
            if uploaded and getattr(uploaded, 'filename', ''):
                try:
                    guest_list = _merge_guest_lists(guest_list, _guest_list_from_upload(uploaded))
                    source = f'file:{Path(str(uploaded.filename)).name}'
                except Exception as exc:
                    flash(str(exc), 'error')
                    return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            ga, gc, _ = _guest_counts(guest_list)
            if ga > _ival(row['adults'], 0) or gc > _ival(row['children'], 0):
                flash('У списку більше дорослих або дітей, ніж у зафіксованому розрахунку. Для зміни кількості гостей використайте «Редагувати та перерахувати».', 'error')
                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
            conn.execute('UPDATE accommodation_quotes SET guest_list_json=?, guest_list_source=?, updated_at=?, updated_by=? WHERE quote_id=?', (
                json.dumps(guest_list, ensure_ascii=False, separators=(',', ':')), source, _now(), _actor(), quote_id,
            ))
            _clear_hms_booking_preflight(conn, quote_id)
            conn.commit()
            _audit('accommodation_quote', quote_id, 'guest_list_updated', new_value=f'{len(guest_list)} guests', reason=source)
            flash(f'Список гостей збережено: {len(guest_list)} ос.', 'success')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
        if row['tariff_status'] != 'live_hms' or row['stay_total_before_tourist_tax'] is None:
            flash('Не можна змінити комерційні умови без зафіксованої актуальної базової ціни.', 'error')
            return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))
        pct = _percent_decimal(request.form.get('discount_percent'))
        note = (request.form.get('commercial_note') or '').strip()
        terms = _commercial_terms(row['stay_total_before_tourist_tax'], pct)
        revision_no = _ival(row['revision_no'], 1, minimum=1) + 1
        now = _now()
        actor = _actor()
        _ensure_revision_snapshots(conn, row)
        conn.execute('''
            UPDATE accommodation_quotes
            SET commercial_discount_percent=?, commercial_discount_amount=?, commercial_total=?,
                commercial_note=?, revision_no=?, updated_at=?, updated_by=?
            WHERE quote_id=?
        ''', (
            terms['discount_percent'], terms['discount_amount'], terms['commercial_total'],
            note, revision_no, now, actor, quote_id,
        ))
        _clear_hms_booking_preflight(conn, quote_id)
        updated_row = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
        conn.execute('''
            INSERT INTO accommodation_quote_revisions(
                revision_id, quote_id, revision_no, created_at, created_by,
                hms_base_total, discount_percent, discount_amount, commercial_total, commercial_note,
                snapshot_json, revision_kind
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            str(uuid.uuid4()), quote_id, revision_no, now, actor,
            terms['base_total'], terms['discount_percent'], terms['discount_amount'], terms['commercial_total'], note,
            _quote_snapshot_json(updated_row, version_created_at=now, version_created_by=actor, revision_kind='commercial'),
            'commercial',
        ))
        conn.commit()
        check = conn.execute('SELECT revision_no, commercial_total FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
        if not check or _ival(check['revision_no'], 0) != revision_no:
            raise RuntimeError('Оновлення комерційних умов не підтверджено після запису в БД.')
        _audit('accommodation_quote', quote_id, 'commercial_terms_updated', new_value=f'rev {revision_no}; {terms["discount_percent"]}%; {terms["commercial_total"]}', reason=note)
        flash(f'Комерційні умови оновлено. Версія {revision_no}: знижка {terms["discount_percent"]:.2f}%.', 'success')
        return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))

    current_row = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
    try:
        row, is_current_revision, selected_revision = _revision_quote_view(
            conn, current_row, request.args.get('revision')
        )
    except KeyError:
        abort(404)
    manual_editor = None
    manual_editor_error = ''
    edit_day = (request.args.get('edit_day') or '').strip()
    if edit_day and is_current_revision:
        try:
            manual_editor = _manual_editor_context_for_quote(row, edit_day)
        except Exception as exc:
            manual_editor_error = _pricing_error_for_manager(exc)
    return _render_quote_detail_response(
        conn=conn, current_row=current_row, row=row, is_current_revision=is_current_revision,
        selected_revision=selected_revision, manual_editor=manual_editor, manual_preview=None,
        manual_editor_error=manual_editor_error,
    )


@bp.route('/accommodation-calculator/quotes/<quote_id>/print')
def quote_print(quote_id: str):
    ensure_accommodation_schema()
    conn = _db()
    current_row = conn.execute('SELECT * FROM accommodation_quotes WHERE quote_id=?', (quote_id,)).fetchone()
    if not current_row:
        abort(404)
    try:
        row, _is_current_revision, selected_revision = _revision_quote_view(
            conn, current_row, request.args.get('revision')
        )
    except KeyError:
        abort(404)
    try:
        allocation = json.loads(row['allocation_json'])
    except Exception:
        allocation = {'rows': []}
    try:
        pricing = json.loads(row['pricing_json'] or '{}')
    except Exception:
        pricing = {}
    try:
        daily_plan = json.loads(row['daily_plan_json'] or '[]')
        if not isinstance(daily_plan, list):
            daily_plan = []
    except Exception:
        daily_plan = []
    pricing_breakdown = _pricing_category_breakdown(pricing, _ival(row['nights'], 0, minimum=0))
    pricing_daily = _pricing_daily_statement(pricing, row['arrival'], row['departure'])
    manager_name = _employee_full_name(row['created_by'])
    version_created_at = row.get('_version_created_at') if isinstance(row, dict) else None
    # v5.303: canonical print route. Build the guest-facing payload here so the
    # print form cannot silently fall back to an obsolete adapter view.
    from proposal_print_adapter import build_accommodation_print_payload
    proposal = build_accommodation_print_payload(conn, row, allocation)
    return render_template(
        'accommodation_quote_print.html', title=row['quote_number'], quote=row,
        allocation=allocation, proposal=proposal, pricing=pricing, pricing_breakdown=pricing_breakdown,
        pricing_daily=pricing_daily, daily_plan=daily_plan, manager_name=manager_name,
        selected_revision=selected_revision,
        version_created_at=version_created_at or row.get('updated_at') or row.get('created_at'),
        placement_labels=PLACEMENT_LABELS, money_fmt=_money_text,
    )


def register_accommodation_module(app, *, db, audit, now_iso, current_employee_id, project_dir: Path) -> None:
    global _DEPS
    _DEPS = {
        'db': db,
        'audit': audit,
        'now_iso': now_iso,
        'current_employee_id': current_employee_id,
        'project_dir': Path(project_dir),
    }
    app.register_blueprint(bp)
