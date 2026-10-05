#!/usr/bin/env python3
"""Validate all receiver kinds with a local amtool and synthetic mounted files."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--amtool',required=True)
    args = parser.parse_args(argv)
    spec = importlib.util.spec_from_file_location('routing_plugin',ROOT/'render_plugin.py')
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    model = json.loads((ROOT/'tests/fixtures/org.fixture.json').read_text())
    entries = []
    for kind in ['webhook','ntfy','slack','pagerduty','email']:
        entry = dict(name=kind,kind=kind,secret='receiver-'+kind,key='credential')
        if kind=='email':
            entry.update(dict(to='oncall@example.org',**{'from':'alerts@example.org'},
                              smarthost='smtp.example.org:587',auth_username='alerts'))
        entries.append(entry)
    model['org']['alerting']['receivers'] = entries
    model['org']['alerting']['default_receiver'] = 'webhook'
    model['org']['components']['monitoring'] = dict(team_receivers={'payments':'slack'})
    config,_ = plugin.build_config(model)
    with tempfile.TemporaryDirectory(prefix='alertmanager-offline-') as directory:
        work = Path(directory)
        for receiver in config['receivers']:
            for configs in receiver.values():
                if not isinstance(configs,list):
                    continue
                for settings in configs:
                    for field,value in list(settings.items()):
                        if not field.endswith('_file'):
                            continue
                        target = work/(receiver['name']+'-'+field)
                        target.write_text('https://notifications.example.org/alert' if 'url' in field else
                                          'synthetic-key',encoding='utf-8')
                        settings[field] = target.as_posix()
        path = work/'alertmanager.yaml'
        path.write_text(json.dumps(config,indent=2),encoding='utf-8',newline='\n')
        process = subprocess.run([args.amtool,'check-config',str(path)],capture_output=True,text=True)
        if process.returncode:
            print(process.stderr)
            return process.returncode
        print(process.stdout.strip())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
