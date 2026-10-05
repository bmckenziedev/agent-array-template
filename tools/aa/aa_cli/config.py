"""Non-secret deployment configuration and isolated OIDC kubeconfig."""

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

FIELDS = {
    "cluster_api_url", "ca_bundle_path", "oidc_issuer", "oidc_client_id",
    "label_prefix", "user_namespace_prefix", "NS_SYSTEM", "panel_url",
    "pace_service_path",
}


def directory() -> Path:
    if os.environ.get("AA_CONFIG_DIR"):
        return Path(os.environ["AA_CONFIG_DIR"])
    if os.name == "nt":
        return Path(os.environ["APPDATA"]) / "agent-array"
    return Path.home() / ".config" / "agent-array"


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")
    path.chmod(0o600)


def validate(data: dict) -> dict:
    if set(data) != FIELDS or not all(isinstance(v, str) for v in data.values()):
        raise ValueError("client config must contain exactly the deployment fields")
    for key in ("cluster_api_url", "oidc_issuer", "panel_url"):
        if key == "panel_url" and not data[key]:
            continue
        url = urlsplit(data[key])
        if url.scheme != "https" or not url.hostname or url.username or url.password:
            raise ValueError(f"{key} requires HTTPS without embedded credentials")
        if url.query or url.fragment:
            raise ValueError(f"{key} must not contain query or fragment")
    if not data["ca_bundle_path"] or not data["oidc_client_id"]:
        raise ValueError("CA bundle and OIDC client id are required")
    import re
    for key in ("user_namespace_prefix", "NS_SYSTEM"):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", data[key]):
            raise ValueError(f"invalid {key}")
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", data["label_prefix"]):
        raise ValueError("invalid label_prefix")
    expected = f"/api/v1/namespaces/{data['NS_SYSTEM']}/services/pace:8080/proxy"
    if data["pace_service_path"].rstrip("/") != expected:
        raise ValueError("pace_service_path must address the system pace service proxy")
    return data


def load() -> dict:
    return validate(json.loads((directory() / "config.json").read_text(encoding="utf-8")))


def init(source: str) -> Path:
    data = validate(json.loads(Path(source).read_text(encoding="utf-8")))
    path = directory() / "config.json"
    write_json(path, data)
    return path


def kubeconfig() -> Path:
    return directory() / "kubeconfig.json"
