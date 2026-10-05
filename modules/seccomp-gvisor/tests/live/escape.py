#!/usr/bin/env python3
"""Adversarial probe for the agent-array Localhost seccomp profile (gen_probe_pods.py case script `escape`).

Prints 'E <name>: <result>' lines: clone3/clone/unshare with every CLONE_NEW* flag, setns, the mount family,
ptrace in every form, a process's OWN filter (ALLOW, LOG, TRACE, USER_NOTIF, unknown verdicts) stacked on
the pod's, the USER_NOTIF listener attack (a supervisor answering CONTINUE), seccomp(2) flag combinations,
socket domains and personality(). Read the lines against README.md ("Seccomp listener filters"). Every call that
could change the caller (unshare, TRACEME, installing a filter) runs in a forked child.
"""
import array
import ctypes
import errno
import fcntl
import os
import select
import signal
import socket
import struct

libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long


def sysc(nr, *args):
    cargs = [ctypes.c_long(a) if isinstance(a, int) else a for a in args]
    ctypes.set_errno(0)
    r = libc.syscall(ctypes.c_long(nr), *cargs)
    return r, ctypes.get_errno()


def en(e):
    return errno.errorcode.get(e, str(e))


def E(name, msg):
    print("E %s: %s" % (name, msg), flush=True)


NR_CLONE, NR_UNSHARE, NR_SETNS, NR_PTRACE, NR_SECCOMP, NR_PRCTL, NR_CLONE3 = 56, 272, 308, 101, 317, 157, 435
FLAGS = {"NEWNS": 0x00020000, "NEWCGROUP": 0x02000000, "NEWUTS": 0x04000000, "NEWIPC": 0x08000000,
         "NEWUSER": 0x10000000, "NEWPID": 0x20000000, "NEWNET": 0x40000000, "NEWTIME": 0x00000080}
SIGCHLD = 17


class CloneArgs(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in
                ("flags", "pidfd", "child_tid", "parent_tid", "exit_signal", "stack", "stack_size", "tls")]


def clone3(flags):
    a = CloneArgs()
    a.flags = flags
    a.exit_signal = SIGCHLD
    r, e = sysc(NR_CLONE3, ctypes.byref(a), ctypes.sizeof(a))
    if r == 0:
        os._exit(0)  # we are the new child: leave at once
    if r > 0:
        os.waitpid(r, 0)
        return "CREATED child"
    return "refused %s" % en(e)


def status_line():
    try:
        with open("/proc/self/status") as f:
            keep = [l.strip() for l in f if l.startswith(("Seccomp", "NoNewPrivs", "CapEff", "Uid"))]
        return " | ".join(keep)
    except Exception as ex:  # noqa: BLE001
        return "status unreadable: %s" % ex


E("proc_status", status_line())

# --- 1. clone3 in every shape: always the same answer, never a child in a new namespace
E("clone3_null", "ret=%d errno=%s" % ((lambda r: (r[0], en(r[1])))(sysc(NR_CLONE3, 0, 0))))
E("clone3_plain_fork_flags0", clone3(0))
for n, fl in FLAGS.items():
    E("clone3_" + n, clone3(fl))
E("clone3_ALLNS", clone3(sum(v for k, v in FLAGS.items() if k != "NEWTIME")))

# --- 2. clone (the glibc fallback) with each namespace flag
for n, fl in FLAGS.items():
    if n == "NEWTIME":
        continue
    r, e = sysc(NR_CLONE, fl | SIGCHLD, 0, 0, 0, 0)
    if r == 0:
        os._exit(0)
    if r > 0:
        os.waitpid(r, 0)
        E("clone_" + n, "CREATED child")
    else:
        E("clone_" + n, "refused %s" % en(e))
r, e = sysc(NR_CLONE, SIGCHLD, 0, 0, 0, 0)
if r == 0:
    os._exit(0)
if r > 0:
    os.waitpid(r, 0)
    E("clone_plain_fork", "OK")
else:
    E("clone_plain_fork", "FAIL %s" % en(e))

# --- 3. unshare / setns / mount family
for n, fl in FLAGS.items():
    _pid = os.fork()                  # an unshare that succeeds changes the caller: keep it in a throwaway child
    if _pid == 0:
        r, e = sysc(NR_UNSHARE, fl)
        E("unshare_" + n, "allowed" if r == 0 else "refused %s" % en(e))
        os._exit(0)
    os.waitpid(_pid, 0)
r, e = sysc(NR_SETNS, -1, 0)
E("setns", "ret=%d %s" % (r, en(e)))
for nm, nr, args in (("mount", 165, (0, 0, 0, 0, 0)), ("umount2", 166, (0, 0)), ("pivot_root", 155, (0, 0)),
                     ("chroot", 161, (0,)), ("open_by_handle_at", 304, (0, 0, 0)), ("fsopen", 430, (0, 0)),
                     ("move_mount", 429, (0, 0, 0, 0, 0)), ("open_tree", 428, (0, 0, 0)),
                     ("bpf", 321, (0, 0, 0)), ("keyctl", 250, (0, 0, 0, 0, 0)), ("add_key", 248, (0, 0, 0, 0, 0)),
                     ("userfaultfd", 323, (0,)), ("perf_event_open", 298, (0, 0, 0, 0, 0)),
                     ("io_uring_setup", 425, (0, 0)), ("pidfd_getfd", 438, (0, 0, 0)), ("kcmp", 312, (0, 0, 0, 0, 0)),
                     ("init_module", 175, (0, 0, 0)), ("process_madvise", 440, (0, 0, 0, 0, 0))):
    r, e = sysc(nr, *args)
    E("sys_" + nm, "ret=%d %s" % (r, en(e)))

# --- 4. ptrace in every form
for nm, req, pid in (("ATTACH_pid1", 16, 1), ("SEIZE_pid1", 0x4206, 1), ("PEEKTEXT_self", 1, os.getpid()),
                     ("ATTACH_self", 16, os.getpid())):
    r, e = sysc(NR_PTRACE, req, pid, 0, 0)
    E("ptrace_" + nm, "ret=%d %s" % (r, en(e)))
_pid = os.fork()                      # TRACEME makes the caller a tracee of its parent: do it in a throwaway child
if _pid == 0:
    r, e = sysc(NR_PTRACE, 0, 0, 0, 0)
    E("ptrace_TRACEME", "ret=%d %s" % (r, en(e)))
    os._exit(0)
os.waitpid(_pid, 0)

# --- 5. seccomp-stacking attacks: can a process's OWN filter beat the pod's TRACE verdict on clone3?
SOCK_FILTER = struct.Struct("HBBI")


def make_prog(ret_for_clone3, ret_other=0x7FFF0000):
    insns = b"".join(SOCK_FILTER.pack(*i) for i in (
        (0x20, 0, 0, 0),                        # A = seccomp_data.nr
        (0x15, 0, 1, NR_CLONE3),                # if A == clone3 fallthrough else skip 1
        (0x06, 0, 0, ret_for_clone3),
        (0x06, 0, 0, ret_other)))
    buf = ctypes.create_string_buffer(insns, len(insns))
    fprog = ctypes.create_string_buffer(16)
    struct.pack_into("@HQ", fprog, 0, 4, ctypes.addressof(buf))   # struct sock_fprog {u16 len; ptr filter}
    return fprog, buf


def install(flags, ret_for_clone3):
    sysc(NR_PRCTL, 38, 1, 0, 0, 0)               # PR_SET_NO_NEW_PRIVS (the pods set it anyway)
    fprog, keep = make_prog(ret_for_clone3)
    r, e = sysc(NR_SECCOMP, 1, flags, ctypes.c_void_p(ctypes.addressof(fprog)))
    return r, e, (fprog, keep)


def own_filter_probe(label, action):
    """Child installs its own filter returning ACTION for clone3, then calls plain clone3."""
    pid = os.fork()
    if pid == 0:
        r, e, _k = install(0, action)
        if r != 0:
            E("ownfilter_%s" % label, "filter install refused %s" % en(e))
            os._exit(0)
        E("ownfilter_%s" % label, "clone3 -> %s" % clone3(0))
        os._exit(0)
    _, st = os.waitpid(pid, 0)
    if os.WIFSIGNALED(st):
        E("ownfilter_%s" % label, "child killed by signal %d" % os.WTERMSIG(st))


own_filter_probe("ALLOW", 0x7FFF0000)
own_filter_probe("LOG", 0x7FFC0000)
own_filter_probe("TRACE", 0x7FF00000)
own_filter_probe("ERRNO_0", 0x00050000)
# actions that outrank TRACE (lower value) without being a plain refusal: USER_NOTIF with no listener,
# and values no kernel defines (Linux kills the process on those; what does the Sentry do?)
own_filter_probe("USER_NOTIF_nolistener", 0x7FC00000)
own_filter_probe("UNKNOWN_7fe00000", 0x7FE00000)
own_filter_probe("UNKNOWN_7ff80000", 0x7FF80000)
own_filter_probe("UNKNOWN_7ffe0000", 0x7FFE0000)


def seccomp_flag_probe(label, op, flags):
    """Which seccomp(2) calls does the pod's profile still let through? (child: the filter sticks to it)"""
    pid = os.fork()
    if pid == 0:
        sysc(NR_PRCTL, 38, 1, 0, 0, 0)
        fprog, keep = make_prog(0x7FFF0000)          # clone3 -> ALLOW (never matters: the pod filter outranks it)
        r, e = sysc(NR_SECCOMP, op, flags, ctypes.c_void_p(ctypes.addressof(fprog)))
        E("seccomp_%s" % label, "ret=%s" % ("ok" if r >= 0 else en(e)))
        os._exit(0)
    os.waitpid(pid, 0)


for _lbl, _fl in (("flags0", 0), ("TSYNC", 1), ("LOG", 2), ("SPEC_ALLOW", 4), ("NEW_LISTENER", 8), ("TSYNC+NEW_LISTENER", 9),
                  ("NEW_LISTENER_high_bits", 0x100000008), ("TSYNC_ESRCH", 0x10)):
    seccomp_flag_probe(_lbl, 1, _fl)


def notif_attack(label, clone_flags):
    """USER_NOTIF outranks TRACE (but not ERRNO): a child installs a listener filter that sends
    clone3 to a supervisor (its parent), which answers CONTINUE. If clone3 then runs, the pod's
    TRACE/ENOSYS verdict was bypassed."""
    ps, cs = socket.socketpair()
    pid = os.fork()
    if pid == 0:
        ps.close()
        r, e, keep = install(8, 0x7FC00000)       # SECCOMP_FILTER_FLAG_NEW_LISTENER
        if r < 0:
            cs.send(("NOLISTENER %s" % en(e)).encode())
            os._exit(0)
        socket.send_fds(cs, [b"FD"], [r])
        os.close(r)
        signal.alarm(20)
        cs.send(("RESULT %s" % clone3(clone_flags)).encode())
        os._exit(0)
    cs.close()
    msg, fds, _f, _a = socket.recv_fds(ps, 64, 1)
    if not fds:
        E("notif_attack_" + label, msg.decode())
        os.waitpid(pid, 0)
        return
    lfd = fds[0]
    handled = 0
    result = None
    for _ in range(20):
        rl, _w, _x = select.select([lfd, ps], [], [], 8)
        if not rl:
            break
        if ps in rl:
            result = ps.recv(100).decode()
            break
        buf = bytearray(80)
        fcntl.ioctl(lfd, 0xC0502100, buf)           # SECCOMP_IOCTL_NOTIF_RECV
        nid = struct.unpack_from("Q", buf, 0)[0]
        nr = struct.unpack_from("i", buf, 16)[0]
        resp = bytearray(struct.pack("QqiI", nid, 0, 0, 1))   # CONTINUE
        fcntl.ioctl(lfd, 0xC0182101, resp)          # SECCOMP_IOCTL_NOTIF_SEND
        handled += 1
        if result is None and ps in select.select([ps], [], [], 5)[0]:
            result = ps.recv(100).decode()
            break
    if result is None:
        os.kill(pid, signal.SIGKILL)
    os.waitpid(pid, 0)
    E("notif_attack_" + label, "supervisor handled %d notification(s); child: %s" % (handled, result or "no result (child blocked or refused)"))


try:
    notif_attack("clone3_plain", 0)
    notif_attack("clone3_NEWUSER", FLAGS["NEWUSER"])
except Exception as ex:  # noqa: BLE001
    E("notif_attack", "exception %r" % (ex,))

# --- 6. sockets that RuntimeDefault refuses (arg-compare rules survive the runsc conversion?)
for nm, dom in (("AF_UNIX", 1), ("AF_INET", 2), ("AF_NETLINK", 16), ("AF_PACKET", 17), ("AF_ALG", 38), ("AF_VSOCK", 40), ("AF_XDP", 44)):
    r, e = sysc(41, dom, 2 if dom != 16 else 3, 0)
    E("socket_" + nm, "ret=%s" % ("fd" if r >= 0 else en(e)))
    if r >= 0:
        os.close(r)

# --- 7. personality() arg rules
for nm, v in (("0", 0), ("PER_LINUX32", 8), ("READ_IMPLIES_EXEC", 0x0400000)):
    r, e = sysc(135, v)
    E("personality_" + nm, "ret=%s" % (r if r >= 0 else en(e)))
E("DONE", "")
