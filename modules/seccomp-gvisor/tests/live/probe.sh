#!/usr/bin/env bash
set -euo pipefail
# Each syscall subprobe reports its own status; continue to collect the complete matrix.
set +e
# clone3 / thread probe for one container. Prints one "RESULT <check>: ..." line per check.
# Each guarded diagnostic reports failures independently. Requires bash in the probe image.
echo "kernel: $(uname -r)"
grep -E '^(Seccomp|Seccomp_filters|NoNewPrivs|CapEff)' /proc/self/status | tr '\n' ' '; echo
if ldd --version 2>&1 | head -1 | grep -qi musl || ls /lib/ld-musl-* >/dev/null 2>&1; then
  echo "RESULT libc: musl (never calls clone3; cannot show the bug)"
else
  echo "RESULT libc: $(ldd --version 2>&1 | head -1)"
fi
echo "RESULT gcc: $(command -v gcc cc 2>/dev/null | tr '\n' ' ')"
if command -v python3 >/dev/null 2>&1; then
python3 - <<'EOF'
import ctypes, errno, os
libc = ctypes.CDLL(None, use_errno=True)
libc.syscall.restype = ctypes.c_long
def en(e): return errno.errorcode.get(e, "?")
# 1) clone3(NULL, 0): a reachable (gVisor or host) kernel answers EINVAL; a seccomp
#    verdict (ENOSYS 38 or EPERM 1) comes back before the arguments are looked at.
r = libc.syscall(435, ctypes.c_void_p(0), ctypes.c_size_t(0)); e = ctypes.get_errno()
print("RESULT clone3_raw: ret=%d errno=%d %s" % (r, e, en(e)))
# 2) clone3 with CLONE_NEWUSER, i.e. what RuntimeDefault's clone3 rule exists to stop
#    (seccomp cannot read clone3's flags, so it must refuse clone3 as a whole).
class CloneArgs(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint64) for n in
                ("flags", "pidfd", "child_tid", "parent_tid", "exit_signal", "stack", "stack_size", "tls")]
a = CloneArgs(); a.flags = 0x10000000; a.exit_signal = 17   # CLONE_NEWUSER, SIGCHLD
r = libc.syscall(435, ctypes.byref(a), ctypes.c_size_t(ctypes.sizeof(a))); e = ctypes.get_errno()
if r == 0:
    os._exit(0)                      # child: leave at once
if r > 0:
    os.waitpid(r, 0)
    print("RESULT clone3_newuser: CREATED a child in a new user namespace (not refused)")
else:
    print("RESULT clone3_newuser: refused errno=%d %s" % (e, en(e)))
# 3) glibc pthread_create called directly; its return code is the errno it got.
tid = ctypes.c_ulong()
start = ctypes.cast(libc.getpid, ctypes.c_void_p)   # a C function: no Python in the new thread
rc = libc.pthread_create(ctypes.byref(tid), None, start, None)
if rc == 0:
    libc.pthread_join(tid, None)
print("RESULT pthread_create: rc=%d %s" % (rc, "OK" if rc == 0 else en(rc)))
# 4) ptrace on a pid that cannot exist: allowed -> ESRCH, filtered -> EPERM.
libc.ptrace.restype = ctypes.c_long
r = libc.ptrace(ctypes.c_long(2), ctypes.c_long(0x3ffffff0), None, None); e = ctypes.get_errno()
print("RESULT ptrace: ret=%d errno=%d %s" % (r, e, en(e)))
# 4b) Can a tracer let a traced clone3(CLONE_NEWUSER) through? Child: PTRACE_TRACEME,
#     SIGSTOP, clone3. Parent: PTRACE_O_TRACESECCOMP, then continue through every stop.
#     Child exit 10 = clone3 ran (bypass), 20+errno = clone3 refused, 100+errno = TRACEME refused.
import signal
def _alarm(*_):
    raise TimeoutError()
signal.signal(signal.SIGALRM, _alarm)
pid = os.fork()
if pid == 0:
    if libc.ptrace(ctypes.c_long(0), ctypes.c_long(0), None, None) != 0:
        os._exit(100 + ctypes.get_errno())
    os.kill(os.getpid(), signal.SIGSTOP)
    b = CloneArgs(); b.flags = 0x10000000; b.exit_signal = 17
    r = libc.syscall(435, ctypes.byref(b), ctypes.c_size_t(ctypes.sizeof(b)))
    if r == 0:
        os._exit(0)
    if r > 0:
        os.waitpid(r, 0); os._exit(10)
    os._exit(20 + ctypes.get_errno())
signal.alarm(20)
try:
    _, st = os.waitpid(pid, 0)
    if os.WIFSTOPPED(st):
        libc.ptrace(ctypes.c_long(0x4200), ctypes.c_long(pid), None, ctypes.c_void_p(0x80 | 0x100000))
        libc.ptrace(ctypes.c_long(7), ctypes.c_long(pid), None, None)
        while True:
            _, st = os.waitpid(pid, 0)
            if not os.WIFSTOPPED(st):
                break
            sig = os.WSTOPSIG(st)
            deliver = 0 if (sig == signal.SIGTRAP or sig == signal.SIGSTOP) else sig
            libc.ptrace(ctypes.c_long(7), ctypes.c_long(pid), None, ctypes.c_void_p(deliver))
    code = os.WEXITSTATUS(st) if os.WIFEXITED(st) else -1
    if code == 10:
        print("RESULT trace_bypass: clone3(NEWUSER) RAN under a tracer")
    elif code >= 100:
        print("RESULT trace_bypass: impossible, PTRACE_TRACEME refused errno=%d %s" % (code - 100, en(code - 100)))
    elif code >= 20:
        print("RESULT trace_bypass: clone3 still refused under a tracer errno=%d %s" % (code - 20, en(code - 20)))
    else:
        print("RESULT trace_bypass: unclear status=%r" % (st,))
except TimeoutError:
    os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
    print("RESULT trace_bypass: TIMEOUT")
signal.alarm(0)
# 4c) Can a process's OWN seccomp listener filter beat the pod's TRACE verdict on clone3? In a Linux
#     filter stack the highest-precedence verdict wins and USER_NOTIF outranks TRACE (not ERRNO, so not
#     plain RuntimeDefault). Child: NEW_LISTENER filter sending clone3 to a supervisor, then clone3.
#     Parent: answer CONTINUE. A refused install (EPERM from this profile, EINVAL from gVisor) is the
#     expected "impossible"; clone3 running after CONTINUE is the bypass.
import fcntl, select, socket, struct
def listener_bypass():
    insns = b"".join(struct.pack("HBBI", *i) for i in (
        (0x20, 0, 0, 0), (0x15, 0, 1, 435), (0x06, 0, 0, 0x7FC00000), (0x06, 0, 0, 0x7FFF0000)))
    buf = ctypes.create_string_buffer(insns, len(insns))
    fprog = ctypes.create_string_buffer(16)
    struct.pack_into("@HQ", fprog, 0, 4, ctypes.addressof(buf))
    ps, cs = socket.socketpair()
    pid = os.fork()
    if pid == 0:
        ps.close()
        libc.syscall(ctypes.c_long(157), ctypes.c_long(38), ctypes.c_long(1), ctypes.c_long(0), ctypes.c_long(0), ctypes.c_long(0))  # NO_NEW_PRIVS
        fd = libc.syscall(ctypes.c_long(317), ctypes.c_long(1), ctypes.c_long(8), ctypes.c_void_p(ctypes.addressof(fprog)))  # NEW_LISTENER
        if fd < 0:
            cs.send(b"NOFD %d" % ctypes.get_errno())
            os._exit(0)
        socket.send_fds(cs, [b"FD"], [fd])
        os.close(fd)
        signal.alarm(20)
        b = CloneArgs(); b.exit_signal = 17
        r = libc.syscall(ctypes.c_long(435), ctypes.byref(b), ctypes.c_size_t(ctypes.sizeof(b)))
        if r == 0:
            os._exit(0)
        if r > 0:
            os.waitpid(r, 0)
            cs.send(b"RAN")
        else:
            cs.send(b"REFUSED %d" % ctypes.get_errno())
        os._exit(0)
    cs.close()
    msg, fds, _f, _a = socket.recv_fds(ps, 64, 1)
    verdict = msg.decode()
    if fds:
        verdict = "no result"
        for _ in range(20):
            if not select.select([fds[0], ps], [], [], 8)[0]:
                break
            if select.select([ps], [], [], 0)[0]:
                verdict = ps.recv(100).decode()
                break
            nbuf = bytearray(80)
            fcntl.ioctl(fds[0], 0xC0502100, nbuf)                                   # SECCOMP_IOCTL_NOTIF_RECV
            fcntl.ioctl(fds[0], 0xC0182101, bytearray(struct.pack("QqiI", struct.unpack_from("Q", nbuf, 0)[0], 0, 0, 1)))  # SEND, CONTINUE
            if select.select([ps], [], [], 5)[0]:
                verdict = ps.recv(100).decode()
                break
    try:
        os.kill(pid, signal.SIGKILL)       # a blocked child (no answer) must not outlive the probe
    except ProcessLookupError:
        pass
    os.waitpid(pid, 0)
    return verdict
v = listener_bypass()
if v.startswith("NOFD"):
    e = int(v.split()[1])
    print("RESULT listener_bypass: impossible, NEW_LISTENER refused errno=%d %s" % (e, en(e)))
elif v == "RAN":
    print("RESULT listener_bypass: clone3 RAN after a supervisor answered CONTINUE")
else:
    print("RESULT listener_bypass: clone3 not run (%s)" % v)
# 5) unshare(CLONE_NEWUSER): RuntimeDefault without CAP_SYS_ADMIN refuses it (EPERM);
#    success means no seccomp filter is active. Last, since it changes this process.
r = libc.unshare(0x10000000); e = ctypes.get_errno()
print("RESULT unshare_newuser: ret=%d errno=%d %s" % (r, e, en(e) if r else "allowed"))
EOF
python3 -c '
import threading
ok = []
ts = [threading.Thread(target=ok.append, args=(i,)) for i in range(8)]
[t.start() for t in ts]; [t.join() for t in ts]
print("RESULT python_threads: OK (%d threads)" % len(ok))
' 2>&1 | tail -1 | sed '/^RESULT /!s/^/RESULT python_threads: FAIL /'
# glibc posix_spawn also tries clone3 first (then clone(CLONE_VM|CLONE_VFORK) on ENOSYS)
python3 -c '
import os, shutil
pid = os.posix_spawn(shutil.which("true"), ["true"], dict(os.environ))
_, st = os.waitpid(pid, 0)
print("RESULT posix_spawn: OK" if st == 0 else "RESULT posix_spawn: FAIL status=%d" % st)
' 2>&1 | tail -1 | sed '/^RESULT /!s/^/RESULT posix_spawn: FAIL /'
python3 -c '
import subprocess
out = subprocess.run(["sh", "-c", "echo spawned"], capture_output=True, text=True, check=True).stdout.strip()
print("RESULT python_subprocess: OK" if out == "spawned" else "RESULT python_subprocess: FAIL " + out)
' 2>&1 | tail -1 | sed '/^RESULT /!s/^/RESULT python_subprocess: FAIL /'
else
  for c in clone3_raw clone3_newuser pthread_create ptrace trace_bypass listener_bypass unshare_newuser python_threads posix_spawn python_subprocess; do
    echo "RESULT $c: SKIP (no python3)"; done
fi
if command -v node >/dev/null 2>&1; then
node -e '
const { Worker } = require("worker_threads");
const w = new Worker("require(\"worker_threads\").parentPort.postMessage(21*2)", { eval: true });
w.on("message", (m) => { console.log("RESULT node_worker_threads: OK " + process.version + " got " + m); });
w.on("error", (e) => { console.log("RESULT node_worker_threads: FAIL " + e.message); process.exit(1); });
' 2>&1 | tail -1 | sed '/^RESULT /!s/^/RESULT node_worker_threads: FAIL /'
node -e '
const out = require("child_process").execFileSync("sh", ["-c", "echo spawned"]).toString().trim();
console.log(out === "spawned" ? "RESULT node_child_process: OK" : "RESULT node_child_process: FAIL " + out);
' 2>&1 | tail -1 | sed '/^RESULT /!s/^/RESULT node_child_process: FAIL /'
else
  echo "RESULT node_worker_threads: SKIP (no node)"
  echo "RESULT node_child_process: SKIP (no node)"
fi
if command -v claude >/dev/null 2>&1; then
  # Claude Code is a Bun binary; under the bug it aborts at start ("panic: abort() called").
  out="$(timeout 60 claude --help 2>&1)"
  case "$out" in *Usage:*) echo "RESULT bun_claude_help: OK";;
    *) echo "RESULT bun_claude_help: FAIL: $(printf '%s' "$out" | tail -2 | tr '\n' ' ' | cut -c1-160)";; esac
else
  echo "RESULT bun_claude_help: SKIP (no claude)"
fi
