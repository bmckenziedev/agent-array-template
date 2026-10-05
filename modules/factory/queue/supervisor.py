#!/usr/bin/env python3
"""Foreground process-tree guard: parent pipe EOF also cleans up after SIGKILL."""
import contextlib
import os
import selectors
import signal
import subprocess
import sys


def main():
    stop = [False]
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.__setitem__(0, True))
    poll = selectors.DefaultSelector()
    poll.register(sys.stdin.buffer, selectors.EVENT_READ)
    child = subprocess.Popen(sys.argv[1:], preexec_fn=os.setpgrp)
    try:
        while child.poll() is None:
            if stop[0] or (poll.select(.05) and not os.read(sys.stdin.fileno(), 1)):
                return 124
        return child.returncode
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGTERM)
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        poll.close()


if __name__ == '__main__':
    sys.exit(main())
