import json
import yaml
from pathlib import Path

# Читаю sources.yaml
with open('sources.yaml') as f:
    data = yaml.safe_load(f)

# Читаю check_report.json
with open('logs/check_report.json') as f:
    report = json.load(f)

# Формирую словарь мёртвых источников
dead = {}
for r in report['results']:
    if r.get('verdict') in ('DEAD', 'BROKEN', 'STALE'):
        dead[r['name']] = r['verdict']

# Обновляю sources
disabled_count = 0
for source in data['sources']:
    if source['name'] in dead:
        source['enabled'] = False
        source['check_status'] = dead[source['name']]
        disabled_count += 1

# Обновляю meta
data['meta']['enabled'] = sum(1 for s in data['sources'] if s.get('enabled', False))
data['meta']['disabled'] = sum(1 for s in data['sources'] if not s.get('enabled', False))
data['meta']['last_check'] = '2026-06-09'

# Сохраняю
with open('sources.yaml', 'w') as f:
    yaml.dump(data, f, allow_unicode=True, sort_keys=False)

print(f'Disabled: {disabled_count} sources')
print(f"Enabled: {data['meta']['enabled']}, Disabled: {data['meta']['disabled']}")
