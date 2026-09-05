from __future__ import annotations

import ast
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import build_v3 as v3
import build_v2 as base

ROOT = Path(__file__).resolve().parent
V4_NAME = 'Riverwood_5SEP_HMS_COMPATIBILITY_SWAP_AUTOFIX_v4'
V4_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V4'
V4_UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V4_UI'

base.BUILD_NAME = V4_NAME
base.README = '''Riverwood HMS Compatibility Swap Autofix v4
============================================

WHY v4
- v3 correctly detects the writer-stay overflow before HMS (for ACC-20260829-002: 11 stays / 9 room slots).
- v3 autofix was still too greedy: it preserved already-continuous rooms first and then searched for an additional unused live room.
- On a fully allocated night that can fail even when a safe solution exists by SWAPPING/PERMUTING the physical RoomIDs already selected in the quote.

WHAT v4 CHANGES
- Detection/hard block from v3 remains.
- Autofix now performs a whole-night continuity matching, not row-by-row greedy replacement.
- It can swap/permutate already selected physical rooms among rows of the same RoomType when that preserves writer continuity.
- It does NOT require a 10th spare room for a 9-room night.
- Per-row adults/children/paid_children/extra_beds remain unchanged.
- Candidate RoomID must still be live available and pass RoomType/capacity/extra-bed/early/late checks.
- Every changed night is still run through the existing live manual recalculation path before preview/save.
- If no full safe permutation exists, nothing is saved and preflight/booking remain blocked.

INSTALL
1. Extract ZIP on the Operations host.
2. Run APPLY_V4.bat.
3. Restart ONLY Operations :8080 in the normal way.
4. Open ACC-20260829-002 and press “Виправити автоматично”.
5. Expected: a “було → стане” preview using swaps/permutations of the existing room set where possible.

NOT TOUCHED
- :8082
- :8085
- pms_booking_adapter_v1.py
- GroupCard
- ReserveGroup 1/2/3
'''

# Reuse the exact-root/path-safe installer from v3; only label its backup/version as v4.
base.INSTALLER = base.INSTALLER.replace('v3', 'v4')

_orig_patch_module = base.patch_module
_orig_patch_template = base.patch_template
_orig_test = base.test_compat_logic

NEW_MATCH_AND_REMAP = r'''
# RIVERWOOD_HMS_COMPAT_AUTOFIX_V4
HMS_COMPATIBILITY_AUTOFIX_V4_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V4'


def _hms_compat_match_full_night(
    previous: List[Dict[str, Any]], current: List[Dict[str, Any]], options: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return a complete physical-room assignment that maximizes previous-night continuity.

    Unlike v3, this matcher treats the WHOLE night as one assignment problem.  A room
    already used by another row is not forbidden up-front; two rows may swap their
    physical RoomIDs.  Uniqueness is enforced on the final assignment, so the number of
    room slots never increases.
    """
    option_by_id: Dict[str, Dict[str, Any]] = {}
    for raw in options or []:
        if not isinstance(raw, dict):
            continue
        token = str(raw.get('room_id') or raw.get('room_id_token') or '').strip()
        if token:
            option_by_id[token] = dict(raw)
    if not current:
        return []

    prev_by_sig: Dict[Tuple[int, int, int, int, int], List[str]] = {}
    for prev in previous or []:
        if not isinstance(prev, dict):
            continue
        token = str(prev.get('room_id') or '').strip()
        if token and token in option_by_id:
            prev_by_sig.setdefault(_hms_compat_signature(prev), []).append(token)

    assigned: List[Optional[str]] = [None] * len(current)
    reserved = set()

    # Phase 1: maximum matching to previous-night RoomIDs with the SAME writer signature.
    # This is the continuity objective.  It deliberately allows cycles/swaps.
    current_by_sig: Dict[Tuple[int, int, int, int, int], List[int]] = {}
    for idx, row_now in enumerate(current):
        current_by_sig.setdefault(_hms_compat_signature(row_now), []).append(idx)

    for sig, row_indexes in current_by_sig.items():
        targets = list(dict.fromkeys(prev_by_sig.get(sig, [])))
        if not targets:
            continue
        candidates: Dict[int, List[str]] = {}
        for idx in row_indexes:
            row_now = current[idx]
            old_id = str(row_now.get('room_id') or '')
            good = [
                token for token in targets
                if token not in reserved and _hms_compat_option_fits(option_by_id[token], row_now)
            ]
            good.sort(key=lambda token: (0 if token == old_id else 1, str(option_by_id[token].get('room_label') or token)))
            candidates[idx] = good

        owner: Dict[str, int] = {}
        row_target: Dict[int, str] = {}

        def augment(idx: int, seen: set) -> bool:
            for token in candidates.get(idx, []):
                if token in seen:
                    continue
                seen.add(token)
                other = owner.get(token)
                if other is None or augment(other, seen):
                    owner[token] = idx
                    row_target[idx] = token
                    if other is not None and row_target.get(other) == token:
                        row_target.pop(other, None)
                    return True
            return False

        # Scarce rows first, then rows already sitting in a continuity-compatible room.
        for idx in sorted(row_indexes, key=lambda i: (len(candidates.get(i, [])), 0 if str(current[i].get('room_id') or '') in candidates.get(i, []) else 1, i)):
            augment(idx, set())
        for idx, token in row_target.items():
            if token not in reserved:
                assigned[idx] = token
                reserved.add(token)

    # Phase 2: complete the night with any remaining live-safe unique RoomIDs.
    # Prefer the row's existing RoomID, then another RoomID already in this night's plan,
    # then a genuinely spare room.  This keeps physical changes minimal without blocking swaps.
    current_ids = {str(x.get('room_id') or '').strip() for x in current if str(x.get('room_id') or '').strip()}
    remaining_rows = [i for i, token in enumerate(assigned) if token is None]
    if remaining_rows:
        candidates2: Dict[int, List[str]] = {}
        for idx in remaining_rows:
            row_now = current[idx]
            old_id = str(row_now.get('room_id') or '')
            good = [
                token for token, opt in option_by_id.items()
                if token not in reserved and _hms_compat_option_fits(opt, row_now)
            ]
            good.sort(key=lambda token: (
                0 if token == old_id else 1 if token in current_ids else 2,
                str(option_by_id[token].get('room_label') or token),
            ))
            candidates2[idx] = good

        owner2: Dict[str, int] = {}
        row_target2: Dict[int, str] = {}

        def augment2(idx: int, seen: set) -> bool:
            for token in candidates2.get(idx, []):
                if token in seen:
                    continue
                seen.add(token)
                other = owner2.get(token)
                if other is None or augment2(other, seen):
                    owner2[token] = idx
                    row_target2[idx] = token
                    if other is not None and row_target2.get(other) == token:
                        row_target2.pop(other, None)
                    return True
            return False

        for idx in sorted(remaining_rows, key=lambda i: (len(candidates2.get(i, [])), i)):
            if not augment2(idx, set()):
                row_now = current[idx]
                raise ValueError(
                    f"Немає повної live-безпечної перестановки для {len(current)} номерів цієї ночі: "
                    f"RoomType {row_now.get('room_type_id')}, склад {_hms_compat_signature(row_now)[1:]}."
                )
        for idx, token in row_target2.items():
            assigned[idx] = token

    if any(token is None for token in assigned):
        raise ValueError('Continuity matcher не зміг призначити унікальний фізичний RoomID кожному рядку.')
    if len(set(str(x) for x in assigned)) != len(assigned):
        raise ValueError('Continuity matcher створив дубль фізичного RoomID у межах однієї ночі.')

    out: List[Dict[str, Any]] = []
    for idx, token in enumerate(assigned):
        opt = option_by_id.get(str(token))
        if not opt:
            raise ValueError(f'RoomID {token} зник із live options під час continuity matching.')
        out.append(_hms_compat_room_from_option(opt, current[idx]))
    return out


def _hms_compat_remap_day_for_continuity(row: Any, prev_date: str, day_date: str) -> Dict[str, Any]:
    """Whole-night swap/permutation autofix; never requires an extra room slot."""
    occupancy = _quote_occupancy_by_day(row)
    previous = [dict(x) for x in (occupancy.get(prev_date) or []) if isinstance(x, dict)]
    current = [dict(x) for x in (occupancy.get(day_date) or []) if isinstance(x, dict)]
    if not current:
        return {'plan': current, 'changes': []}

    editor = _manual_editor_context_for_quote(row, day_date)
    options = [dict(x) for x in (editor.get('room_options') or []) if isinstance(x, dict)]
    matched = _hms_compat_match_full_night(previous, current, options)
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
            'mode': 'swap_or_permutation',
        })
    return {'plan': matched, 'changes': changes}
'''


def _replace_function_with_block(src: str, function_name: str, block: str) -> str:
    tree = ast.parse(src)
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name), None)
    if node is None:
        raise RuntimeError(f'function {function_name} not found for v4 patch')
    lines = src.splitlines(keepends=True)
    start = node.lineno - 1
    end = node.end_lineno
    prefix = ''.join(lines[:start])
    suffix = ''.join(lines[end:])
    if prefix and not prefix.endswith('\n\n'):
        prefix = prefix.rstrip('\n') + '\n\n'
    return prefix + block.strip() + '\n\n' + suffix.lstrip('\n')


def patch_module_v4(src: str) -> str:
    out = _orig_patch_module(src)
    out = _replace_function_with_block(out, '_hms_compat_remap_day_for_continuity', NEW_MATCH_AND_REMAP)
    if V4_MARKER not in out:
        raise RuntimeError('v4 marker missing')
    return out


def patch_template_v4(src: str) -> str:
    out = _orig_patch_template(src)
    out = out.replace(
        'Виправити автоматично</button>',
        'Виправити автоматично · swap/permutation</button>',
        1,
    )
    out += '\n<!-- RIVERWOOD_HMS_COMPAT_AUTOFIX_V4_UI -->\n'
    if V4_UI_MARKER not in out:
        raise RuntimeError('v4 UI marker missing')
    return out


base.patch_module = patch_module_v4
base.patch_template = patch_template_v4


def test_v4_swap_matcher(module_text: str) -> None:
    _orig_test(module_text)
    tree = ast.parse(module_text)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    needed = ['_hms_compat_signature', '_hms_compat_option_fits', '_hms_compat_room_from_option', '_hms_compat_match_full_night']
    missing = [x for x in needed if x not in funcs]
    if missing:
        raise RuntimeError(f'missing v4 matcher functions: {missing}')
    ns = {'Any': object, 'Dict': dict, 'List': list, 'Tuple': tuple, 'Optional': object}
    def _ival(v, default=0, minimum=None, maximum=None):
        try: out = int(str(v).strip())
        except Exception: out = default
        if minimum is not None: out = max(minimum, out)
        if maximum is not None: out = min(maximum, out)
        return out
    ns['_ival'] = _ival
    for name in needed:
        exec(compile(ast.Module(body=[funcs[name]], type_ignores=[]), '<v4-test>', 'exec'), ns)
    match = ns['_hms_compat_match_full_night']

    def row(rid, adults, children=0, paid=0, extra=0):
        return {
            'room_id': str(rid), 'room_label': str(rid), 'room_type_id': 2, 'category': 'Standard',
            'adults': adults, 'children': children, 'paid_children': paid, 'extra_beds': extra,
        }
    def opt(rid):
        return {
            'room_id': str(rid), 'room_id_token': str(rid), 'room_label': str(rid),
            'room_type_id': 2, 'category': 'Standard', 'capacity_per_room': 4, 'extra_capacity': 2,
            'early_checkin_available': True, 'late_checkout_available': True,
        }

    # Exact production-class regression: FULL night, no spare room.  The two rows have
    # exchanged compositions between RoomIDs.  v3 greedy logic failed; v4 must swap them back.
    previous = [row(111, 2, 1, 1, 1), row(112, 3, 0, 0, 1)]
    current = [row(111, 3, 0, 0, 1), row(112, 2, 1, 1, 1)]
    matched = match(previous, current, [opt(111), opt(112)])
    assert [x['room_id'] for x in matched] == ['112', '111'], matched
    assert [ns['_hms_compat_signature'](x) for x in matched] == [ns['_hms_compat_signature'](x) for x in current], matched

    # 3-cycle permutation, still with no spare room.
    previous3 = [row(111, 2, 1, 1, 1), row(112, 3, 0, 0, 1), row(113, 2, 0, 0, 0)]
    current3 = [row(111, 3, 0, 0, 1), row(112, 2, 0, 0, 0), row(113, 2, 1, 1, 1)]
    matched3 = match(previous3, current3, [opt(111), opt(112), opt(113)])
    assert [x['room_id'] for x in matched3] == ['112', '113', '111'], matched3
    assert len({x['room_id'] for x in matched3}) == 3, matched3


base.test_compat_logic = test_v4_swap_matcher


def main() -> None:
    v3.verify_writer_reference()
    base.main()
    pkg = base.DIST / V4_NAME
    old = pkg / 'APPLY_V2.bat'
    new = pkg / 'APPLY_V4.bat'
    if old.exists(): old.replace(new)
    report = (pkg / 'TEST_REPORT.txt').read_text(encoding='utf-8') if (pkg / 'TEST_REPORT.txt').exists() else ''
    report += '\nV4 FULL-OCCUPANCY SWAP/PERMUTATION REGRESSION\n'
    report += '2-room composition swap with NO spare room: PASS\n'
    report += '3-room composition cycle with NO spare room: PASS\n'
    report += 'Unique room slots preserved: PASS\n'
    report += 'Per-row writer signature preserved: PASS\n'
    report += 'Writer/sidecar modified: NO\n'
    (pkg / 'TEST_REPORT.txt').write_text(report, encoding='utf-8')
    zip_path = base.DIST / (V4_NAME + '.zip')
    if zip_path.exists(): zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob('*')):
            if p.is_file(): z.write(p, p.relative_to(base.DIST))
    print(json.dumps({'ok': True, 'build': V4_NAME, 'zip_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
