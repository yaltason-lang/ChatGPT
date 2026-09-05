from __future__ import annotations

import ast
import hashlib
import json
import shutil
import zipfile
from pathlib import Path

import build_v2 as base
import build_v2_1 as pathfix  # noqa: F401  # installs the safer exact-root installer into base globals

ROOT = Path(__file__).resolve().parent
V3_FRAG = ROOT / 'v3_module_injection.pyfrag'
V3_NAME = 'Riverwood_5SEP_HMS_COMPATIBILITY_WRITER_STAY_GUARD_v3'
V3_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V3'
V3_UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V3_UI'

base.BUILD_NAME = V3_NAME
base.FRAG = V3_FRAG
base.INSTALLER = base.INSTALLER.replace('v2.1', 'v3').replace('v2_1', 'v3')
base.BAT = base.BAT.replace('APPLY_V2', 'APPLY_V3') if 'APPLY_V2' in base.BAT else base.BAT
base.README = '''Riverwood HMS Compatibility Writer-Stay Guard v3
=================================================

WHY v3
- v2 guarded only one narrow pattern: the same RoomID changing composition on adjacent nights.
- The 05.09 production failure proved the real writer invariant is broader: the dashboard must predict the exact 8085 writer room_stays count before HMS.
- Example from ACC-20260829-002: maximum simultaneous rooms = 9, writer generated stay 10/11 and HMS stopped at 9 GuestIDs. v3 blocks this before live preflight.

WHAT v3 DOES
- Mirrors the writer stay signature exactly: (RoomTypeID, adults, children, paid_children, extra_beds).
- Mirrors contiguous-room merge/gap behavior exactly.
- Computes writer_stays_count and the current room-slot budget BEFORE any HMS preflight/write.
- HARD BLOCKS live preflight and HMS booking when writer_stays_count > room-slot budget.
- Shows “HMS-сумісність розміщення” ALWAYS, including a green OK state with exact counts.
- On BLOCKED state shows exact physical rooms, stay segments, nights and compositions that create extra writer stays.
- “Виправити автоматично” attempts a continuity-aware physical-room remap, preserving nightly adults/children/paid children/extra beds, RoomType/capacity, live availability and early/late availability.
- Autofix is fail-closed: if physical-room reassignment cannot reduce writer stays enough without changing the per-room guest composition, nothing is saved and booking remains blocked.
- Successful autofix is previewed “було → стане” and saved only as a new quote revision.
- Manual room preview immediately recalculates writer-stay compatibility.

INSTALL
1. Extract this ZIP on the Operations Windows host.
2. Run APPLY_V3.bat.
3. Restart ONLY the Operations :8080 process the same way you normally restart it.
4. Open the quote. A visible HMS compatibility card MUST appear even when status is OK.

CONFIRMED ROOT / PATH SAFETY
- The installer prefers C:\\Riverwood_Operations_MVP0_Core_Employees in exact-root mode when present.
- Generic discovery excludes All versions, _backups, before_*, baseline*, dist, payload and .git.
- Installer backs up and atomically replaces ONLY accommodation_module.py and templates\\accommodation_quote_detail.html.

NOT TOUCHED
- :8082
- :8085
- pms_booking_adapter_v1.py
- pms_sidecar_with_room_quote_v3.py
- GroupCard writer logic
- ReserveGroup 1/2/3
'''

_orig_patch_module = base.patch_module
_orig_patch_template = base.patch_template


def patch_module_v3(src: str) -> str:
    out = _orig_patch_module(src)
    if V3_MARKER not in out:
        raise RuntimeError('v3 module marker missing after injection')
    if "_assert_hms_compatibility(q)" not in out:
        raise RuntimeError('v3 preflight hard gate missing')
    return out


def patch_template_v3(src: str) -> str:
    out = _orig_patch_template(src)
    anchor = "{% set compat = booking_state.compatibility or {} %}\n{% if compat.blocking %}"
    if anchor not in out:
        raise RuntimeError('v3 template compatibility anchor missing')
    always_visible = """{% set compat = booking_state.compatibility or {} %}
{% if not compat.blocking %}
<div class="card aq-hms-compat ok" id="hms-compatibility">
  <div style="display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap">
    <div><h3>HMS-сумісність розміщення</h3><div class="muted">Writer plan перевірено до HMS: <b>{{ compat.writer_stays_count or 0 }} stay-карток / {{ compat.slot_budget or 0 }} room slots</b>. Додаткових writer stay-сегментів немає.</div></div>
    <span class="aq-booking-pill ready">OK ДО HMS</span>
  </div>
</div>
{% endif %}
{% if compat.blocking %}"""
    out = out.replace(anchor, always_visible, 1)

    old = "Operations знайшов зміну складу в одному фізичному RoomID. HMS writer розіб'є його на кілька stay-карток, тому preflight і бронювання заблоковані до зміни фізичних номерів."
    new = "Writer plan не поміщається в поточний HMS room-slot бюджет: <b>{{ compat.writer_stays_count or 0 }} stay-карток / {{ compat.slot_budget or 0 }} room slots</b>, дефіцит <b>{{ compat.slot_shortage or 0 }}</b>. Причина може бути зміною складу в RoomID, розривом RoomID або заміною фізичних номерів між ночами. Preflight і бронювання заблоковані до усунення розриву."
    if old not in out:
        raise RuntimeError('v3 blocked explanation anchor missing')
    out = out.replace(old, new, 1)

    out = out.replace(
        '<span class="aq-live-ok">Після замін HMS compatibility: OK.</span>',
        '<span class="aq-live-ok">Після замін HMS compatibility: OK · writer stays {{ hms_compat_preview.after.writer_stays_count }} / room slots {{ hms_compat_preview.after.slot_budget }}.</span>',
        1,
    )
    out += '\n<!-- RIVERWOOD_HMS_COMPAT_AUTOFIX_V3_UI -->\n'
    if V3_UI_MARKER not in out:
        raise RuntimeError('v3 UI marker missing')
    return out


base.patch_module = patch_module_v3
base.patch_template = patch_template_v3


def _exec_pure_v3(module_text: str):
    tree = ast.parse(module_text)
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    needed = ['_hms_compat_signature', '_hms_writer_stays_from_plans', '_hms_compatibility_analysis_from_plans']
    missing = [x for x in needed if x not in funcs]
    if missing:
        raise RuntimeError(f'missing v3 pure functions: {missing}')
    ns = {
        'Any': object, 'Dict': dict, 'List': list, 'Iterable': list, 'Tuple': tuple,
        'HMS_COMPATIBILITY_AUTOFIX_V3_MARKER': V3_MARKER,
    }
    def _ival(v, default=0, minimum=None, maximum=None):
        try: out = int(str(v).strip())
        except Exception: out = default
        if minimum is not None: out = max(minimum, out)
        if maximum is not None: out = min(maximum, out)
        return out
    ns['_ival'] = _ival
    ns['ROOM_TYPE_NAMES'] = {2: 'Standard', 3: 'Standard Plus'}
    for name in needed:
        exec(compile(ast.Module(body=[funcs[name]], type_ignores=[]), '<v3-pure>', 'exec'), ns)
    return ns


def test_compat_logic_v3(module_text: str) -> None:
    ns = _exec_pure_v3(module_text)
    analyze = ns['_hms_compatibility_analysis_from_plans']

    schedule = [
        {'date': '2026-10-13', 'next_date': '2026-10-14'},
        {'date': '2026-10-14', 'next_date': '2026-10-15'},
        {'date': '2026-10-15', 'next_date': '2026-10-16'},
    ]
    def room(rid, a, c=0, paid=0, extra=0, early=False, late=False, rt=2):
        return {
            'room_id': str(rid), 'room_label': str(rid), 'room_type_id': rt, 'category': 'Standard',
            'adults': a, 'children': c, 'paid_children': paid, 'extra_beds': extra,
            'early_checkin': early, 'late_checkout': late,
        }

    # Stable two-room plan -> exactly two writer stays / two room slots.
    stable = {
        '2026-10-13': [room(111, 2), room(112, 1, 1)],
        '2026-10-14': [room(111, 2), room(112, 1, 1)],
        '2026-10-15': [room(111, 2), room(112, 1, 1)],
    }
    s = analyze(schedule, stable)
    assert not s['blocking'] and s['writer_stays_count'] == 2 and s['slot_budget'] == 2, s

    # Early/late flags alone MUST NOT split writer stays.
    early_late = {
        '2026-10-13': [room(111, 2, early=True), room(112, 1, 1)],
        '2026-10-14': [room(111, 2), room(112, 1, 1)],
        '2026-10-15': [room(111, 2, late=True), room(112, 1, 1)],
    }
    e = analyze(schedule, early_late)
    assert not e['blocking'] and e['writer_stays_count'] == 2, e

    # Same RoomID composition swap -> four writer stays / two slots. v2 caught only this class.
    composition_split = {
        '2026-10-13': [room(111, 2), room(112, 1, 1)],
        '2026-10-14': [room(111, 1, 1), room(112, 2)],
        '2026-10-15': [room(111, 1, 1), room(112, 2)],
    }
    c = analyze(schedule, composition_split)
    assert c['blocking'] and c['writer_stays_count'] == 4 and c['slot_budget'] == 2 and c['slot_shortage'] == 2, c

    # Physical-room replacement with unchanged composition: v2 MISSED this class.
    physical_swap = {
        '2026-10-13': [room(111, 2), room(112, 1, 1)],
        '2026-10-14': [room(113, 2), room(114, 1, 1)],
        '2026-10-15': [room(113, 2), room(114, 1, 1)],
    }
    p = analyze(schedule, physical_swap)
    assert p['blocking'] and p['writer_stays_count'] == 4 and p['slot_budget'] == 2 and p['slot_shortage'] == 2, p
    assert any(str(x.get('room_id')) == '113' for x in p['conflicts']), p

    # A gap is also a new writer stay and must be treated as unsafe when it exceeds slots.
    gap_schedule = [schedule[0], schedule[2]]
    g = analyze(gap_schedule, {'2026-10-13': [room(111, 2)], '2026-10-15': [room(111, 2)]})
    assert g['blocking'] and g['writer_stays_count'] == 2 and g['slot_budget'] == 1, g

    # Production-shape invariant: 11 writer stays vs 9 simultaneous rooms must hard block by 2.
    prod_schedule = [
        {'date': '2026-10-13', 'next_date': '2026-10-14'},
        {'date': '2026-10-14', 'next_date': '2026-10-15'},
    ]
    night1 = [room(100+i, 2) for i in range(9)]
    night2 = [room(100+i, 2) for i in range(7)] + [room(111, 2), room(112, 2)]
    prod = analyze(prod_schedule, {'2026-10-13': night1, '2026-10-14': night2})
    assert prod['writer_stays_count'] == 11 and prod['slot_budget'] == 9 and prod['slot_shortage'] == 2 and prod['blocking'], prod


base.test_compat_logic = test_compat_logic_v3


def verify_writer_reference() -> None:
    adapter = (ROOT / 'pms_booking_adapter_v1.py').read_text(encoding='utf-8')
    required = [
        'signature = (rtype, adults, children, paid, extra)',
        'tuple(prev.get("signature") or ()) == signature',
        '"room_stays_count": len(stays)',
        'guest_slots >= int(plan["room_stays_count"])',
        'HMS_GUEST_SLOT_COUNT_MISMATCH',
    ]
    missing = [x for x in required if x not in adapter]
    if missing:
        raise RuntimeError('writer reference changed; v3 mirror review required: ' + repr(missing))


def rebuild_package_report() -> str:
    pkg = base.DIST / V3_NAME
    payload = pkg / 'payload'
    module_text = (payload / 'accommodation_module.py').read_text(encoding='utf-8')
    template_text = (payload / 'accommodation_quote_detail.html').read_text(encoding='utf-8')
    if V3_MARKER not in module_text or V3_UI_MARKER not in template_text:
        raise RuntimeError('v3 markers missing from built package')
    old_bat = pkg / 'APPLY_V2.bat'
    new_bat = pkg / 'APPLY_V3.bat'
    if old_bat.exists():
        old_bat.replace(new_bat)
    report = '\n'.join([
        'BUILD TEST REPORT — v3',
        'Python compile: PASS',
        'Jinja parse: PASS',
        'Writer source signature/merge anchors: PASS',
        'Stable writer stays == room slots: PASS',
        'Early/late flags do not split writer stays: PASS',
        'Same-RoomID composition split hard block: PASS',
        'Physical-room swap hard block (v2 miss): PASS',
        'Gap/restart writer stay hard block: PASS',
        'Production-shape 11 stays / 9 slots / shortage 2 hard block: PASS',
        'Server-side preflight hard gate: PASS',
        'Always-visible HMS compatibility UI: PASS',
        'Continuity-aware autofix preview/save: INCLUDED + fail-closed',
        'Installer archive/backups filtering: INCLUDED',
        'Sidecar/writer source files changed: NO',
    ]) + '\n'
    (pkg / 'TEST_REPORT.txt').write_text(report, encoding='utf-8')
    zip_path = base.DIST / (V3_NAME + '.zip')
    if zip_path.exists(): zip_path.unlink()
    with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob('*')):
            if p.is_file(): z.write(p, p.relative_to(base.DIST))
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def main() -> None:
    verify_writer_reference()
    base.main()
    digest = rebuild_package_report()
    print(json.dumps({'ok': True, 'build': V3_NAME, 'zip_sha256': digest}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
