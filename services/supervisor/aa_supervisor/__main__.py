"""Foreground Unix supervisor; lifecycle ends with the container."""
import signal
import json
import datetime
from pathlib import Path
from .service import Supervisor
from .transport import UnixAPI


def main():
    service = Supervisor()
    path = Path(service.env.get("AA_SUPERVISOR_POLICY", "/etc/aa-supervisor/policy.json"))
    try:
        holder = json.loads(path.read_bytes()).get("holder", {}).get("slug")
        if holder != service.env.get("AA_USER"):
            service.audit("supervisor.start", "deny", detail={"reason": "holder_mismatch"})
            return 1
    except (OSError, ValueError, AttributeError):
        pass
    service.reload()
    if service.policy.get("console_url"):
        from .connector import Connector
        service.connector = Connector(service, service.policy["console_url"])
        service.connector.start()
    api = UnixAPI(service)
    def stop(_signum, _frame):
        api.running = False
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, stop)
    try:
        api.serve()
    finally:
        if getattr(service, "connector", None):
            service.connector.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        from .egress import Egress
        Egress().send({"ts": datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z"),
            "component": "supervisor", "event": "supervisor.start", "actor": {"user": None, "sa": None, "sub": None},
            "team": None, "target": {}, "outcome": "error", "detail": {"reason": "start_failed"}})
        raise SystemExit(1)
