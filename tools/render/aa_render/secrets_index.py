"""Aggregate secret names and provisioning recipes, never secret values."""

import json

from .templates import RenderError, lookup, walk, subst
from .yamlsub import load


def render_secrets(root, model, emit, exclude=()):
    records = []
    namespaces = model["org"]["namespaces"]
    for path in walk(root, model, exclude=exclude):
        if path.name != "secrets.required.yaml":
            continue
        doc = load(path)
        if not isinstance(doc, dict) or doc.get("version") != 1 or not isinstance(doc.get("secrets"), list):
            raise RenderError(f"{path}: expected version: 1 and secrets list")
        for secret in doc["secrets"]:
            if not isinstance(secret, dict):
                raise RenderError(f"{path}: secrets entry must be a mapping")
            for key in ("name", "namespace_ref", "keys", "purpose", "recipe", "required_when"):
                if key not in secret:
                    raise RenderError(f"{path}: secrets.{key} is required")
            for key in ("name", "namespace_ref", "purpose", "recipe", "required_when"):
                if not isinstance(secret[key], str):
                    raise RenderError(f"{path}: secrets.{key} must be a string")
            when = secret["required_when"]
            if when != "always" and not lookup(model, when):
                continue
            ref = subst(secret["namespace_ref"], model["keys"])
            if ref == "user":
                targets = [u["USER_NS"] for u in model["entities"]["user"]]
            elif ref in namespaces and ref != "user_prefix":
                targets = [namespaces[ref]]
            elif ref.startswith(model["keys"]["PROJECT_NAME"] + "-") and "modules" in path.parts:
                targets = [ref]
            else:
                raise RenderError(f"{path}: invalid namespace_ref {ref}")
            if not isinstance(secret["keys"], list) or not all(isinstance(k, str) for k in secret["keys"]):
                raise RenderError(f"{path}: secrets.keys must be a list of strings")
            for namespace in targets:
                records.append(
                    {
                        **{
                            k: secret[k]
                            for k in ("name", "namespace_ref", "keys", "purpose", "recipe", "required_when")
                        },
                        "namespace": namespace,
                        "source": path.relative_to(root).as_posix(),
                    }
                )
    records.sort(key=lambda r: (r["namespace"], r["name"], r["source"]))
    emit(
        "files/SECRETS-REQUIRED.json",
        json.dumps(records, indent=2, sort_keys=True) + "\n",
        "secrets.required.yaml",
    )
    lines = [
        "# Required Secrets",
        "",
        "Only names and keys are listed; values are provisioned separately.",
        "",
        "| Namespace | Secret | Keys | Purpose | Recipe | Source |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for record in records:
        cells = [
            record["namespace"],
            record["name"],
            ", ".join(record["keys"]),
            record["purpose"],
            record["recipe"],
            record["source"],
        ]
        lines.append("| " + " | ".join(str(v).replace("|", "\\|").replace("\n", " ") for v in cells) + " |")
    emit("files/SECRETS-REQUIRED.md", "\n".join(lines) + "\n", "secrets.required.yaml")
