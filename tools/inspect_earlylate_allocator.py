from __future__ import annotations

import ast
import hashlib
from pathlib import Path

SRC = Path('accommodation_module.py')
OUT = Path('EARLYLATE_INSPECTION.txt')
text = SRC.read_text(encoding='utf-8-sig')
lines = text.splitlines()
sha = hashlib.sha256(SRC.read_bytes()).hexdigest()
tree = ast.parse(text)

wanted_exact = {
    '_stay_time_availability_for_plans',
    '_available_room_tokens_for_period',
    '_saved_daily_room_plans',
    '_hms_booking_payload',
    '_hms_booking_state',
}
keywords = ('alloc', 'avail', 'room_plan', 'calculator', 'quote_detail', 'hms_booking')

rows = []
rows.append(f'SHA256={sha}')
rows.append(f'LINES={len(lines)}')
rows.append('')

funcs = []
for node in ast.walk(tree):
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        funcs.append(node)
funcs.sort(key=lambda n: n.lineno)

rows.append('FUNCTION INDEX:')
for n in funcs:
    name = n.name
    if name in wanted_exact or any(k in name.lower() for k in keywords):
        rows.append(f'{name}: {n.lineno}-{getattr(n, "end_lineno", n.lineno)}')
rows.append('')

needles = [
    '_stay_time_availability_for_plans(',
    '_available_room_tokens_for_period(',
    'early_checkin',
    'late_checkout',
    'day_results',
    'room_plan',
    "detail_action == 'hms_booking_preflight'",
]
rows.append('MATCH LINES:')
for i, line in enumerate(lines, 1):
    if any(x in line for x in needles):
        rows.append(f'{i}: {line}')
rows.append('')

rows.append('RELEVANT FUNCTION SOURCES:')
for n in funcs:
    src = '\n'.join(lines[n.lineno-1:getattr(n, 'end_lineno', n.lineno)])
    if n.name in wanted_exact or '_stay_time_availability_for_plans(' in src or '_available_room_tokens_for_period(' in src:
        rows.append('\n' + '='*90)
        rows.append(f'FUNCTION {n.name} {n.lineno}-{getattr(n, "end_lineno", n.lineno)}')
        rows.append('='*90)
        rows.append(src)

OUT.write_text('\n'.join(rows) + '\n', encoding='utf-8')
print('\n'.join(rows[:250]))
print(f'WROTE={OUT}')
