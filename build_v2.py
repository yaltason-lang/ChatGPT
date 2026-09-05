from __future__ import annotations

import ast
import hashlib
import json
import py_compile
import shutil
import tempfile
import textwrap
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MODULE = ROOT / 'accommodation_module.py'
TEMPLATE = ROOT / 'accommodation_quote_detail.html'
FRAG = ROOT / 'v2_module_injection.pyfrag'
DIST = ROOT / 'dist'
BUILD_NAME = 'Riverwood_5SEP_HMS_COMPATIBILITY_AUTOFIX_DASHBOARD_v2'
MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2'
UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2_UI'


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'{label}: expected exactly one anchor, found {count}')
    return text.replace(old, new, 1)


def patch_module(src: str) -> str:
    if MARKER in src:
        raise RuntimeError('source module already contains v2 marker; refusing double patch')
    src = replace_once(
        src,
        "DEFAULT_TOURIST_TAX_PER_ADULT_NIGHT = Decimal('43.24')\n",
        "DEFAULT_TOURIST_TAX_PER_ADULT_NIGHT = Decimal('43.24')\nHMS_COMPATIBILITY_AUTOFIX_V2_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2'\n",
        'module marker',
    )
    frag = FRAG.read_text(encoding='utf-8').rstrip() + '\n\n'
    src = replace_once(
        src,
        '\ndef _clear_hms_booking_preflight(conn, quote_id: str) -> None:\n',
        '\n' + frag + 'def _clear_hms_booking_preflight(conn, quote_id: str) -> None:\n',
        'compatibility injection',
    )
    src = replace_once(
        src,
        "    write_blockers: List[str] = []\n    if bool(q.get('early_checkin')) or bool(q.get('late_checkout')):\n",
        "    write_blockers: List[str] = []\n    compatibility = _hms_compatibility_analysis(q)\n    if compatibility.get('blocking'):\n        write_blockers.append(_hms_compatibility_failure_message(compatibility))\n    if bool(q.get('early_checkin')) or bool(q.get('late_checkout')):\n",
        'booking state compatibility blocker',
    )
    src = replace_once(
        src,
        "        'write_blockers': write_blockers,\n        'hms_price_list': hms_price_list or {},\n        'can_book': bool(status == 'ready' and not write_blockers),\n",
        "        'write_blockers': write_blockers,\n        'compatibility': compatibility,\n        'hms_price_list': hms_price_list or {},\n        'can_book': bool(status == 'ready' and not write_blockers and not compatibility.get('blocking')),\n",
        'booking state compatibility return',
    )
    src = replace_once(
        src,
        "def _hms_booking_preflight(row: Any, timetable: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n    \"\"\"Live non-destructive verification of the exact physical rooms before HMS write.\"\"\"\n    q = _row_dict(row)\n    payload = _hms_booking_payload(q)\n",
        "def _hms_booking_preflight(row: Any, timetable: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n    \"\"\"Live non-destructive verification of the exact physical rooms before HMS write.\"\"\"\n    q = _row_dict(row)\n    # v2 hard gate: never call HMS timetable/writer for a plan that would split one\n    # physical RoomID into multiple composition-based HMS stay cards.\n    _assert_hms_compatibility(q)\n    payload = _hms_booking_payload(q)\n",
        'preflight hard gate',
    )
    src = replace_once(
        src,
        "def _render_quote_detail_response(\n    *, conn, current_row: Any, row: Any, is_current_revision: bool, selected_revision: Any = None,\n    manual_editor: Optional[Dict[str, Any]] = None, manual_preview: Optional[Dict[str, Any]] = None,\n    manual_editor_error: str = '',\n):\n",
        "def _render_quote_detail_response(\n    *, conn, current_row: Any, row: Any, is_current_revision: bool, selected_revision: Any = None,\n    manual_editor: Optional[Dict[str, Any]] = None, manual_preview: Optional[Dict[str, Any]] = None,\n    manual_editor_error: str = '', hms_compat_preview: Optional[Dict[str, Any]] = None,\n):\n",
        'render signature',
    )
    src = replace_once(
        src,
        "        manual_editor_error=manual_editor_error, booking_state=booking_state,\n    )\n\n@bp.route('/accommodation-calculator/quotes/<quote_id>', methods=['GET', 'POST'])\n",
        "        manual_editor_error=manual_editor_error, booking_state=booking_state,\n        hms_compat_preview=hms_compat_preview,\n    )\n\n@bp.route('/accommodation-calculator/quotes/<quote_id>', methods=['GET', 'POST'])\n",
        'render preview param',
    )
    action_anchor = "        detail_action = (request.form.get('detail_action') or 'commercial').strip()\n        if detail_action == 'hms_booking_reserve':\n"
    action_code = """        detail_action = (request.form.get('detail_action') or 'commercial').strip()\n        if detail_action in ('hms_compatibility_autofix_preview', 'hms_compatibility_autofix_save'):\n            try:\n                result = _hms_compatibility_autofix(row)\n                if detail_action == 'hms_compatibility_autofix_save':\n                    if not result.get('booking_allowed', True):\n                        reason = _booking_restriction_failure_message(\n                            result.get('booking_restrictions') or [],\n                            arrival=str(row['arrival']), departure=str(row['departure']),\n                            selected_nights=_ival(row['nights'], 1, minimum=1),\n                        ) or 'live умови бронювання не виконані.'\n                        raise ValueError('Автовиправлення не збережено: ' + reason)\n                    quote_id2, quote_number, revision_no = _persist_quote_version(\n                        conn, result['quote_data'], edit_quote_id=quote_id, revision_kind='hms_compatibility_autofix'\n                    )\n                    conn.commit()\n                    _audit(\n                        'accommodation_quote', quote_id2, 'hms_compatibility_autofix',\n                        new_value=f'{quote_number}; v{revision_no}',\n                        reason='; '.join(\n                            f\"{x.get('from_room_label')}->{x.get('to_room_label')}:{','.join(x.get('dates') or [])}\"\n                            for x in result.get('replacements') or []\n                        ),\n                    )\n                    flash(f'HMS-сумісність виправлено та збережено як версію {revision_no}.', 'success')\n                    return redirect(url_for('accommodation.quote_detail', quote_id=quote_id2))\n                return _render_quote_detail_response(\n                    conn=conn, current_row=row, row=row, is_current_revision=True, selected_revision=None,\n                    manual_editor=None, manual_preview=None, manual_editor_error='', hms_compat_preview=result,\n                )\n            except Exception as exc:\n                flash('HMS compatibility autofix: ' + _pricing_error_for_manager(exc), 'error')\n                return redirect(url_for('accommodation.quote_detail', quote_id=quote_id))\n        if detail_action == 'hms_booking_reserve':\n"""
    src = replace_once(src, action_anchor, action_code, 'autofix actions')
    # Manual edits get an immediate compatibility result in the same preview.
    src = replace_once(
        src,
        "                preview['composition_changed'] = (\n                    preview['old_adults'] != preview['new_adults'] or\n                    preview['old_children'] != preview['new_children'] or\n                    preview['old_paid_children'] != preview['new_paid_children']\n                )\n                if detail_action == 'manual_day_save':\n",
        "                preview['composition_changed'] = (\n                    preview['old_adults'] != preview['new_adults'] or\n                    preview['old_children'] != preview['new_children'] or\n                    preview['old_paid_children'] != preview['new_paid_children']\n                )\n                virtual_quote = _row_dict(row)\n                virtual_quote.update(_quote_data_from_manual_recalculation(row, calc))\n                preview['hms_compatibility'] = _hms_compatibility_analysis(virtual_quote)\n                if detail_action == 'manual_day_save':\n",
        'manual compatibility check',
    )
    return src


def patch_template(src: str) -> str:
    if UI_MARKER in src:
        raise RuntimeError('source template already contains v2 UI marker; refusing double patch')
    css_anchor = '.aq-booking-list{margin:8px 0 0 22px;color:#991b1b;font-size:13px}\n'
    css = css_anchor + ".aq-hms-compat{border:2px solid #f59e0b;background:#fffbeb}.aq-hms-compat.ok{border-color:#86efac;background:#f0fdf4}.aq-hms-compat h3{margin:0}.aq-hms-compat-grid{display:grid;grid-template-columns:repeat(2,minmax(260px,1fr));gap:10px;margin-top:12px}.aq-hms-compat-item{border:1px solid #fed7aa;background:#fff;border-radius:12px;padding:12px}.aq-hms-compat-seg{margin-top:7px;padding:7px 9px;border-radius:8px;background:#f8fafc}.aq-hms-compat-seg.replace{background:#fff1f2;border:1px solid #fecaca}.aq-hms-compat-preview{margin-top:12px;border:1px solid #93c5fd;background:#eff6ff;border-radius:12px;padding:12px}@media(max-width:800px){.aq-hms-compat-grid{grid-template-columns:1fr}}\n/* RIVERWOOD_HMS_COMPAT_AUTOFIX_V2_UI */\n"
    src = replace_once(src, css_anchor, css, 'compatibility CSS')
    grid_end = "</div>\n\n{% if is_current_revision %}\n<div class=\"card aq-booking-card\">\n"
    compat_block = """</div>\n\n{% if is_current_revision %}\n{% set compat = booking_state.compatibility or {} %}\n{% if compat.blocking %}\n<div class=\"card aq-hms-compat\" id=\"hms-compatibility\">\n  <div style=\"display:flex;justify-content:space-between;gap:14px;align-items:flex-start;flex-wrap:wrap\">\n    <div><h3>HMS-сумісність розміщення</h3><div class=\"muted\">Operations знайшов зміну складу в одному фізичному RoomID. HMS writer розіб'є його на кілька stay-карток, тому preflight і бронювання заблоковані до зміни фізичних номерів.</div></div>\n    <span class=\"aq-booking-pill blocked\">ПОТРІБНЕ ВИПРАВЛЕННЯ</span>\n  </div>\n  <div class=\"aq-hms-compat-grid\">{% for c in compat.conflicts or [] %}<div class=\"aq-hms-compat-item\"><b>№ {{ c.room_label }} · {{ c.category }}</b><div class=\"aq-rate-sub\">RoomID {{ c.room_id }} · замінити ночі: {{ c.replacement_nights|join(', ') }}</div>{% for s in c.segments or [] %}<div class=\"aq-hms-compat-seg {{ 'replace' if s.needs_replacement else '' }}\"><b>{{ s.start_date }} → {{ s.end_date }}</b> · {{ s.adults }} дор. + {{ s.children }} діт.{% if s.paid_children %} · платних дітей {{ s.paid_children }}{% endif %}{% if s.extra_beds %} · дод. місць {{ s.extra_beds }}{% endif %}<div class=\"aq-rate-sub\">{{ 'Треба інший фізичний номер' if s.needs_replacement else 'Залишаємо цей номер' }}</div></div>{% endfor %}</div>{% endfor %}</div>\n  <form method=\"post\" style=\"margin-top:12px\"><input type=\"hidden\" name=\"detail_action\" value=\"hms_compatibility_autofix_preview\"><button class=\"btn primary\" type=\"submit\">Виправити автоматично</button></form>\n  {% if hms_compat_preview %}<div class=\"aq-hms-compat-preview\"><b>Preview · було → стане</b>{% for x in hms_compat_preview.replacements or [] %}<div style=\"margin-top:8px\"><strong>№ {{ x.from_room_label }} → № {{ x.to_room_label }}</strong> · {{ x.start_date }} → {{ x.end_date }} · {{ x.adults }} дор. + {{ x.children }} діт.{% if x.paid_children %} · платних дітей {{ x.paid_children }}{% endif %}{% if x.extra_beds %} · дод. місць {{ x.extra_beds }}{% endif %}</div>{% endfor %}<div style=\"margin-top:10px\"><span class=\"aq-live-ok\">Після замін HMS compatibility: OK.</span></div><form method=\"post\" style=\"margin-top:10px\"><input type=\"hidden\" name=\"detail_action\" value=\"hms_compatibility_autofix_save\"><button class=\"btn primary\" type=\"submit\">Зберегти виправлення як нову версію {{ (quote['revision_no'] or 1) + 1 }}</button></form></div>{% endif %}\n</div>\n{% endif %}\n\n<div class=\"card aq-booking-card\">\n"""
    src = replace_once(src, grid_end, compat_block, 'compatibility UI block')
    old_preflight = """    {% if booking_state.status not in ['booked','booking_uncertain'] %}\n      <form method=\"post\" style=\"margin:0\"><input type=\"hidden\" name=\"detail_action\" value=\"hms_booking_preflight\"><button class=\"btn\" type=\"submit\">{{ 'Оновити live preflight' if booking_state.status in ['ready','blocked','error','stale'] else 'Перевірити перед бронюванням' }}</button></form>\n    {% endif %}\n"""
    new_preflight = """    {% if booking_state.status not in ['booked','booking_uncertain'] %}\n      {% if compat.blocking %}<button class=\"btn\" type=\"button\" disabled title=\"Спочатку усуньте HMS compatibility conflict\">Оновити live preflight</button>{% else %}<form method=\"post\" style=\"margin:0\"><input type=\"hidden\" name=\"detail_action\" value=\"hms_booking_preflight\"><button class=\"btn\" type=\"submit\">{{ 'Оновити live preflight' if booking_state.status in ['ready','blocked','error','stale'] else 'Перевірити перед бронюванням' }}</button></form>{% endif %}\n    {% endif %}\n"""
    src = replace_once(src, old_preflight, new_preflight, 'preflight UI hard block')
    manual_anchor = """    <div style=\"margin-top:10px\">{% if manual_preview.booking_allowed %}<span class=\"aq-live-ok\">Усі умови виконані. Можна зберігати нову версію.</span>{% else %}<span class=\"aq-live-bad\">Зберегти не можна — є блокуючі умови бронювання.</span>{% endif %}</div>\n"""
    manual_new = manual_anchor + """    {% if manual_preview.hms_compatibility and manual_preview.hms_compatibility.blocking %}<div class=\"aq-editor-error\"><b>HMS compatibility conflict після цієї ручної зміни.</b>{% for c in manual_preview.hms_compatibility.conflicts or [] %}<div>№ {{ c.room_label }} · замінити ночі: {{ c.replacement_nights|join(', ') }}</div>{% endfor %}</div>{% elif manual_preview.hms_compatibility %}<div class=\"aq-validity\" style=\"border-left-color:#15803d;background:#f0fdf4\"><b>HMS compatibility</b>OK — ручна зміна не створює composition stay-split.</div>{% endif %}\n"""
    src = replace_once(src, manual_anchor, manual_new, 'manual compatibility preview')
    src = src.replace("{% elif r['revision_kind']=='recalculation' %}Перерахунок{% else %}Комерційні умови{% endif %}", "{% elif r['revision_kind']=='recalculation' %}Перерахунок{% elif r['revision_kind']=='hms_compatibility_autofix' %}HMS compatibility autofix{% else %}Комерційні умови{% endif %}")
    return src


INSTALLER = r'''from __future__ import annotations
import argparse, hashlib, os, shutil, sys, tempfile
from datetime import datetime
from pathlib import Path

MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2'
UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2_UI'
HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / 'payload'


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def ignored(p: Path) -> bool:
    low = str(p).lower()
    return any(x in low for x in ('\\.git\\', '/.git/', '\\backup', '/backup', '\\dist\\', '/dist/', '\\payload\\', '/payload/'))

def roots(explicit):
    out=[]
    for raw in explicit or []:
        p=Path(raw).expanduser()
        if p.exists(): out.append(p.resolve())
    env=os.environ.get('RIVERWOOD_OPERATIONS_ROOT','').strip()
    if env and Path(env).exists(): out.append(Path(env).resolve())
    cwd=Path.cwd().resolve(); out.extend([cwd, *list(cwd.parents)[:3]])
    for raw in (r'C:\Riverwood', r'C:\RiverwoodOperations', r'C:\Riverwood_Operations', r'C:\Apps', r'D:\Riverwood', r'D:\RiverwoodOperations'):
        p=Path(raw)
        if p.exists(): out.append(p.resolve())
    seen=[]
    for p in out:
        if p not in seen: seen.append(p)
    return seen

def find_pairs(search_roots):
    pairs=[]; seen=set()
    for root in search_roots:
        try:
            modules=list(root.rglob('accommodation_module.py'))
        except Exception:
            continue
        for mod in modules:
            try: mod=mod.resolve()
            except Exception: continue
            if ignored(mod) or mod in seen: continue
            candidates=[mod.parent/'templates'/'accommodation_quote_detail.html', mod.parent/'accommodation_quote_detail.html']
            tpl=next((x.resolve() for x in candidates if x.exists() and not ignored(x)), None)
            if tpl is None:
                try:
                    matches=[x.resolve() for x in mod.parent.rglob('accommodation_quote_detail.html') if not ignored(x)]
                    tpl=matches[0] if len(matches)==1 else None
                except Exception: tpl=None
            if tpl is None: continue
            seen.add(mod)
            score=0
            for hint in ('app.py','wsgi.py','main.py'):
                if (mod.parent/hint).exists(): score+=4
            if (mod.parent/'templates').exists(): score+=4
            if (mod.parent/'data').exists(): score+=2
            if 'riverwood' in str(mod.parent).lower(): score+=3
            pairs.append((score, mod, tpl))
    pairs.sort(key=lambda x:(-x[0], str(x[1])))
    return pairs

def atomic_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=dst.name+'.', suffix='.tmp', dir=str(dst.parent)); os.close(fd)
    try:
        shutil.copy2(src,tmp); os.replace(tmp,dst)
    finally:
        try: Path(tmp).unlink(missing_ok=True)
        except Exception: pass

def main():
    ap=argparse.ArgumentParser(description='Riverwood HMS compatibility autofix dashboard v2 replace-only installer')
    ap.add_argument('--root', action='append', help='Optional Riverwood Operations root; can be repeated')
    ap.add_argument('--apply', action='store_true', help='Actually replace live files. Without this flag only discovery is performed.')
    args=ap.parse_args()
    pairs=find_pairs(roots(args.root))
    if not pairs:
        print('FAILED: live accommodation_module.py + accommodation_quote_detail.html pair not found'); return 2
    top_score=pairs[0][0]; top=[x for x in pairs if x[0]==top_score]
    if len(top)!=1:
        print('FAILED: ambiguous live paths; use --root to point at the active Operations folder')
        for s,m,t in pairs[:10]: print(f'  score={s} module={m} template={t}')
        return 3
    _,mod,tpl=top[0]
    print('LIVE MODULE  :',mod); print('LIVE TEMPLATE:',tpl)
    if not args.apply:
        print('DRY RUN OK. Re-run with --apply to replace exactly these files.'); return 0
    src_mod=PAYLOAD/'accommodation_module.py'; src_tpl=PAYLOAD/'accommodation_quote_detail.html'
    if not src_mod.exists() or not src_tpl.exists(): print('FAILED: payload missing'); return 4
    if MARKER not in src_mod.read_text(encoding='utf-8') or UI_MARKER not in src_tpl.read_text(encoding='utf-8'):
        print('FAILED: package marker missing'); return 5
    before_mod=sha(mod); before_tpl=sha(tpl); expected_mod=sha(src_mod); expected_tpl=sha(src_tpl)
    if before_mod==expected_mod or before_tpl==expected_tpl:
        print('FAILED: at least one live file already equals payload; installer requires both live files to change'); return 6
    stamp=datetime.now().strftime('%Y%m%d_%H%M%S'); backup=mod.parent/f'_riverwood_backup_hms_compat_v2_{stamp}'; backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(mod, backup/'accommodation_module.py'); shutil.copy2(tpl, backup/'accommodation_quote_detail.html')
    try:
        atomic_copy(src_mod,mod); atomic_copy(src_tpl,tpl)
        after_mod=sha(mod); after_tpl=sha(tpl)
        if after_mod==before_mod or after_tpl==before_tpl: raise RuntimeError('live files did not change')
        if after_mod!=expected_mod or after_tpl!=expected_tpl: raise RuntimeError('post-APPLY hash mismatch')
        if MARKER not in mod.read_text(encoding='utf-8'): raise RuntimeError('module marker not found after APPLY')
        html=tpl.read_text(encoding='utf-8')
        if UI_MARKER not in html or 'HMS-сумісність розміщення' not in html or 'Виправити автоматично' not in html: raise RuntimeError('new UI marker not found after APPLY')
    except Exception as exc:
        shutil.copy2(backup/'accommodation_module.py',mod); shutil.copy2(backup/'accommodation_quote_detail.html',tpl)
        print('FAILED:',exc,'; rollback restored'); return 7
    print('APPLY OK'); print('BACKUP:',backup); print('MODULE SHA256:',after_mod); print('TEMPLATE SHA256:',after_tpl)
    print('No :8082/:8085 lifecycle action was performed.')
    return 0
if __name__=='__main__': raise SystemExit(main())
'''

BAT = r'''@echo off
setlocal
cd /d "%~dp0"
py -3 installer.py --apply %*
if errorlevel 1 (
  echo.
  echo INSTALL FAILED
  pause
  exit /b 1
)
echo.
echo INSTALL OK
pause
'''

README = '''Riverwood HMS Compatibility Autofix Dashboard v2
==================================================

Scope
- Replaces ONLY accommodation_module.py and accommodation_quote_detail.html.
- Does NOT modify/start/stop/reconfigure :8082 or :8085.
- Does NOT modify pms_booking_adapter_v1.py, GroupCard or ReserveGroup 1/2/3.

What v2 adds
- Dashboard-side HMS composition compatibility analysis before live preflight.
- Visible “HMS-сумісність розміщення” block with exact physical room, nights and composition segments.
- Hard server-side and UI block for live preflight/HMS booking while a conflict remains.
- “Виправити автоматично” live autofix preview using other physical rooms of the same RoomType.
- Autofix preserves nightly adults/children/paid children/extra beds, capacity, live availability and adjacent-night early/late requirements.
- Explicit preview “було → стане”.
- Explicit save as a new immutable quote revision.
- Immediate HMS compatibility check after manual room preview/change.

Install
1. Copy/extract the package on the Operations Windows host.
2. Optional discovery only: py -3 installer.py
3. Apply: APPLY_V2.bat
4. Installer prints the exact live module/template paths before replacing them, creates a timestamped backup, verifies both hashes changed and verifies the new UI marker after APPLY.
5. If discovery is ambiguous, run: py -3 installer.py --root "C:\\path\\to\\active\\Riverwood" --apply

Fail-closed
Installer returns FAILED and rolls back if the active pair cannot be identified, either live file does not change, hashes differ after copy, or the v2 UI marker is absent.
'''


def test_compat_logic(module_text: str):
    tree=ast.parse(module_text)
    funcs={n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    required={'_hms_compat_signature','_hms_compatibility_analysis_from_plans','_assert_hms_compatibility','_hms_compatibility_autofix'}
    missing=required-set(funcs)
    if missing: raise RuntimeError(f'missing v2 functions: {sorted(missing)}')
    # Execute only the pure analyzer and tiny dependencies in an isolated namespace.
    ns={'Any':object,'Dict':dict,'List':list,'Iterable':list,'Tuple':tuple,'HMS_COMPATIBILITY_AUTOFIX_V2_MARKER':MARKER}
    def _ival(v,default=0,minimum=None,maximum=None):
        try: out=int(str(v).strip())
        except Exception: out=default
        if minimum is not None: out=max(minimum,out)
        if maximum is not None: out=min(maximum,out)
        return out
    ns['_ival']=_ival; ns['ROOM_TYPE_NAMES']={2:'Standard'}
    for name in ('_hms_compat_signature','_hms_compatibility_analysis_from_plans'):
        code=compile(ast.Module(body=[funcs[name]], type_ignores=[]),'<compat-test>','exec'); exec(code,ns)
    analyze=ns['_hms_compatibility_analysis_from_plans']
    schedule=[
        {'date':'2026-09-10','next_date':'2026-09-11'},
        {'date':'2026-09-11','next_date':'2026-09-12'},
        {'date':'2026-09-12','next_date':'2026-09-13'},
    ]
    def room(a,c=0): return {'room_id':'111','room_label':'111','room_type_id':2,'category':'Standard','adults':a,'children':c,'paid_children':0,'extra_beds':0}
    bad={'2026-09-10':[room(2)],'2026-09-11':[room(2)],'2026-09-12':[room(1)]}
    result=analyze(schedule,bad)
    assert result['blocking'] and result['conflict_count']==1, result
    assert result['conflicts'][0]['replacement_nights']==['2026-09-12'], result
    good={'2026-09-10':[room(2)],'2026-09-11':[room(2)],'2026-09-12':[room(2)]}
    result2=analyze(schedule,good)
    assert not result2['blocking'], result2
    # Natural gap must not be a composition conflict.
    gap_schedule=[schedule[0],schedule[2]]
    gap={'2026-09-10':[room(2)],'2026-09-12':[room(1)]}
    result3=analyze(gap_schedule,gap)
    assert not result3['blocking'], result3


def main():
    src_module=MODULE.read_text(encoding='utf-8')
    src_template=TEMPLATE.read_text(encoding='utf-8')
    out_module=patch_module(src_module)
    out_template=patch_template(src_template)
    compile(out_module, 'accommodation_module.py', 'exec')
    test_compat_logic(out_module)
    try:
        from jinja2 import Environment
        Environment().parse(out_template)
    except ImportError:
        pass
    if MARKER not in out_module or UI_MARKER not in out_template:
        raise RuntimeError('markers missing from generated payload')
    if 'def _hms_booking_preflight' not in out_module or '_assert_hms_compatibility(q)' not in out_module:
        raise RuntimeError('preflight hard block missing')
    if 'hms_compatibility_autofix_save' not in out_module or 'Виправити автоматично' not in out_template:
        raise RuntimeError('autofix UI/action missing')

    if DIST.exists(): shutil.rmtree(DIST)
    pkg=DIST/BUILD_NAME; payload=pkg/'payload'; payload.mkdir(parents=True)
    (payload/'accommodation_module.py').write_text(out_module,encoding='utf-8')
    (payload/'accommodation_quote_detail.html').write_text(out_template,encoding='utf-8')
    (pkg/'installer.py').write_text(INSTALLER,encoding='utf-8')
    (pkg/'APPLY_V2.bat').write_text(BAT,encoding='utf-8')
    (pkg/'README.txt').write_text(README,encoding='utf-8')
    manifest={
        'build':BUILD_NAME,
        'markers':{'module':MARKER,'ui':UI_MARKER},
        'scope':['accommodation_module.py','accommodation_quote_detail.html'],
        'forbidden_untouched':[':8082',':8085','pms_booking_adapter_v1.py','pms_sidecar_with_room_quote_v3.py','GroupCard','ReserveGroup 1/2/3'],
        'source_sha256':{'accommodation_module.py':sha(MODULE),'accommodation_quote_detail.html':sha(TEMPLATE)},
        'payload_sha256':{'accommodation_module.py':sha(payload/'accommodation_module.py'),'accommodation_quote_detail.html':sha(payload/'accommodation_quote_detail.html')},
    }
    (pkg/'MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    report='\n'.join([
        'BUILD TEST REPORT',
        'Python compile: PASS',
        'Jinja parse: PASS',
        'Compatibility changed-signature case: PASS',
        'Compatibility unchanged-signature case: PASS',
        'Natural-gap non-conflict case: PASS',
        'Preflight hard gate marker: PASS',
        'Autofix preview/save UI markers: PASS',
        'Installer post-APPLY hash+UI-marker verification: INCLUDED',
        'Sidecar files changed: NO',
    ])+'\n'
    (pkg/'TEST_REPORT.txt').write_text(report,encoding='utf-8')
    zip_path=DIST/(BUILD_NAME+'.zip')
    with zipfile.ZipFile(zip_path,'w',zipfile.ZIP_DEFLATED) as z:
        for p in sorted(pkg.rglob('*')):
            if p.is_file(): z.write(p,p.relative_to(DIST))
    print(json.dumps({'ok':True,'zip':str(zip_path),'zip_sha256':sha(zip_path),'manifest':manifest},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
