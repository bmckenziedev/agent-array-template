#!/usr/bin/env python3
"""Narrow restic backup. Exit 0 success/preview, 1 failure, 2 configuration error."""
import argparse
from contextlib import closing
import json
import hashlib
import os
from pathlib import Path
import sqlite3
import shutil
import subprocess
import tempfile
import yaml
from restore_check import validate_restore


def exclusions(env: dict[str, str], active: list[str]) -> list[str]:
    required = json.loads(env['BACKUP_EXCLUDE_JSON'])
    login = env['BACKUP_LOGIN_ROOT']
    if (not isinstance(required, list) or not required or
            not all(isinstance(p, str) and p.startswith('/') for p in required)):
        raise ValueError('exclusion policy must be a nonempty absolute path list')
    if login not in required or not login.startswith('/') or login == '/':
        raise ValueError('login root must be explicitly present in the policy')
    missing = set(required) - set(active)
    if missing:
        raise ValueError('required exclusion missing')
    return required


def restic_args(env: dict[str, str]) -> list[str]:
    return ['restic', '-o', 'sftp.command=ssh -F ' + env['BACKUP_SSH_CONFIG'] + ' backup-target -s sftp']


def main(argv=None) -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['run', 'check', 'restore-drill'])
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--exclude', action='append')
    args = parser.parse_args(argv)
    env = dict(os.environ)
    try:
        if env['BACKUP_KIND'] != 'restic-sftp':
            raise ValueError('backup kind is not restic-sftp')
        active = args.exclude if args.exclude is not None else json.loads(env['BACKUP_EXCLUDE_JSON'])
        exclusions(env, active)
        base = restic_args(env)
        env['RESTIC_REPOSITORY'] = 'sftp:backup-target:' + env['BACKUP_REPOSITORY_PATH']
        env.pop('RESTIC_PASSWORD', None)
        env.pop('RESTIC_PASSWORD_COMMAND', None)
        if args.action == 'check' and not args.yes:
            # Avoid repository lock writes and a local persistent cache in read-only mode.
            subprocess.run(base + ['--no-lock', '--no-cache', 'check', '--read-data-subset=5%'],
                           env=env, check=True)
            return 0
        if not args.yes:
            print('Validated plan:', args.action, '; explicit --yes required for writes')
            return 0
        # Never expand the allowlist to home directories or general PVC trees.
        stage = Path(env['BACKUP_STAGING_DIR'])
        login = Path(env['BACKUP_LOGIN_ROOT']).resolve()
        inputs = [stage, Path(env['BACKUP_SNAPSHOT_DIR']), Path(env['BACKUP_AUDIT_DIR'])]
        for path in inputs + [Path(env['BACKUP_GRAFANA_DB']), Path(env['BACKUP_SERVER_TOKEN_FILE']),
                              Path(env['BACKUP_ENCRYPTION_CONFIG'])]:
            resolved = path.resolve()
            if resolved == login or login in resolved.parents:
                raise ValueError('backup input resides in login root')
        if args.action == 'check':
            subprocess.run(base + ['check', '--read-data-subset=5%'], env=env, check=True)
            return 0
        if args.action == 'restore-drill':
            # mkdtemp is exclusive; restoration never targets a production directory.
            scratch = tempfile.mkdtemp(prefix='cluster-restore-')
            subprocess.run(base + ['restore', 'latest', '--target', scratch], env=env, check=True)
            validate_restore(Path(scratch), env['BACKUP_LOGIN_ROOT'],
                             env['BACKUP_SECRETS_ENCRYPTION_ENABLED'] == 'true')
            print('Restore verified in scratch directory:', scratch)
            return 0
        stage.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(stage, 0o700)
        with tempfile.TemporaryDirectory(prefix='run-', dir=stage) as work:
            work = Path(work)
            token = Path(env['BACKUP_SERVER_TOKEN_FILE'])
            if not token.is_file() or token.stat().st_size == 0:
                raise ValueError('cluster bootstrap token required for snapshot recovery')
            shutil.copyfile(token, work / 'cluster-server-token')
            encryption = Path(env['BACKUP_ENCRYPTION_CONFIG'])
            if env['BACKUP_SECRETS_ENCRYPTION_ENABLED'] == 'true' and not encryption.is_file():
                raise ValueError('encryption provider configuration required for snapshot recovery')
            if encryption.is_file():
                shutil.copyfile(encryption, work / 'encryption-provider-config.json')
                config_before = hashlib.sha256(encryption.read_bytes()).digest()
            else:
                config_before = None
            subprocess.run(['k3s', 'etcd-snapshot', 'save', '--dir', env['BACKUP_SNAPSHOT_DIR']], check=True)
            config_after = hashlib.sha256(encryption.read_bytes()).digest() if encryption.is_file() else None
            if config_after != config_before:
                raise ValueError('encryption configuration changed during snapshot; retry after rotation')
            key = work / 'sealed-controller-key.yaml'
            with key.open('wb') as output:
                subprocess.run(['kubectl', '-n', env['BACKUP_SEALED_NAMESPACE'], 'get', 'secrets',
                    '-l', 'sealedsecrets.bitnami.com/sealed-secrets-key', '-o', 'yaml'], stdout=output, check=True)
            if key.stat().st_size == 0:
                raise ValueError('empty controller key export')
            inventory = yaml.safe_load(key.read_text(encoding='utf-8'))
            if not inventory.get('items') or any(
                    not {'tls.key', 'tls.crt'} <= set(item.get('data', {}))
                    for item in inventory['items']):
                raise ValueError('controller keys missing')
            subprocess.run(['python3', str(Path(__file__).with_name('sealed-keys-fingerprints.py')),
                            str(key)], check=True, stdout=subprocess.DEVNULL)
            with closing(sqlite3.connect(Path(env['BACKUP_GRAFANA_DB']).resolve().as_uri() + '?mode=ro', uri=True)) as src:
                with closing(sqlite3.connect(work / 'grafana.db')) as dst:
                    src.backup(dst)
                    dst.commit()
            command = base + ['backup', '--one-file-system']
            for path in active:
                command += ['--exclude', path]
            command += [str(work), env['BACKUP_SNAPSHOT_DIR'], env['BACKUP_AUDIT_DIR']]
            subprocess.run(command, env=env, check=True)
        return 0
    except (KeyError, ValueError, TypeError, OSError, subprocess.CalledProcessError, sqlite3.Error, yaml.YAMLError) as error:
        reason = str(error) if isinstance(error, ValueError) else type(error).__name__
        print('Backup refused or failed:', reason)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
