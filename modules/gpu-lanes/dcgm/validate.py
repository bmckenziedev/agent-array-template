#!/usr/bin/env python3
"""Offline DCGM counter and reference checks. Exit 0 pass, 1 validation failure."""
import csv
import json
from pathlib import Path
import re

import yaml


def validate():
    root = Path(__file__).resolve().parents[1]
    text = (root / "k8s/dcgm-exporter.tmpl.yaml").read_text()
    objects = list(yaml.safe_load_all(text))
    by_kind = {o["kind"]: o for o in objects}
    assert set(by_kind) == {"DaemonSet", "Service", "ServiceMonitor", "ConfigMap"}
    pod = by_kind["DaemonSet"]["spec"]["template"]["spec"]
    container = pod["containers"][0]
    assert container["securityContext"]["capabilities"]["add"] == ["SYS_ADMIN"]
    assert not container["securityContext"].get("privileged", False)
    assert "nvidia.com/gpu" not in container["resources"].get("limits", {})
    counters = {}
    for line in by_kind["ConfigMap"]["data"]["counters.csv"].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        field, kind, help_text = [v.strip() for v in next(csv.reader([line]))]
        assert re.fullmatch(r"DCGM_(FI|EXP)_[A-Z0-9_]+", field)
        assert field not in counters and kind in {"gauge", "counter", "label"} and help_text
        assert not field.startswith("DCGM_FI_PROF_")
        counters[field] = kind
    for field in ("DCGM_FI_DEV_GPU_TEMP", "DCGM_FI_DEV_POWER_MGMT_LIMIT",
                  "DCGM_FI_DEV_POWER_VIOLATION", "DCGM_FI_DEV_THERMAL_VIOLATION",
                  "DCGM_EXP_CLOCK_EVENTS_COUNT", "DCGM_FI_DEV_XID_ERRORS"):
        assert field in counters
    service = by_kind["Service"]
    assert service["spec"]["selector"]["app.kubernetes.io/name"] == "dcgm-exporter"
    assert by_kind["ServiceMonitor"]["spec"]["endpoints"][0]["port"] == service["spec"]["ports"][0]["name"]
    print(f"PASS: DCGM references and {len(counters)} unique counters")


if __name__ == "__main__":
    validate()
