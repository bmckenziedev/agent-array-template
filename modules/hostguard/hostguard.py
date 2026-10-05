#!/usr/bin/env python3
"""Render an additive host INPUT guard; mutation requires --apply --yes."""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import subprocess

TABLE = "aa_hostguard"
MARKER = "agent-array hostguard v1"


def read_env(path):
    """Read data only: never source an env file as shell code."""
    values = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in {"POD_CIDR", "POD_INTERFACES_JSON"} or key in values:
            raise ValueError("unknown, duplicate or malformed environment setting")
        if value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        values[key] = value
    if set(values) != {"POD_CIDR", "POD_INTERFACES_JSON"}:
        raise ValueError("rendered env requires POD_CIDR and POD_INTERFACES_JSON")
    interfaces = json.loads(values["POD_INTERFACES_JSON"])
    if not isinstance(interfaces, list):
        raise ValueError("pod interfaces must be a JSON list")
    return [values["POD_CIDR"]], interfaces


def render(cidrs, interfaces=()):
    nets = sorted({ipaddress.ip_network(c, strict=True) for c in cidrs}, key=str)
    if not nets:
        raise ValueError("at least one explicit pod CIDR is required")
    if any(not isinstance(i, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,15}", i)
           for i in interfaces):
        raise ValueError("unsafe interface name")
    rules = [f'    iifname "{i}" tcp dport {{ 22, 2379, 2380 }} counter drop'
             for i in sorted(set(interfaces))]
    for net in nets:
        family = "ip" if net.version == 4 else "ip6"
        rules.append(f"    {family} saddr {net} tcp dport {{ 22, 2379, 2380 }} counter drop")
    return (f'table inet {TABLE} {{\n  comment "{MARKER}"\n'
            '  chain input {\n    type filter hook input priority -10; policy accept;\n'
            + "\n".join(rules) + "\n  }\n}\n")


def inspect(inventory):
    tables = [item["table"] for item in inventory["nftables"] if "table" in item
              and item["table"].get("family") == "inet"
              and item["table"].get("name") == TABLE]
    if len(tables) > 1 or (tables and tables[0].get("comment") != MARKER):
        raise ValueError("owned table name collision: refusing foreign table")
    return bool(tables)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", required=True, help="rendered hostguard.env data file")
    parser.add_argument("--pod-cidr", action="append", default=[], help="additional dual-stack pod CIDR")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--report", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and not args.yes:
        parser.error("--apply requires --yes")
    if args.yes and not args.apply:
        parser.error("--yes is only valid with --apply")
    try:
        cidrs, interfaces = read_env(args.env)
        draft = render(cidrs + args.pod_cidr, interfaces)
        if args.report or args.check or args.apply:
            inventory = subprocess.run(["nft", "-j", "list", "ruleset"],
                                       capture_output=True, text=True, check=True)
            exists = inspect(json.loads(inventory.stdout))
            if args.report:
                print("owned table present" if exists else "owned table absent")
            else:
                if exists:
                    raise ValueError("owned table already exists: review diff; automatic reconciliation refused")
                subprocess.run(["nft", "--check", "--file", "-"], input=draft, text=True, check=True)
                if args.apply:
                    # Exclusive creation fails if another process creates the table after inspection.
                    transaction = f"create table inet {TABLE}\n" + draft
                    subprocess.run(["nft", "--file", "-"], input=transaction, text=True, check=True)
        print(draft, end="")
        return 0
    except (ValueError, KeyError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"hostguard: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
