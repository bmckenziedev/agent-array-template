#!/usr/bin/env python3
"""Diff two syscall-sweep logs (sweep.py output, e.g. `kubectl logs POD > file`).

    python3 compare_sweeps.py RD.log PROFILE.log [--names unistd_64.h]

Prints every syscall number whose class differs. For RuntimeDefault vs the shipped profile, under the same runtime,
the expected output is exactly one line: 101 (ptrace). Exit 1 on any other difference. --names takes a copy of
/usr/include/x86_64-linux-gnu/asm/unistd_64.h (from a node) to print syscall names.
"""
import re
import sys


def load(path):
    out = {}
    for line in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"^S (\d+) (\S+)", line)
        if m:
            out[int(m.group(1))] = m.group(2)
    if not out:
        raise SystemExit("%s: no 'S <nr> <class>' lines" % path)
    return out


def main(argv):
    names = {}
    if "--names" in argv:
        i = argv.index("--names")
        for line in open(argv[i + 1], encoding="utf-8"):
            m = re.match(r"#define __NR_(\w+)\s+(\d+)", line)
            if m:
                names[int(m.group(2))] = m.group(1)
        del argv[i:i + 2]
    if len(argv) != 2:
        raise SystemExit(__doc__)
    a, b = load(argv[0]), load(argv[1])
    unexpected = 0
    for nr in sorted(set(a) | set(b)):
        if a.get(nr) != b.get(nr):
            print("%3d %-24s %-12s -> %s" % (nr, names.get(nr, "?"), a.get(nr), b.get(nr)))
            unexpected += nr != 101
    print("%d syscalls compared; %d differ%s" % (len(set(a) & set(b)), sum(a.get(n) != b.get(n) for n in set(a) | set(b)),
          "" if unexpected == 0 else " (%d besides ptrace: investigate)" % unexpected))
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
