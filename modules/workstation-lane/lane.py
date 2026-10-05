#!/usr/bin/env python3
"""Window calculation and supervised volunteer lane. No admin kubeconfig is accepted."""
import argparse
import ctypes
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import time
from zoneinfo import ZoneInfo
from job import ProcessJob


def in_window(now, start, stop, drain_minutes=0):
    a = dt.time.fromisoformat(start)
    b = dt.time.fromisoformat(stop)
    if a == b or not 0 <= drain_minutes < 1440:
        raise ValueError('invalid window or drain duration')
    minute = now.hour * 60 + now.minute
    begin = a.hour * 60 + a.minute
    end = b.hour * 60 + b.minute
    length = (end - begin) % 1440
    if drain_minutes >= length:
        raise ValueError('drain duration consumes the window')
    return (minute - begin) % 1440 < length - drain_minutes


def local_now(zone):
    return dt.datetime.now(dt.timezone.utc).astimezone(ZoneInfo(zone))


def locked():
    if os.name != 'nt':
        raise RuntimeError('interactive Windows session required')
    user = ctypes.WinDLL('user32', use_last_error=True)
    user.OpenInputDesktop.restype = ctypes.c_void_p
    user.OpenInputDesktop.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    user.SwitchDesktop.argtypes = [ctypes.c_void_p]
    user.CloseDesktop.argtypes = [ctypes.c_void_p]
    desktop = user.OpenInputDesktop(0, False, 0x0100)
    if not desktop:
        return True
    try:
        return not bool(user.SwitchDesktop(desktop))
    finally:
        user.CloseDesktop(desktop)


def parent_alive(pid):
    if os.name != 'nt':
        return os.getppid() == pid
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.OpenProcess.argtypes = [ctypes.c_uint, ctypes.c_bool, ctypes.c_uint]
    kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_uint()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def transport(config, schedule, command):
    namespace = schedule['namespace']
    kubeconfig = config['kubeconfig']
    base = ['kubectl', '--kubeconfig', kubeconfig, '--namespace', namespace]
    who = json.loads(subprocess.check_output(base + ['auth', 'whoami', '-o', 'json']))
    expected = 'system:serviceaccount:' + namespace + ':' + config['service_account']
    if who.get('status', {}).get('userInfo', {}).get('username') != expected:
        raise PermissionError('device-specific service account required')
    # Reject credentials with broader rights even if the configured name matches.
    for verb, resource in [('get', 'secrets'), ('create', 'pods'), ('create', 'rolebindings')]:
        allowed = subprocess.run(base + ['auth', 'can-i', verb, resource], capture_output=True, text=True)
        if allowed.stdout.strip() != 'no':
            raise PermissionError('device credential has excessive privileges or check failed')
    return base + ['exec', config['factory_pod'], '--'] + list(command)


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    parser.add_argument('--schedule', required=True)
    parser.add_argument('--parent-pid', type=int, required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--yes', action='store_true')
    args = parser.parse_args(argv)
    config = json.loads(Path(args.config).read_text())
    schedule = json.loads(Path(args.schedule).read_text())
    ZoneInfo(schedule['timezone'])
    in_window(local_now(schedule['timezone']), schedule['window_start'], schedule['window_stop'], schedule['drain_minutes'])
    model = (Path(config['model_dir']) / config['model']).resolve()
    if Path(config['model_dir']).resolve() not in model.parents:
        raise ValueError('model must reside in model directory')
    if args.check or not args.yes:
        print('Lane configuration valid; --yes is required to start supervised processes')
        return 0
    state = Path(config['state_dir'])
    state.mkdir(parents=True, exist_ok=True)
    drain = state / 'drain.request'
    # Everything is a foreground child owned and reaped by this supervisor.
    while parent_alive(args.parent_pid):
        if locked() or not in_window(local_now(schedule['timezone']), schedule['window_start'], schedule['window_stop'], schedule['drain_minutes']):
            time.sleep(5)
            continue
        # The worker contract consumes a namespace-scoped kubectl command as JSON.
        pull = transport(config, schedule, ['factory-worker-pull', '--device', config['device_id']])
        drain.unlink(missing_ok=True)
        env = dict(os.environ, CUDA_VISIBLE_DEVICES=str(config['gpu']),
                   FACTORY_PULL_COMMAND_JSON=json.dumps(pull), FACTORY_DRAIN_FILE=str(drain),
                   KUBECTL_REMOTE_COMMAND_WEBSOCKETS='false')
        children = []
        job = ProcessJob()
        try:
            children.append(job.start([config['model_executable'], '--model', str(model),
                             '--host', '127.0.0.1', '--port', '8081'], env))
            children.append(job.start([config['worker_executable'], '--device', config['device_id']], env))
            while (parent_alive(args.parent_pid) and not locked()
                   and all(p.poll() is None for p in children)
                   and in_window(local_now(schedule['timezone']), schedule['window_start'], schedule['window_stop'])):
                if not in_window(local_now(schedule['timezone']), schedule['window_start'],
                                 schedule['window_stop'], schedule['drain_minutes']):
                    # Stop new leases, allowing current work to finish before window close.
                    drain.touch(exist_ok=True)
                time.sleep(2)
        finally:
            for child in reversed(children):
                stop(child)
            job.close()
            drain.unlink(missing_ok=True)
        time.sleep(5)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
