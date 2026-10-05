#!/usr/bin/env bash
set -euo pipefail
rendered=${1:?Usage: promtool_tests.sh RENDERED}
org=${2:-org/org.yaml}
[[ -f "$org" ]] || org=org/org.example.yaml
promtool=${PROMTOOL:-promtool}
command -v "$promtool" >/dev/null || { echo "SKIP: promtool absent"; exit 0; }
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
python - "$rendered" "$tmp" "$promtool" "$org" <<'PY'
import pathlib, subprocess, sys, yaml
rendered, temporary = map(pathlib.Path, sys.argv[1:3])
promtool = sys.argv[3]
sys.path.insert(0, str(pathlib.Path('tools/render').resolve()))
from aa_render.model import load_model
from aa_render.templates import subst
model = load_model(pathlib.Path('.').resolve(), pathlib.Path(sys.argv[4]).resolve(), False)
rules = {}
for file in sorted((rendered / 'files').rglob('*.yaml')):
    if file.parent.name != 'rules':
        continue
    doc = yaml.safe_load(file.read_text(encoding='utf-8'))
    if not isinstance(doc, dict) or 'groups' not in doc or 'kind' in doc:
        raise SystemExit(f'{file}: expected plain rule groups')
    # Preserve rule directories so sibling tests have stable relative references.
    output = temporary / file.relative_to(rendered)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(file.read_text(encoding='utf-8'), encoding='utf-8')
    rules.setdefault(file.name, []).append(output)
    subprocess.run([promtool, 'check', 'rules', '--lint=all', '--lint-fatal', str(output)], check=True)
for index, test in enumerate(sorted(pathlib.Path('.').rglob('*.test.yaml'))):
    if test.parent.name != 'tests' or any(part in {'.git', 'rendered', '.ci-venvs'} for part in test.parts):
        continue
    # Test expressions use the same configured metrics/thresholds as rule producers.
    doc = yaml.safe_load(subst(test.read_text(encoding='utf-8'), model['keys']))
    resolved = []
    for reference in doc.get('rule_files', []):
        name = pathlib.Path(reference).name
        matches = [output for filename, outputs in rules.items()
                   if pathlib.PurePath(filename).match(name) for output in outputs]
        # Optional module rule suites run only when their rule producers render.
        gated = {'factory.rules.yaml', 'gpu.rules.yaml', 'ci.rules.yaml', 'hostwatch.rules.yaml'}
        if not matches and name in gated:
            print(f'SKIP {test}: optional rule producer disabled')
            break
        if not matches:
            raise SystemExit(f'{test}: missing rendered rule {name}')
        # A source can also publish the same groups for a chart; do not double-run it.
        resolved.append(str(matches[0].resolve()))
    else:
        import os
        output = pathlib.Path(resolved[0]).parent.parent / 'tests' / ('test-' + str(index) + '.yaml')
        doc['rule_files'] = [os.path.relpath(file, output.parent) for file in resolved]
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(yaml.safe_dump(doc), encoding='utf-8')
        subprocess.run([promtool, 'test', 'rules', str(output)], check=True)
print(f'promtool: {sum(map(len, rules.values()))} rendered rules checked')
PY
