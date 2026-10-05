#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
cd "$HERE"
tmp="$(mktemp -d "$HERE/test-registry-XXXXXX")"
trap 'rm -rf "$tmp"' EXIT
cat > "$tmp/kubeseal.py" <<'FAKE'
#!/usr/bin/env python3
import base64
import json
import sys
assert "synthetic-token" not in " ".join(sys.argv)
secret = json.load(sys.stdin)
assert secret["type"] == "kubernetes.io/dockerconfigjson"
config = json.loads(base64.b64decode(secret["data"][".dockerconfigjson"]))
assert config["auths"]["registry.example.org"]["password"] == "synthetic-token"
assert secret["metadata"] == {"name": "pull", "namespace": "example-app"}
print("apiVersion: bitnami.com/v1alpha1\nkind: SealedSecret\nspec:\n  encryptedData: {}")
FAKE
cat > "$tmp/kubeseal" <<'SHIM'
#!/usr/bin/env bash
set -euo pipefail
exec python3 "$(dirname "$0")/kubeseal.py" "$@"
SHIM
# Windows Python resolves .cmd while Linux Python resolves the executable shim.
cat > "$tmp/kubeseal.cmd" <<'CMD'
@python "%~dp0kubeseal.py" %*
CMD
chmod +x "$tmp/kubeseal"
export PATH="$tmp:$PATH"
python3 - "$tmp" <<'PY'
import importlib.util
import os
from pathlib import Path
import sys
from unittest.mock import patch
root = Path(sys.argv[1]).resolve()
os.environ["PATH"] = str(root) + os.pathsep + os.environ["PATH"]
spec = importlib.util.spec_from_file_location("seal_secret", Path("../seal_secret.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
cert = root / "synthetic-public-cert.pem"
cert.write_text("synthetic-test-only\n")
values = iter(["synthetic-user", "synthetic-token"])
with patch.object(module, "ROOT", root), patch.object(module, "hidden", side_effect=lambda _: next(values)):
    assert module.main(["registry", "--namespace", "example-app", "--name", "pull",
                        "--registry-host", "registry.example.org", "--cert", str(cert)]) == 0
output = root / "secrets/k8s/example-app/pull.sealed.yaml"
assert output.is_file()
assert "synthetic-token" not in output.read_text()
assert "synthetic-user" not in output.read_text()
PY
printf '%s\n' 'PASS: registry credentials stay in stdin; fake kubeseal on PATH; strict scope output.'
