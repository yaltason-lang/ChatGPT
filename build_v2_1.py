from __future__ import annotations

import tempfile
from pathlib import Path

import build_v2 as base

base.BUILD_NAME = 'Riverwood_5SEP_HMS_COMPATIBILITY_AUTOFIX_DASHBOARD_v2_1_INSTALLER_FIX'

base.INSTALLER = r'''from __future__ import annotations
import argparse, hashlib, os, shutil, tempfile
from datetime import datetime
from pathlib import Path

MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2'
UI_MARKER = 'RIVERWOOD_HMS_COMPAT_AUTOFIX_V2_UI'
HERE = Path(__file__).resolve().parent
PAYLOAD = HERE / 'payload'
KNOWN_LIVE_ROOT = Path(r'C:\Riverwood_Operations_MVP0_Core_Employees')


def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def historical(p: Path) -> bool:
    parts = [str(x).strip().lower() for x in Path(p).parts]
    for part in parts:
        if part in ('all versions', '_backups', 'backups', 'backup', 'archive', 'archives'):
            return True
        if part.startswith('before_') or part.startswith('_backup'):
            return True
        if part.startswith('baseline') or part.endswith('_baseline'):
            return True
    low = str(p).lower().replace('/', '\\')
    return any(x in low for x in ('\\dist\\', '\\payload\\', '\\.git\\'))


def direct_pair(root: Path):
    try: root = root.resolve()
    except Exception: return None
    mod = root / 'accommodation_module.py'
    candidates = [root / 'templates' / 'accommodation_quote_detail.html', root / 'accommodation_quote_detail.html']
    tpl = next((x for x in candidates if x.exists()), None)
    if mod.exists() and tpl is not None:
        return mod.resolve(), tpl.resolve()
    return None


def roots(explicit):
    out = []
    for raw in explicit or []:
        p = Path(raw).expanduser()
        if p.exists(): out.append(p.resolve())
    env = os.environ.get('RIVERWOOD_OPERATIONS_ROOT', '').strip()
    if env and Path(env).exists(): out.append(Path(env).resolve())
    if KNOWN_LIVE_ROOT.exists(): out.append(KNOWN_LIVE_ROOT.resolve())
    cwd = Path.cwd().resolve(); out.extend([cwd, *list(cwd.parents)[:3]])
    for raw in (r'C:\Riverwood', r'C:\RiverwoodOperations', r'C:\Riverwood_Operations', r'C:\Apps', r'D:\Riverwood', r'D:\RiverwoodOperations'):
        p = Path(raw)
        if p.exists(): out.append(p.resolve())
    seen = []
    for p in out:
        if p not in seen: seen.append(p)
    return seen


def score_pair(mod: Path, *, direct=False):
    score = 100 if direct else 0
    parent = mod.parent
    for hint in ('app.py', 'wsgi.py', 'main.py'):
        if (parent / hint).exists(): score += 4
    if (parent / 'templates').exists(): score += 4
    if (parent / 'data').exists(): score += 2
    if 'riverwood' in str(parent).lower(): score += 3
    if not historical(parent): score += 30
    else: score -= 200
    if parent == KNOWN_LIVE_ROOT:
        score += 1000
    return score


def find_pairs(search_roots, exact_root=False):
    pairs = []; seen = set()
    for root in search_roots:
        pair = direct_pair(root)
        if pair:
            mod, tpl = pair
            if mod not in seen:
                seen.add(mod); pairs.append((score_pair(mod, direct=True), mod, tpl))
            # A root that itself contains the live pair is authoritative. Do not walk
            # its All versions/backups tree and create artificial ambiguity.
            continue
        if exact_root:
            continue
        try:
            modules = list(root.rglob('accommodation_module.py'))
        except Exception:
            continue
        for mod in modules:
            try: mod = mod.resolve()
            except Exception: continue
            if historical(mod) or mod in seen: continue
            candidates = [mod.parent / 'templates' / 'accommodation_quote_detail.html', mod.parent / 'accommodation_quote_detail.html']
            tpl = next((x.resolve() for x in candidates if x.exists() and not historical(x)), None)
            if tpl is None:
                try:
                    matches = [x.resolve() for x in mod.parent.rglob('accommodation_quote_detail.html') if not historical(x)]
                    tpl = matches[0] if len(matches) == 1 else None
                except Exception: tpl = None
            if tpl is None: continue
            seen.add(mod); pairs.append((score_pair(mod, direct=False), mod, tpl))
    pairs.sort(key=lambda x: (-x[0], str(x[1])))
    return pairs


def atomic_copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=dst.name + '.', suffix='.tmp', dir=str(dst.parent)); os.close(fd)
    try:
        shutil.copy2(src, tmp); os.replace(tmp, dst)
    finally:
        try: Path(tmp).unlink(missing_ok=True)
        except Exception: pass


def main():
    ap = argparse.ArgumentParser(description='Riverwood HMS compatibility autofix dashboard v2.1 replace-only installer')
    ap.add_argument('--root', action='append', help='Optional exact Riverwood Operations root; can be repeated')
    ap.add_argument('--exact-root', action='store_true', help='Do not recurse below supplied roots')
    ap.add_argument('--apply', action='store_true', help='Actually replace live files. Without this flag only discovery is performed.')
    args = ap.parse_args()
    search_roots = roots(args.root)
    pairs = find_pairs(search_roots, exact_root=args.exact_root)
    if not pairs:
        print('FAILED: live accommodation_module.py + accommodation_quote_detail.html pair not found'); return 2
    top_score = pairs[0][0]; top = [x for x in pairs if x[0] == top_score]
    if len(top) != 1:
        print('FAILED: ambiguous live paths after archive/backups filtering')
        for s, m, t in pairs[:10]: print(f'  score={s} module={m} template={t}')
        return 3
    _, mod, tpl = top[0]
    print('LIVE MODULE  :', mod); print('LIVE TEMPLATE:', tpl)
    if historical(mod.parent):
        print('FAILED: selected path is historical/archive/backup; refusing APPLY'); return 8
    if not args.apply:
        print('DRY RUN OK. Re-run with --apply to replace exactly these files.'); return 0
    src_mod = PAYLOAD / 'accommodation_module.py'; src_tpl = PAYLOAD / 'accommodation_quote_detail.html'
    if not src_mod.exists() or not src_tpl.exists(): print('FAILED: payload missing'); return 4
    if MARKER not in src_mod.read_text(encoding='utf-8') or UI_MARKER not in src_tpl.read_text(encoding='utf-8'):
        print('FAILED: package marker missing'); return 5
    before_mod = sha(mod); before_tpl = sha(tpl); expected_mod = sha(src_mod); expected_tpl = sha(src_tpl)
    if before_mod == expected_mod and before_tpl == expected_tpl:
        print('ALREADY APPLIED: both live files already match this payload'); return 0
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S'); backup = mod.parent / f'_riverwood_backup_hms_compat_v2_1_{stamp}'; backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(mod, backup / 'accommodation_module.py'); shutil.copy2(tpl, backup / 'accommodation_quote_detail.html')
    try:
        atomic_copy(src_mod, mod); atomic_copy(src_tpl, tpl)
        after_mod = sha(mod); after_tpl = sha(tpl)
        if after_mod != expected_mod or after_tpl != expected_tpl: raise RuntimeError('post-APPLY hash mismatch')
        if MARKER not in mod.read_text(encoding='utf-8'): raise RuntimeError('module marker not found after APPLY')
        html = tpl.read_text(encoding='utf-8')
        if UI_MARKER not in html or 'HMS-сумісність розміщення' not in html or 'Виправити автоматично' not in html:
            raise RuntimeError('new UI marker not found after APPLY')
    except Exception as exc:
        shutil.copy2(backup / 'accommodation_module.py', mod); shutil.copy2(backup / 'accommodation_quote_detail.html', tpl)
        print('FAILED:', exc, '; rollback restored'); return 7
    print('APPLY OK'); print('BACKUP:', backup); print('MODULE SHA256:', after_mod); print('TEMPLATE SHA256:', after_tpl)
    print('No :8082/:8085 lifecycle action was performed.')
    return 0

if __name__ == '__main__': raise SystemExit(main())
'''

base.BAT = r'''@echo off
setlocal
cd /d "%~dp0"
if exist "C:\Riverwood_Operations_MVP0_Core_Employees\accommodation_module.py" if exist "C:\Riverwood_Operations_MVP0_Core_Employees\templates\accommodation_quote_detail.html" (
  echo Using confirmed Operations root: C:\Riverwood_Operations_MVP0_Core_Employees
  py -3 installer.py --root "C:\Riverwood_Operations_MVP0_Core_Employees" --exact-root --apply
) else (
  py -3 installer.py --apply %*
)
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

base.README = '''Riverwood HMS Compatibility Autofix Dashboard v2.1 — installer-path hotfix
=======================================================================

Why v2.1 exists
- v2 correctly failed closed, but its recursive discovery gave the same score to the active Operations root and an archived copy under “All versions”.
- v2.1 fixes ONLY installer path selection; the verified HMS compatibility/autofix payload is unchanged.

Confirmed Operations root for this deployment
C:\\Riverwood_Operations_MVP0_Core_Employees

APPLY_V2.bat behavior
- If the confirmed root exists, it uses that root in exact-root mode and does NOT recurse into “All versions” or backups.
- Generic discovery ignores All versions, _backups, before_*, baseline*, dist, payload and .git paths.
- It refuses to APPLY to a historical/archive/backup path.
- It backs up the two live files, atomically replaces them, then verifies exact payload hashes and UI/module markers.

Scope remains unchanged
- Replaces ONLY accommodation_module.py and templates\\accommodation_quote_detail.html.
- Does NOT modify/start/stop/reconfigure :8082 or :8085.
- Does NOT modify pms_booking_adapter_v1.py, GroupCard or ReserveGroup 1/2/3.
'''


def test_installer_discovery() -> None:
    ns = {'__name__': 'installer_test', '__file__': str(Path(tempfile.gettempdir()) / 'installer.py')}
    exec(compile(base.INSTALLER, '<installer-test>', 'exec'), ns)
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / 'Riverwood_Operations_MVP0_Core_Employees'
        (root / 'templates').mkdir(parents=True)
        (root / 'accommodation_module.py').write_text('live', encoding='utf-8')
        (root / 'templates' / 'accommodation_quote_detail.html').write_text('live', encoding='utf-8')
        archive = root / 'All versions' / '4.09.2026'
        (archive / 'templates').mkdir(parents=True)
        (archive / 'accommodation_module.py').write_text('archive', encoding='utf-8')
        (archive / 'templates' / 'accommodation_quote_detail.html').write_text('archive', encoding='utf-8')
        backup = root / '_backups' / 'before_v5.327'
        (backup / 'templates').mkdir(parents=True)
        (backup / 'accommodation_module.py').write_text('backup', encoding='utf-8')
        (backup / 'templates' / 'accommodation_quote_detail.html').write_text('backup', encoding='utf-8')
        pairs = ns['find_pairs']([root], exact_root=False)
        assert pairs, 'no installer pair found'
        top_score = pairs[0][0]
        top = [x for x in pairs if x[0] == top_score]
        assert len(top) == 1, pairs
        assert top[0][1] == (root / 'accommodation_module.py').resolve(), pairs
        exact = ns['find_pairs']([root], exact_root=True)
        assert len(exact) == 1 and exact[0][1] == (root / 'accommodation_module.py').resolve(), exact


def main() -> None:
    compile(base.INSTALLER, 'installer.py', 'exec')
    test_installer_discovery()
    base.main()


if __name__ == '__main__':
    main()
