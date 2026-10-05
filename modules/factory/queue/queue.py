#!/usr/bin/env python3
"""Snapshot-queue CLI, without shadowing the standard library queue module."""
if __name__ == "queue":
    import importlib.util
    import sysconfig
    from pathlib import Path
    _spec = importlib.util.spec_from_file_location("_stdlib_queue", Path(sysconfig.get_path("stdlib")) / "queue.py")
    _module = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_module)
    globals().update({key: value for key, value in vars(_module).items() if not key.startswith("__")})
else:
    from factory_queue.legacy import *
    if __name__ == "__main__":
        raise SystemExit(main())
