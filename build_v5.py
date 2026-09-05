from __future__ import annotations

import ast
import hashlib
import json
import zipfile
from pathlib import Path

import build_v4 as v4
import build_v2 as base

ROOT = Path(__file__).resolve().parent
V5_NAME = 'Riverwood_5SEP_HMS_COMPATIBILITY_MINIMAL_REPAIR_v5'
V5_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V5'
V5_UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V5_UI'

base.BUILD_NAME = V5_NAME
base.README = '''Riverwood HMS Compatibility Minimal Repair v5
=============================================

WHY v5
- v4 could detect 11 writer stays / 9 room slots but its autofix demanded a FULL live-options perfect matching for every row of a night.
- Production showed this was too strict: a 7-row RoomType/signature group failed even though only the conflicting subset needs to move.
- v5 treats the room IDs already selected on the night as valid permutation candidates and searches for the assignment that MINIMIZES writer stay starts across the previous/current/next night boundary.

WHAT CHANGES
- Detection and hard block remain exactly as in v3/v4.
- Autofix no longer requires every currently selected RoomID to be present in the live "free options" list just to keep/swap it inside the same already-selected night set.
- Existing selected RoomIDs are always available as permutation candidates; genuinely new RoomIDs are allowed only when returned by live availability.
- Assignment is optimized per RoomType against BOTH previous-night and next-night continuity.
- Already-continuous rows are preserved unless moving them produces a strictly better total writer-stay result.
- Final candidate day still goes through the existing live manual recalculation path, which revalidates the exact final room set, capacity, rates, restrictions and early/late rules.
- If no RoomID-only repair can reduce the writer-stay count enough, nothing is saved and the error explains that the nightly signature multiset itself requires a deeper reallocation rather than pretending another free room will solve it.

INSTALL
1. Extract ZIP on Operations host.
2. Run APPLY_V5.bat.
3. Restart ONLY Operations :8080.
4. Open ACC-20260829-002 and press “Виправити автоматично · minimal repair”.

NOT TOUCHED
- :8082
- :8085
- pms_booking_adapter_v1.py
- GroupCard
- ReserveGroup 1/2/3
'''
base.INSTALLER = base.INSTALLER.replace('v4', 'v5')

_orig_patch_module = base.patch_module
_orig_patch_template = base.patch_template
_orig_test = base.test_compat_logic

V5_REPAIR_BLOCK = r'''
# RIVERWOOD_HMS_COMPAT_AUTOFIX_V5
HMS_COMPATIBILITY_AUTOFIX_V5_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V5'


def _hms_compat_meta_for_selected_room(room: Dict[str, Any], live_opt: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Physical-room metadata for a room already selected on this exact night.

    A currently selected RoomID must not disappear from a swap just because the PMS
    free-options list omits it while it is already part of the quote plan.  Live option
    fields, when present, override the saved metadata.  The final manual recalculation
    still performs authoritative live validation of the whole resulting plan.
    """
    out = dict(room or {})
    if isinstance(live_opt, dict):
        keep = {
            'adults': out.get('adults'), 'children': out.get('children'),
            'paid_children': out.get('paid_children'), 'extra_beds': out.get('extra_beds'),
            'early_checkin': out.get('early_checkin'), 'late_checkout': out.get('late_checkout'),
            'manual_locked': out.get('manual_locked'), 'manual_source': out.get('manual_source'),
            'key': out.get('key'), 'position': out.get('position'),
        }
        out.update(dict(live_opt))
        for k, v in keep.items():
            if v is not None:
                out[k] = v
    token = str(out.get('room_id') or out.get('room_id_token') or '').strip()
    out['room_id'] = token
    out['room_id_token'] = token
    return out


def _hms_compat_candidate_fits(meta: Dict[str, Any], row_now: Dict[str, Any], *, is_selected_this_night: bool) -> bool:
    if _ival(meta.get('room_type_id'), 0, minimum=0) != _ival(row_now.get('room_type_id'), 0, minimum=0):
        return False
    occupants = _ival(row_now.get('adults'), 0, minimum=0) + _ival(row_now.get('children'), 0, minimum=0)
    cap = _ival(meta.get('capacity_per_room'), _ival(meta.get('base_capacity'), 0), minimum=0)
    if cap and cap < occupants:
        return False
    extra_need = _ival(row_now.get('extra_beds'), 0, minimum=0)
    extra_cap = _ival(meta.get('extra_capacity'), 0, minimum=0)
    if extra_need > 0 and extra_cap < extra_need:
        return False
    # For boundary-day early/late moves, require explicit live proof unless the exact
    # same row keeps the same selected room.  Middle-night swaps are unaffected.
    if bool(row_now.get('early_checkin')) and not bool(meta.get('early_checkin_available')):
        if not is_selected_this_night:
            return False
    if bool(row_now.get('late_checkout')) and not bool(meta.get('late_checkout_available')):
        if not is_selected_this_night:
            return False
    return True


def _hms_compat_best_assignment_for_type(
    row_indexes: List[int], current: List[Dict[str, Any]], previous: List[Dict[str, Any]],
    next_plan: List[Dict[str, Any]], options: List[Dict[str, Any]]
) -> Dict[int, str]:
    """Maximum-continuity unique assignment for one RoomType.

    The selected room set is always a candidate set.  Additional candidates are accepted
    only from the live options list.  Dynamic programming minimizes writer-start
    boundaries first and physical changes second.
    """
    if not row_indexes:
        return {}
    live_by_id: Dict[str, Dict[str, Any]] = {}
    for opt in options or []:
        if not isinstance(opt, dict):
            continue
        token = str(opt.get('room_id') or opt.get('room_id_token') or '').strip()
        if token:
            live_by_id[token] = dict(opt)

    current_by_id = {
        str(x.get('room_id') or '').strip(): dict(x)
        for x in current if isinstance(x, dict) and str(x.get('room_id') or '').strip()
    }
    previous_by_id = {
        str(x.get('room_id') or '').strip(): dict(x)
        for x in previous if isinstance(x, dict) and str(x.get('room_id') or '').strip()
    }
    next_by_id = {
        str(x.get('room_id') or '').strip(): dict(x)
        for x in next_plan if isinstance(x, dict) and str(x.get('room_id') or '').strip()
    }
    room_type_id = _ival(current[row_indexes[0]].get('room_type_id'), 0, minimum=0)

    selected_ids = [
        str(current[i].get('room_id') or '').strip()
        for i in row_indexes if str(current[i].get('room_id') or '').strip()
    ]
    candidate_ids: List[str] = []
    for token in selected_ids:
        if token and token not in candidate_ids:
            candidate_ids.append(token)
    # Previous/next rooms are the most valuable external continuity targets, but only
    # when they are confirmed live on the current night.
    for source in (previous_by_id, next_by_id):
        for token, meta in source.items():
            if token in candidate_ids or token not in live_by_id:
                continue
            if _ival(meta.get('room_type_id'), 0, minimum=0) == room_type_id:
                candidate_ids.append(token)
    # A small live spare pool is enough for displacement chains without exploding DP.
    for token, opt in sorted(live_by_id.items(), key=lambda kv: str(kv[1].get('room_label') or kv[0])):
        if token in candidate_ids:
            continue
        if _ival(opt.get('room_type_id'), 0, minimum=0) != room_type_id:
            continue
        candidate_ids.append(token)
        if len(candidate_ids) >= len(row_indexes) + 6:
            break

    metas: Dict[str, Dict[str, Any]] = {}
    for token in candidate_ids:
        if token in current_by_id:
            metas[token] = _hms_compat_meta_for_selected_room(current_by_id[token], live_by_id.get(token))
        else:
            metas[token] = dict(live_by_id.get(token) or {})
            metas[token]['room_id'] = token
            metas[token]['room_id_token'] = token

    # dp[mask] = (score, same_count, assignment_tokens)
    # score counts continuity to prev+next; each edge avoided removes one writer-start.
    dp: Dict[int, Tuple[int, int, List[str]]] = {0: (0, 0, [])}
    for row_idx in row_indexes:
        row_now = current[row_idx]
        sig = _hms_compat_signature(row_now)
        old_id = str(row_now.get('room_id') or '').strip()
        new_dp: Dict[int, Tuple[int, int, List[str]]] = {}
        for mask, state in dp.items():
            base_score, base_same, assigned = state
            for ci, token in enumerate(candidate_ids):
                bit = 1 << ci
                if mask & bit:
                    continue
                meta = metas.get(token) or {}
                if not _hms_compat_candidate_fits(meta, row_now, is_selected_this_night=(token in current_by_id)):
                    continue
                cont = 0
                prev = previous_by_id.get(token)
                nxt = next_by_id.get(token)
                if prev is not None and _hms_compat_signature(prev) == sig:
                    cont += 1
                if nxt is not None and _hms_compat_signature(nxt) == sig:
                    cont += 1
                same = 1 if token == old_id else 0
                cand = (base_score + cont, base_same + same, assigned + [token])
                nmask = mask | bit
                old = new_dp.get(nmask)
                if old is None or (cand[0], cand[1]) > (old[0], old[1]):
                    new_dp[nmask] = cand
        dp = new_dp
        if not dp:
            raise ValueError(
                f'Не вдалося знайти жодного допустимого призначення RoomType {room_type_id} '
                f'для {len(row_indexes)} рядків цієї ночі.'
            )

    best = max(dp.values(), key=lambda x: (x[0], x[1]))
    if len(best[2]) != len(row_indexes):
        raise ValueError('Minimal continuity repair повернув неповне призначення.')
    return {row_indexes[pos]: token for pos, token in enumerate(best[2])}


def _hms_compat_remap_day_for_continuity(row: Any, prev_date: str, day_date: str) -> Dict[str, Any]:
    """Minimal writer-stay repair for one night; optimize only what actually helps."""
    occupancy = _quote_occupancy_by_day(row)
    previous = [dict(x) for x in (occupancy.get(prev_date) or []) if isinstance(x, dict)]
    current = [dict(x) for x in (occupancy.get(day_date) or []) if isinstance(x, dict)]
    if not current:
        return {'plan': current, 'changes': []}

    schedule = _quote_daily_schedule_from_row(row)
    pos = next((i for i, x in enumerate(schedule) if str(x.get('date') or '') == day_date), -1)
    next_date = str(schedule[pos + 1].get('date') or '') if 0 <= pos < len(schedule) - 1 else ''
    next_plan = [dict(x) for x in (occupancy.get(next_date) or []) if isinstance(x, dict)] if next_date else []

    editor = _manual_editor_context_for_quote(row, day_date)
    options = [dict(x) for x in (editor.get('room_options') or []) if isinstance(x, dict)]
    live_by_id = {
        str(x.get('room_id') or x.get('room_id_token') or '').strip(): dict(x)
        for x in options if str(x.get('room_id') or x.get('room_id_token') or '').strip()
    }
    current_by_id = {
        str(x.get('room_id') or '').strip(): dict(x)
        for x in current if str(x.get('room_id') or '').strip()
    }

    groups: Dict[int, List[int]] = {}
    for idx, row_now in enumerate(current):
        groups.setdefault(_ival(row_now.get('room_type_id'), 0, minimum=0), []).append(idx)

    assignment: Dict[int, str] = {}
    for _, indexes in sorted(groups.items()):
        assignment.update(_hms_compat_best_assignment_for_type(indexes, current, previous, next_plan, options))

    matched: List[Dict[str, Any]] = []
    for idx, row_now in enumerate(current):
        token = str(assignment.get(idx) or row_now.get('room_id') or '').strip()
        if not token:
            raise ValueError(f'{day_date}: minimal repair отримав порожній RoomID.')
        if token in current_by_id:
            meta = _hms_compat_meta_for_selected_room(current_by_id[token], live_by_id.get(token))
        else:
            meta = dict(live_by_id.get(token) or {})
            meta['room_id'] = token
            meta['room_id_token'] = token
        matched.append(_hms_compat_room_from_option(meta, row_now))

    if len({str(x.get('room_id') or '') for x in matched}) != len(matched):
        raise ValueError(f'{day_date}: minimal repair створив дубль фізичного RoomID.')

    # Prove the proposed day actually improves the exact writer count before doing a
    # live repricing/recalculation.  No cosmetic permutation is applied.
    before = _hms_compatibility_analysis_from_plans(schedule, occupancy)
    virtual = {str(k): [dict(x) for x in (v or [])] for k, v in occupancy.items()}
    virtual[day_date] = [dict(x) for x in matched]
    after = _hms_compatibility_analysis_from_plans(schedule, virtual)
    if _ival(after.get('writer_stays_count'), 0) >= _ival(before.get('writer_stays_count'), 0):
        return {
            'plan': current, 'changes': [],
            'diagnostic': (
                f'{day_date}: RoomID-перестановка не зменшує writer stays '
                f"({before.get('writer_stays_count')} → {after.get('writer_stays_count')})."
            ),
        }

    changes: List[Dict[str, Any]] = []
    for old, new in zip(current, matched):
        old_id = str(old.get('room_id') or '')
        new_id = str(new.get('room_id') or new.get('room_id_token') or '')
        if old_id == new_id:
            continue
        changes.append({
            'date': day_date,
            'from_room_id': old_id,
            'from_room_label': str(old.get('room_label') or old_id),
            'to_room_id': new_id,
            'to_room_label': str(new.get('room_label') or new_id),
            'room_type_id': _ival(old.get('room_type_id'), 0, minimum=0),
            'category': str(old.get('category') or new.get('category') or ''),
            'adults': _ival(old.get('adults'), 0, minimum=0),
            'children': _ival(old.get('children'), 0, minimum=0),
            'paid_children': _ival(old.get('paid_children'), 0, minimum=0),
            'extra_beds': _ival(old.get('extra_beds'), 0, minimum=0),
            'mode': 'minimal_continuity_repair',
        })
    return {'plan': matched, 'changes': changes, 'before': before, 'after': after}
'''


def patch_module_v5(src: str) -> str:
    out = _orig_patch_module(src)
    out = v4._replace_function_with_block(out, '_hms_compat_remap_day_for_continuity', V5_REPAIR_BLOCK)
    # Improve the terminal autofix error so impossible signature-multiset cases are explicit.
    old = (
        "'Автоматична заміна фізичних номерів не може безпечно прибрати цей розрив без зміни '"
        "\n            'покомнатного складу гостей: '"
    )
    new = (
        "'RoomID-only minimal repair перевірив перестановки вибраних і live-доступних номерів, але не може '"
        "\n            'звести writer stays до room-slot бюджету. Це означає, що проблема вже не лише у фізичних RoomID: '"
        "\n            'денний набір HMS-signature (RoomType/adults/children/paid/extra) відрізняється між ночами або потрібна '"
        "\n            'інша покомнатна розкладка складу. Нічого не збережено: '"
    )
    if old in out:
        out = out.replace(old, new, 1)
    if V5_MARKER not in out:
        raise RuntimeError('v5 module marker missing')
    return out


def patch_template_v5(src: str) -> str:
    out = _orig_patch_template(src)
    out = out.replace('Виправити автоматично · swap/permutation</button>', 'Виправити автоматично · minimal repair</button>', 1)
    out += '\n<!-- RIVERWOOD_HMS_COMPAT_AUTOFIX_V5_UI -->\n'
    if V5_UI_MARKER not in out:
        raise RuntimeError('v5 UI marker missing')
    return out


base.patch_module = patch_module_v5
base.patch_template = patch_template_v5


def test_v5_minimal_repair(module_text: str) -> None:
    _orig_test(module_text)
    tree = ast.parse(module_text)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    needed = [
        '_hms_compat_signature', '_hms_compat_room_from_option', '_hms_compat_meta_for_selected_room',
        '_hms_compat_candidate_fits', '_hms_compat_best_assignment_for_type',
    ]
    missing = [x for x in needed if x not in funcs]
    if missing:
        raise RuntimeError(f'missing v5 test functions: {missing}')
    ns = {'Any': object, 'Dict': dict, 'List': list, 'Tuple': tuple, 'Optional': object}
    def _ival(v, default=0, minimum=None, maximum=None):
        try: out = int(str(v).strip())
        except Exception: out = default
        if minimum is not None: out = max(minimum, out)
        if maximum is not None: out = min(maximum, out)
        return out
    ns['_ival'] = _ival
    for name in needed:
        exec(compile(ast.Module(body=[funcs[name]], type_ignores=[]), '<v5-test>', 'exec'), ns)
    assign = ns['_hms_compat_best_assignment_for_type']

    A = (3, 0, 0, 1)
    B = (2, 1, 1, 1)
    C = (2, 0, 0, 0)
    def row(rid, sig, rt=2):
        return {
            'room_id': str(rid), 'room_label': str(rid), 'room_type_id': rt, 'category': 'Standard',
            'adults': sig[0], 'children': sig[1], 'paid_children': sig[2], 'extra_beds': sig[3],
            'capacity_per_room': 4, 'base_capacity': 4, 'extra_capacity': 2,
        }
    def opt(rid, rt=2):
        return {
            'room_id': str(rid), 'room_id_token': str(rid), 'room_label': str(rid), 'room_type_id': rt,
            'category': 'Standard', 'capacity_per_room': 4, 'base_capacity': 4, 'extra_capacity': 2,
            'early_checkin_available': True, 'late_checkout_available': True,
        }

    # Production-shaped regression from the v4 error: seven rows share signature A.
    # Only 111/112 need to swap; the live free-options list intentionally DOES NOT
    # contain all seven currently selected RoomIDs.  v4 failed here by demanding a
    # complete live-options matching.  v5 must use the selected room set itself.
    previous = [row(101, A), row(102, A), row(103, A), row(104, A), row(105, A), row(106, A), row(112, A), row(111, B), row(201, C)]
    current  = [row(101, A), row(102, A), row(103, A), row(104, A), row(105, A), row(106, A), row(111, A), row(112, B), row(201, C)]
    nextplan = [row(101, A), row(102, A), row(103, A), row(104, A), row(105, A), row(106, A), row(112, A), row(111, B), row(201, C)]
    # only two live options present; the other seven selected RoomIDs are absent by design
    options = [opt(111), opt(112)]
    indexes = list(range(9))
    result = assign(indexes, current, previous, nextplan, options)
    assigned_ids = [result[i] for i in indexes]
    assert assigned_ids[:6] == ['101','102','103','104','105','106'], assigned_ids
    assert assigned_ids[6] == '112' and assigned_ids[7] == '111' and assigned_ids[8] == '201', assigned_ids

    # No-conflict rows with zero live options still remain valid candidates because they
    # are already selected on the night.  v4 would reject a full matching.
    stable = [row(301, A), row(302, A), row(303, B)]
    stable_result = assign(list(range(3)), stable, stable, stable, [])
    assert [stable_result[i] for i in range(3)] == ['301','302','303'], stable_result


base.test_compat_logic = test_v5_minimal_repair


def main() -> None:
    v4.v3.verify_writer_reference()
    base.main()
    pkg = base.DIST / V5_NAME
    old = pkg / 'APPLY_V2.bat'
    new = pkg / 'APPLY_V5.bat'
    if old.exists(): old.replace(new)
    report = (pkg / 'TEST_REPORT.txt').read_text(encoding='utf-8') if (pkg / 'TEST_REPORT.txt').exists() else ''
    report += '\nV5 MINIMAL REPAIR REGRESSION\n'
    report += '7-row same-signature group with only 2 live options and 111<->112 swap: PASS\n'
    report += 'Selected RoomIDs remain candidates even when absent from free-options list: PASS\n'
    report += 'Stable selected-room set with zero live options: PASS\n'
    report += 'Optimization uses previous + next continuity: PASS\n'
    report += 'Cosmetic/non-improving remap rejected before recalculation: INCLUDED\n'
    report += 'Writer/sidecar modified: NO\n'
    (pkg / 'TEST_REPORT.txt').write_text(report, encoding='utf-8')
    zip_path = base.DIST / (V5_NAME + '.zip')
    if zip_path.exists(): zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob('*')):
            if p.is_file(): z.write(p, p.relative_to(base.DIST))
    print(json.dumps({'ok': True, 'build': V5_NAME, 'zip_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
