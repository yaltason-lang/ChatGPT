from __future__ import annotations

import argparse
import ast
import hashlib
import shutil
from datetime import datetime
from pathlib import Path

MARKER = '# RIVERWOOD_HMS_WRITER_V10_RUNTIME_PREFLIGHT_GUARD_V1'
PREREQ_MARKER = '# RIVERWOOD_EARLYLATE_ADJACENT_DAY_ALLOC_V1'
EXPECTED_WRITER_SHA256 = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
WRITER_MARKER = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'
DEFAULT_TARGET = Path(r'C:\Riverwood_Operations_MVP0_Core_Employees\accommodation_module.py')

HELPER = r'''
# RIVERWOOD_HMS_WRITER_V10_RUNTIME_PREFLIGHT_GUARD_V1
# Preflight must prove that the exact v10 writer source is on disk AND that the
# :8085 listener process started after that source was installed. This prevents
# Operations from showing READY while a stale pre-v10 writer is still resident.
def _hms_writer_v10_runtime_guard() -> Dict[str, Any]:
    writer_path = Path(os.environ.get(
        'HMS_BOOKING_WRITER_SOURCE',
        r'C:\riverwood_revenue_bot\pms_booking_adapter_v5328.py',
    ))
    expected_sha = '23462d24847e677e6fedaa1bf4cbe863899341d536122a3140a7408abb5926ac'
    required_marker = 'RIVERWOOD_HMS_WRITER_V10_TECHNICAL_CARD_BUDGET_RESTORE_GATE'
    out: Dict[str, Any] = {
        'ok': False,
        'writer_path': str(writer_path),
        'expected_sha256': expected_sha,
        'actual_sha256': '',
        'listener_port': 8085,
        'listener_pid': 0,
        'process_start_epoch': 0,
        'source_mtime_epoch': 0,
        'message': '',
    }
    try:
        if not writer_path.is_file():
            out['message'] = f'Booking writer source не знайдено: {writer_path}'
            return out
        raw = writer_path.read_bytes()
        actual_sha = hashlib.sha256(raw).hexdigest()
        out['actual_sha256'] = actual_sha
        out['source_mtime_epoch'] = int(writer_path.stat().st_mtime)
        if actual_sha != expected_sha:
            out['message'] = (
                'HMS writer на диску не є перевіреним v10 card-budget build. '
                f'Очікується SHA {expected_sha[:12]}…, фактичний {actual_sha[:12]}…. '
                'Бронювання заблоковане до виправлення writer.'
            )
            return out
        text = raw.decode('utf-8-sig', errors='replace')
        if required_marker not in text:
            out['message'] = 'HMS writer має v10 SHA-check mismatch marker: technical card-budget marker відсутній.'
            return out

        # Prove the actual listener process, not merely the source file on disk.
        ps = (
            "$c=Get-NetTCPConnection -LocalPort 8085 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; "
            "if(-not $c){exit 7}; "
            "$p=Get-Process -Id $c.OwningProcess -ErrorAction Stop; "
            "$s=[DateTimeOffset]$p.StartTime.ToUniversalTime(); "
            "Write-Output ($p.Id.ToString()+'|'+$s.ToUnixTimeSeconds().ToString())"
        )
        proc = subprocess.run(
            ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', ps],
            capture_output=True, text=True, timeout=5,
        )
        if proc.returncode != 0 or '|' not in (proc.stdout or ''):
            out['message'] = 'HMS writer :8085 не має підтвердженого LISTEN process. Бронювання заблоковане.'
            return out
        line = (proc.stdout or '').strip().splitlines()[-1].strip()
        pid_text, start_text = line.split('|', 1)
        pid = int(pid_text)
        started = int(start_text)
        out['listener_pid'] = pid
        out['process_start_epoch'] = started
        # 2 seconds tolerance for filesystem timestamp granularity.
        if started + 2 < int(out['source_mtime_epoch'] or 0):
            out['message'] = (
                f'HMS writer :8085 PID {pid} запущений ДО встановлення v10 source. '
                'Це stale runtime; перезапустіть writer :8085. Бронювання заблоковане до рестарту.'
            )
            return out
        try:
            with socket.create_connection(('127.0.0.1', 8085), timeout=1.0):
                pass
        except Exception as exc:
            out['message'] = f'HMS writer :8085 не приймає TCP-з’єднання: {exc}'
            return out

        out['ok'] = True
        out['message'] = f'Writer v10 runtime підтверджено: PID {pid}, SHA {actual_sha[:12]}…'
        return out
    except Exception as exc:
        out['message'] = f'Не вдалося підтвердити HMS writer v10 runtime: {exc}'
        return out
'''.strip('\n')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def function_nodes(text: str, name: str):
    tree = ast.parse(text)
    return [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]


def patch_text(source: str) -> str:
    text = source.replace('\r\n', '\n').replace('\r', '\n')
    if MARKER in text:
        verify_text(text)
        return text
    if PREREQ_MARKER not in text:
        raise RuntimeError('Early/Late Adjacent Day Allocation V1 is not installed; refusing wrong baseline.')
    if len(function_nodes(text, '_hms_booking_preflight')) != 1:
        raise RuntimeError('Expected exactly one _hms_booking_preflight.')

    if 'import subprocess\n' not in text:
        if text.count('import socket\n') != 1:
            raise RuntimeError('Cannot find unique import socket anchor.')
        text = text.replace('import socket\n', 'import socket\nimport subprocess\n', 1)

    helper_anchor = '\ndef _hms_booking_preflight(row: Any, timetable: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n'
    if text.count(helper_anchor) != 1:
        raise RuntimeError('Cannot find unique _hms_booking_preflight insertion anchor.')
    text = text.replace(helper_anchor, '\n\n' + HELPER + '\n\n\ndef _hms_booking_preflight(row: Any, timetable: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:\n', 1)

    ready_old = '    ready = not conflicts\n    warnings: List[str] = []\n'
    ready_new = (
        '    writer_runtime = _hms_writer_v10_runtime_guard()\n'
        '    if not writer_runtime.get(\'ok\'):\n'
        '        conflicts.append({\n'
        "            'type': 'writer_runtime_not_ready',\n"
        "            'message': str(writer_runtime.get('message') or 'HMS writer runtime не підтверджено.'),\n"
        '        })\n'
        '    ready = not conflicts\n'
        '    warnings: List[str] = []\n'
    )
    if text.count(ready_old) != 1:
        raise RuntimeError(f'Cannot find unique preflight ready anchor; found {text.count(ready_old)}.')
    text = text.replace(ready_old, ready_new, 1)

    final_old = (
        "        'status': 'ready' if ready else 'blocked',\n"
        "        'booking_write_enabled': True,\n"
        "        'booking_write_reason': (\n"
        "            'Live preflight готовий. Dedicated writer :8085 виконує fail-closed транзакцію: Reservation POST → GroupCard → точні RoomID → ValidateRoom → ReserveGroup 1/2/3.'\n"
        "        ),\n"
    )
    final_new = (
        "        'status': 'ready' if ready else 'blocked',\n"
        "        'booking_write_enabled': bool(writer_runtime.get('ok')),\n"
        "        'booking_write_reason': (\n"
        "            str(writer_runtime.get('message') or '') if not writer_runtime.get('ok') else\n"
        "            'Live preflight готовий. Writer v10 runtime підтверджено; exact RoomID → ValidateRoom → ReserveGroup 1/2/3.'\n"
        "        ),\n"
        "        'writer_runtime': writer_runtime,\n"
    )
    if text.count(final_old) != 1:
        raise RuntimeError(f'Cannot find unique final booking_write block; found {text.count(final_old)}.')
    text = text.replace(final_old, final_new, 1)

    verify_text(text)
    return text


def verify_text(text: str) -> None:
    compile(text, '<patched accommodation_module.py>', 'exec')
    tree = ast.parse(text)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    if '_hms_writer_v10_runtime_guard' not in funcs:
        raise RuntimeError('Missing writer runtime guard helper.')
    if '_hms_booking_preflight' not in funcs:
        raise RuntimeError('Missing preflight after patch.')
    src = ast.get_source_segment(text, funcs['_hms_booking_preflight']) or ''
    for needle in (
        '_hms_writer_v10_runtime_guard()',
        "'type': 'writer_runtime_not_ready'",
        "'booking_write_enabled': bool(writer_runtime.get('ok'))",
        "'writer_runtime': writer_runtime",
    ):
        if needle not in src:
            raise RuntimeError(f'Preflight guard missing required wire: {needle}')
    if PREREQ_MARKER not in text:
        raise RuntimeError('Adjacent-day allocator marker lost.')
    if "'paid_children': bool" in text:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', default=str(DEFAULT_TARGET))
    parser.add_argument('--out', default='')
    parser.add_argument('--no-backup', action='store_true')
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_file():
        print(f'FAILED: Operations source not found: {source}')
        return 2
    old_sha = sha256(source)
    raw = source.read_bytes()
    newline = '\r\n' if b'\r\n' in raw else '\n'
    try:
        original = raw.decode('utf-8-sig')
        patched = patch_text(original)
    except Exception as exc:
        print(f'FAILED: {exc}')
        return 3
    out = Path(args.out) if args.out else source
    if out == source and not args.no_backup:
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = Path(r'C:\riverwood_revenue_bot\Old VERSIONS') / f'before_WRITER_V10_RUNTIME_GUARD_{stamp}'
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, backup_dir / source.name)
        print(f'Backup: {backup_dir / source.name}')
    payload = patched.replace('\n', newline).encode('utf-8')
    tmp = out.with_suffix(out.suffix + '.tmp')
    tmp.write_bytes(payload)
    tmp.replace(out)
    print(f'Operations old SHA256: {old_sha}')
    print(f'Operations new SHA256: {sha256(out)}')
    print('VERIFY OK: writer v10 runtime gate is wired into live HMS preflight.')
    print('APPLY OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
