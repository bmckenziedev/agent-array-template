#!/usr/bin/env bash
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/policy"
python_bin="${PYTHON_BIN:-$(python3 -c 'import sys; print(sys.executable)')}"
if command -v cygpath >/dev/null 2>&1; then
  python_bin="$(cygpath -u "$python_bin")"
fi
cat > "$tmp/bin/entrypoint-check" <<EOF
#!/usr/bin/env bash
# Windows Python folds environment names; emulate Linux case-sensitive keys.
exec "$python_bin" -c 'import os,runpy,sys; os.environ=dict(os.environ); sys.argv=sys.argv[1:]; runpy.run_path(sys.argv[0],run_name="__main__")' "$root/common/bin/entrypoint-check" "\$@"
EOF
chmod +x "$tmp/bin/entrypoint-check"
# Stub vendor CLIs must never be reached on a preflight refusal.
for cli in claude codex kimi; do
  printf '#!/usr/bin/env bash\nexit 99\n' > "$tmp/bin/$cli"
  chmod +x "$tmp/bin/$cli"
done
run_refusal() {
  local expected="$1"
  shift
  if env -i PATH="$tmp/bin:$PATH" AA_POLICY_DIR="$policy" "${tool_env[@]}" "$@" bash "$root/$tool/image/bin/entrypoint.sh" check > "$tmp/output" 2>&1; then
    cat "$tmp/output"
    echo "FAIL expected refusal: $tool $expected" >&2
    exit 1
  fi
  if ! grep -q "$expected" "$tmp/output"; then cat "$tmp/output"; exit 1; fi
}
for tool in claude codex kimi; do
  policy="$tmp/$tool-policy"
  mkdir -p "$policy"
  tool_env=(AA_LOGIN_STORAGE=disk)
  case "$tool" in
    claude)
      files=(managed-settings.json)
      tool_env+=(CLAUDE_CONFIG_DIR="$tmp/missing-home")
      ;;
    codex)
      files=(requirements.toml managed_config.toml)
      tool_env+=(CODEX_HOME="$tmp/missing-home")
      ;;
    kimi)
      files=(policy.toml)
      tool_env+=(KIMI_CODE_HOME="$tmp/missing-home"
        KIMI_DISABLE_TELEMETRY=1 KIMI_CODE_NO_AUTO_UPDATE=1 KIMI_DISABLE_CRON=1
        KIMI_CODE_MODEL_CATALOG_REFRESH_ON_START=0
        KIMI_CODE_MODEL_CATALOG_REFRESH_INTERVAL_MS=0 KIMI_CODE_WATCH=0
        KIMI_CODE_BACKGROUND_KEEP_ALIVE_ON_EXIT=0
        AA_KIMI_EGRESS_PROXY=http://gateway.example.org:3128
        HTTPS_PROXY=http://gateway.example.org:3128 NO_PROXY=127.0.0.1,localhost)
      ;;
  esac
  : > "$policy/.rendered.sha256"
  for file in "${files[@]}"; do
    printf '{}\n' > "$policy/$file"
    printf '%064d  %s\n' 0 "$file" >> "$policy/.rendered.sha256"
  done
  run_refusal 'environment variables' ANTHROPIC_API_KEY=synthetic
  run_refusal 'environment variables' OPENAI_BASE_URL=https://example.invalid
  run_refusal 'environment variables' CLAUDE_CODE_OAUTH_TOKEN=synthetic
  run_refusal 'policy hash mismatch'
  "$python_bin" -c 'import hashlib,pathlib,sys; p=pathlib.Path(sys.argv[1]); files=sorted(f for f in p.iterdir() if f.name!=".rendered.sha256"); (p/".rendered.sha256").write_text("".join(hashlib.sha256(f.read_bytes()).hexdigest()+"  "+f.name+"\n" for f in files))' "$policy"
  run_refusal 'login directory must exist'
  echo "PASS $tool entrypoint credential, policy hash and login directory refusals"
done
