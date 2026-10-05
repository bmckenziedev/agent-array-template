"""Filesystem helpers."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

# Windows: a console program started by a process that has no console (the supervised worker) gets a NEW
# console window of its own. Each node/git/docker call then flashes a terminal window, costs ~20x the
# spawn time, and under load fails with 0xC0000142 (desktop heap). CREATE_NO_WINDOW gives it no window.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) if os.name == "nt" else 0
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200) if os.name == "nt" else 0


def hidden() -> dict:
    """subprocess kwargs that keep a child from opening a console window (empty off Windows)."""
    return {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}


def is_loader_crash(code: int | None) -> bool:
    """A Windows NTSTATUS exit (0xC0000000..): the program died before it could answer (DLL init failed,
    access violation, out of desktop heap). Environmental, so worth a retry. POSIX exit codes are < 256
    and a signal is negative: neither is retried (an OOM kill would only repeat)."""
    return code is not None and code >= 0xC0000000


def rmtree(path: Path) -> None:
    """Remove a tree, including read-only files (git objects are read-only on Windows)."""
    def onexc(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            func(p)
        except OSError:
            pass
    p = Path(path)
    if p.is_symlink() or p.is_file():
        p.unlink(missing_ok=True)
    elif p.exists():
        shutil.rmtree(p, onexc=onexc)
