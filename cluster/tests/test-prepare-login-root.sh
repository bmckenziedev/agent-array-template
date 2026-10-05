#!/usr/bin/env bash
set -euo pipefail
base=$(cd "$(dirname "$0")/../.."; pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin"
export PATH="$tmp/bin:$PATH" AA_FAKE_ROOT="$tmp/root"
cat > "$tmp/bin/findmnt" <<'EOF'
#!/usr/bin/env bash
echo /dev/mapper/logins
EOF
cat > "$tmp/bin/lsblk" <<'EOF'
#!/usr/bin/env bash
echo "${FAKE_BLOCK_TYPE:-crypt}"
EOF
cat > "$tmp/bin/id" <<'EOF'
#!/usr/bin/env bash
echo 1000
EOF
chmod +x "$tmp/bin/"*
sed 's|{{LOGIN_HOST_ROOT}}|/logins|g' "$base/cluster/node-prep/prepare-login-root.tmpl.sh" > "$tmp/prep.sh"
bash "$tmp/prep.sh" --require-encrypted > "$tmp/out"
grep -q 'dm-crypt/LUKS ancestry: true' "$tmp/out"
[[ ! -e "$tmp/root" ]]
if FAKE_BLOCK_TYPE=disk bash "$tmp/prep.sh" --require-encrypted > "$tmp/out" 2>&1; then exit 1; fi
grep -q 'encrypted node volume required' "$tmp/out"
if bash "$tmp/prep.sh" --yes > "$tmp/out" 2>&1; then exit 1; fi
grep -q 'root required' "$tmp/out"
echo 'prepare-login-root: 3 cases passed'
