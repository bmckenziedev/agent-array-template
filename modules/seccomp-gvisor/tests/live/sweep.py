#!/usr/bin/env python3
"""Run every x86_64 syscall number once with zero arguments in a forked child and print
'S <nr> <class>' (OK, an errno name, TIMEOUT or NORET(sigN)). It runs inside a probe pod
(gen_probe_pods.py case script `sweep`); compare_sweeps.py diffs the logs of two pods.

Why: a runtime proof that the filter runsc builds from the profile refuses the same syscalls as the filter
built from RuntimeDefault (expected difference: ptrace only). Skipped: rt_sigreturn, clone, fork, vfork,
exit, exit_group, reboot, clone3 (escape.py and probe.sh cover clone3). The pod is non-root with no
capabilities, so zero-argument calls cannot do harm; each call runs in its own forked child with a timeout.
"""
import ctypes
import errno
import os
import select
import signal

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
# skipped: rt_sigreturn, clone, fork, vfork, exit, reboot, exit_group, clone3 (the last four are
# covered by escape.py / dangerous with zero args)
SKIP = {15, 56, 57, 58, 60, 169, 231, 435}
for nr in range(0, 471):
    if nr in SKIP:
        print("S %d SKIP" % nr, flush=True)
        continue
    rfd, wfd = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(rfd)
        signal.alarm(2)
        ctypes.set_errno(0)
        r = libc.syscall(ctypes.c_long(nr), *[ctypes.c_long(0)] * 6)
        e = ctypes.get_errno()
        os.write(wfd, (("OK" if r >= 0 else errno.errorcode.get(e, str(e)))).encode())
        os._exit(0)
    os.close(wfd)
    cls = None
    if select.select([rfd], [], [], 5)[0]:
        data = os.read(rfd, 64)
        cls = data.decode() if data else None
    else:
        os.kill(pid, signal.SIGKILL)
        cls = "TIMEOUT"
    _, st = os.waitpid(pid, 0)
    os.close(rfd)
    if cls is None:
        cls = "NORET(sig%d)" % os.WTERMSIG(st) if os.WIFSIGNALED(st) else "NORET(exit%d)" % os.WEXITSTATUS(st)
    print("S %d %s" % (nr, cls), flush=True)
print("S DONE", flush=True)
