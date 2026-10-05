#!/usr/bin/env bash
set -euo pipefail

readonly VG_NAME="ubuntu-vg"
readonly LV_NAME="ubuntu-lv"
readonly LV_PATH="/dev/${VG_NAME}/${LV_NAME}"
readonly SAFETY_BYTES=$((1024 * 1024 * 1024))

usage() {
  cat <<'EOF'
Usage: grow-root-lv.sh [--yes] [--size SIZE]

Grow the ext4 root filesystem on ubuntu-vg/ubuntu-lv. Without --size, use the
VG's free space except for a 1 GiB safety reserve. The default is a dry run.

  --size SIZE  add an LVM size such as 50G or 512MiB
  --yes      perform the online lvextend -r operation
EOF
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

apply=false
size=""
while (($#)); do
  case "$1" in
    --yes)
      apply=true
      shift
      ;;
    --size)
      (($# >= 2)) || die "--size requires a value"
      size=$2
      shift 2
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

[[ $(id -u) == 0 ]] || die "run this script as root"
for command in findmnt vgs lvs lvextend df; do
  command -v "$command" >/dev/null || die "required command not found: $command"
done

read -r root_source root_fstype < <(findmnt -n -o SOURCE,FSTYPE /)
[[ $root_fstype == ext4 ]] || die "root filesystem must be ext4 (found: $root_fstype)"
case "$root_source" in
  /dev/ubuntu-vg/ubuntu-lv | /dev/mapper/ubuntu--vg-ubuntu--lv) ;;
  *) die "root must be ${VG_NAME}/${LV_NAME} (found: $root_source)" ;;
esac

read -r actual_vg actual_lv < <(
  lvs --noheadings --options vg_name,lv_name "$LV_PATH" | awk '{$1=$1};1'
)
[[ $actual_vg == "$VG_NAME" && $actual_lv == "$LV_NAME" ]] ||
  die "$LV_PATH is not ${VG_NAME}/${LV_NAME}"

if [[ -n $size ]]; then
  [[ $size =~ ^[0-9]+([.][0-9]+)?([KMGTPE]i?B?|[bB])$ ]] ||
    die "invalid --size '$size' (examples: 50G, 512MiB)"
  extension="+$size"
  reason="requested size"
else
  free_bytes=$(vgs --noheadings --units b --nosuffix --options vg_free "$VG_NAME" |
    awk '{$1=$1};1')
  [[ $free_bytes =~ ^[0-9]+$ ]] || die "could not determine free bytes in $VG_NAME"
  ((free_bytes > SAFETY_BYTES)) ||
    die "$VG_NAME has no growable space beyond the 1 GiB safety reserve"
  extension="+$((free_bytes - SAFETY_BYTES))B"
  reason="all VG free space except the 1 GiB safety reserve"
fi

echo "=== Before ==="
vgs "$VG_NAME"
lvs "$LV_PATH"
df -hT /
echo
echo "Target: $LV_PATH"
echo "Extension: $extension ($reason)"

if [[ $apply == false ]]; then
  echo "DRY RUN: no changes made; rerun with --yes to execute."
else
  lvextend -r -L "$extension" "$LV_PATH"
fi

echo
echo "=== After ==="
vgs "$VG_NAME"
lvs "$LV_PATH"
df -hT /
