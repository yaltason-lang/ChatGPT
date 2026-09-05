from __future__ import annotations

import ast
import hashlib
import json
import zipfile
from pathlib import Path

import build_v5 as v5
import build_v2 as base

ROOT = Path(__file__).resolve().parent
V6_NAME = 'Riverwood_5SEP_HMS_COMPATIBILITY_CAPACITY_RULE_FIX_v6'
V6_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V6'
V6_UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V6_UI'

base.BUILD_NAME = V6_NAME
base.README = '''Riverwood HMS Compatibility Capacity Rule Fix v6
===============================================

WHY v6
- v5 production error: “Не вдалося знайти жодного допустимого призначення RoomType 2 для 7 рядків цієї ночі.”
- The matcher incorrectly compared all occupants only with base/capacity_per_room and rejected a valid Standard Double row with 3 adults + 1 extra bed when base capacity is 2 and extra capacity is 1.
- That made every candidate disappear before the continuity optimizer could even try the 111<->112 repair.

WHAT v6 CHANGES
- Candidate capacity validation now follows the real room rule: occupants may use base capacity plus the explicitly requested extra beds, provided requested extra beds do not exceed extra_capacity.
- Example now valid: base=2, extra_capacity=1, adults=3, extra_beds=1.
- A row with adults=3 and extra_beds=0 remains invalid for base=2.
- A row requesting extra_beds=2 remains invalid when extra_capacity=1.
- v3 writer-stay detection, v5 minimal continuity optimizer and all HMS hard blocks remain.
- Final proposed plan still passes through the existing live manual recalculation before preview/save.

INSTALL
1. Extract ZIP on the Operations host.
2. Run APPLY_V6.bat.
3. Restart ONLY Operations :8080.
4. Open ACC-20260829-002 and press “Виправити автоматично · capacity-aware repair”.

NOT TOUCHED
- :8082
- :8085
- pms_booking_adapter_v1.py
- GroupCard
- ReserveGroup 1/2/3
'''
base.INSTALLER = base.INSTALLER.replace('v5', 'v6')

_orig_patch_module = base.patch_module
_orig_patch_template = base.patch_template
_orig_test = base.test_compat_logic

V6_FIT_FUNCTION = r'''
def _hms_compat_candidate_fits(meta: Dict[str, Any], row_now: Dict[str, Any], *, is_selected_this_night: bool) -> bool:
    """Validate RoomType/capacity for the exact requested occupancy.

    Critical rule: ``capacity_per_room``/``base_capacity`` is the base occupancy.
    A requested extra bed increases usable occupancy by one only when the room exposes
    enough ``extra_capacity``.  v5 compared occupants only to the base capacity and
    therefore rejected valid 3-adult Standard Double + 1 extra-bed rows.
    """
    if _ival(meta.get('room_type_id'), 0, minimum=0) != _ival(row_now.get('room_type_id'), 0, minimum=0):
        return False

    adults = _ival(row_now.get('adults'), 0, minimum=0)
    children = _ival(row_now.get('children'), 0, minimum=0)
    occupants = adults + children
    extra_need = _ival(row_now.get('extra_beds'), 0, minimum=0)

    base_cap = _ival(meta.get('base_capacity'), 0, minimum=0)
    if base_cap <= 0:
        base_cap = _ival(meta.get('capacity_per_room'), 0, minimum=0)
    extra_cap = _ival(meta.get('extra_capacity'), 0, minimum=0)

    if extra_need > extra_cap:
        return False

    # Every requested extra bed may cover at most one occupant beyond base capacity.
    # Do not silently use unused extra_capacity when no extra bed is requested.
    effective_cap = base_cap + extra_need if base_cap > 0 else 0
    if effective_cap and occupants > effective_cap:
        return False

    # Defensive cross-check for sources where capacity_per_room already represents a
    # configured absolute maximum larger than base_capacity.  Never make the rule stricter
    # than the room metadata itself, but still require explicit extra_beds for overflow.
    absolute_cap = _ival(meta.get('capacity_per_room'), 0, minimum=0)
    if absolute_cap > base_cap:
        effective_cap = max(effective_cap, min(absolute_cap, base_cap + extra_need if base_cap > 0 else absolute_cap))
        if effective_cap and occupants > effective_cap:
            return False

    if bool(row_now.get('early_checkin')) and not bool(meta.get('early_checkin_available')):
        if not is_selected_this_night:
            return False
    if bool(row_now.get('late_checkout')) and not bool(meta.get('late_checkout_available')):
        if not is_selected_this_night:
            return False
    return True
'''


def _replace_function(src: str, function_name: str, replacement: str) -> str:
    tree = ast.parse(src)
    node = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == function_name), None)
    if node is None:
        raise RuntimeError(f'function {function_name} not found for v6 patch')
    lines = src.splitlines(keepends=True)
    prefix = ''.join(lines[:node.lineno - 1]).rstrip('\n') + '\n\n'
    suffix = ''.join(lines[node.end_lineno:]).lstrip('\n')
    return prefix + replacement.strip() + '\n\n' + suffix


def patch_module_v6(src: str) -> str:
    out = _orig_patch_module(src)
    out = _replace_function(out, '_hms_compat_candidate_fits', V6_FIT_FUNCTION)
    out += '\n# RIVERWOOD_HMS_COMPAT_AUTOFIX_V6\nHMS_COMPATIBILITY_AUTOFIX_V6_MARKER = \'RIVERWOOD_HMS_COMPAT_AUTOFIX_V6\'\n'
    if V6_MARKER not in out:
        raise RuntimeError('v6 marker missing')
    return out


def patch_template_v6(src: str) -> str:
    out = _orig_patch_template(src)
    out = out.replace('Виправити автоматично · minimal repair</button>', 'Виправити автоматично · capacity-aware repair</button>', 1)
    out += '\n<!-- RIVERWOOD_HMS_COMPAT_AUTOFIX_V6_UI -->\n'
    if V6_UI_MARKER not in out:
        raise RuntimeError('v6 UI marker missing')
    return out


base.patch_module = patch_module_v6
base.patch_template = patch_template_v6


def test_v6_capacity_rule(module_text: str) -> None:
    _orig_test(module_text)
    tree = ast.parse(module_text)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    needed = [
        '_hms_compat_signature', '_hms_compat_meta_for_selected_room',
        '_hms_compat_candidate_fits', '_hms_compat_best_assignment_for_type',
    ]
    missing = [x for x in needed if x not in funcs]
    if missing:
        raise RuntimeError(f'missing v6 functions: {missing}')
    ns = {'Any': object, 'Dict': dict, 'List': list, 'Tuple': tuple, 'Optional': object}
    def _ival(v, default=0, minimum=None, maximum=None):
        try: out = int(str(v).strip())
        except Exception: out = default
        if minimum is not None: out = max(minimum, out)
        if maximum is not None: out = min(maximum, out)
        return out
    ns['_ival'] = _ival
    for name in needed:
        exec(compile(ast.Module(body=[funcs[name]], type_ignores=[]), '<v6-test>', 'exec'), ns)

    fits = ns['_hms_compat_candidate_fits']
    assign = ns['_hms_compat_best_assignment_for_type']

    def row(rid, adults, children=0, paid=0, extra=0, rt=2):
        return {
            'room_id': str(rid), 'room_label': str(rid), 'room_type_id': rt, 'category': 'Standard Double',
            'adults': adults, 'children': children, 'paid_children': paid, 'extra_beds': extra,
            'capacity_per_room': 2, 'base_capacity': 2, 'extra_capacity': 1,
        }
    def opt(rid, rt=2):
        return {
            'room_id': str(rid), 'room_id_token': str(rid), 'room_label': str(rid), 'room_type_id': rt,
            'category': 'Standard Double', 'capacity_per_room': 2, 'base_capacity': 2, 'extra_capacity': 1,
            'early_checkin_available': True, 'late_checkout_available': True,
        }

    # Exact production regression from room 111: 3 adults are valid only because one
    # extra bed is explicitly requested and this RoomType supports one extra place.
    assert fits(opt(111), row(111, 3, 0, 0, 1), is_selected_this_night=True), '3 adults + 1 extra bed must fit base2+extra1'
    assert fits(opt(111), row(111, 2, 1, 1, 1), is_selected_this_night=True), '2 adults + 1 child + 1 extra bed must fit base2+extra1'
    assert not fits(opt(111), row(111, 3, 0, 0, 0), is_selected_this_night=True), '3 adults without requested extra bed must not fit base2'
    assert not fits(opt(111), row(111, 4, 0, 0, 2), is_selected_this_night=True), 'cannot request 2 extras when extra_capacity=1'

    A = (3, 0, 0, 1)
    B = (2, 1, 1, 1)
    C = (2, 0, 0, 0)
    def r(rid, sig): return row(rid, sig[0], sig[1], sig[2], sig[3])

    previous = [r(101,A), r(102,A), r(103,A), r(104,A), r(105,A), r(106,A), r(112,A), r(111,B), r(201,C)]
    current  = [r(101,A), r(102,A), r(103,A), r(104,A), r(105,A), r(106,A), r(111,A), r(112,B), r(201,C)]
    nextplan = [r(101,A), r(102,A), r(103,A), r(104,A), r(105,A), r(106,A), r(112,A), r(111,B), r(201,C)]
    # Live list may contain only the two rooms that need swapping.  All seven A rows use
    # base=2 + extra=1 and must remain assignable.
    result = assign(list(range(9)), current, previous, nextplan, [opt(111), opt(112)])
    assigned = [result[i] for i in range(9)]
    assert assigned[:6] == ['101','102','103','104','105','106'], assigned
    assert assigned[6] == '112' and assigned[7] == '111' and assigned[8] == '201', assigned


base.test_compat_logic = test_v6_capacity_rule


def main() -> None:
    v5.v4.v3.verify_writer_reference()
    base.main()
    pkg = base.DIST / V6_NAME
    old = pkg / 'APPLY_V2.bat'
    new = pkg / 'APPLY_V6.bat'
    if old.exists(): old.replace(new)
    report = (pkg / 'TEST_REPORT.txt').read_text(encoding='utf-8') if (pkg / 'TEST_REPORT.txt').exists() else ''
    report += '\nV6 CAPACITY RULE REGRESSION\n'
    report += 'base=2 + extra_capacity=1 + 3 adults + extra_beds=1: PASS\n'
    report += 'base=2 + 3 adults + extra_beds=0 rejected: PASS\n'
    report += 'extra_beds > extra_capacity rejected: PASS\n'
    report += '7-row RoomType2 production-shaped minimal swap with base2+extra1: PASS\n'
    report += 'Writer/sidecar modified: NO\n'
    (pkg / 'TEST_REPORT.txt').write_text(report, encoding='utf-8')
    zip_path = base.DIST / (V6_NAME + '.zip')
    if zip_path.exists(): zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob('*')):
            if p.is_file(): z.write(p, p.relative_to(base.DIST))
    print(json.dumps({'ok': True, 'build': V6_NAME, 'zip_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
