#!/usr/bin/env bash
# Boot the UniXNU EFI application under QEMU/AAVMF and check that a payload launches.
#
# This is the same check the UniXNU tree runs for QEMU (package/Scripts/qemu-run.py),
# applied to the application this repository builds and to the payload layout that
# boot-uefi-hello.img carries. It does not touch fuxi hardware; it proves the
# EDK II build, the FAT volume layout, the config parser, the menu and the EFI
# entry work before any kernel exists.
#
# Usage:
#   scripts/qemu-smoke.sh \
#     --unixnu ~/unixnu/UniXNU \
#     --efi    ~/unixnu/UniXNU/out/fuxi/package/Build/Out/AARCH64-DEBUG/UniXNU.efi \
#     --hello  ~/unixnu/UniXNU/out/fuxi/package/Build/Out/AARCH64-DEBUG/HelloPayload.efi \
#     --firmware /usr/share/AAVMF/AAVMF_CODE.fd \
#     --vars     /usr/share/AAVMF/AAVMF_VARS.fd \
#     --log out/qemu-hello.log --png out/qemu-hello.png
#
# Pass --payload-image FILE to test the real FAT payload image instead of a
# directory: the ESP then holds only BOOTAA64.EFI and the image is attached as
# a read-only virtio disk. This is how payload-hello.img is verified.
#
# --vars is optional: pass it when your AAVMF package splits code and variables
# (Ubuntu/Debian do; the single-file QEMU_EFI.fd does not).
set -euo pipefail

unixnu=""; efi=""; hello=""; firmware=""; vars=""; log=""; png=""; payload_image=""; timeout=240
while [ $# -gt 0 ]; do
  case "$1" in
    --unixnu) unixnu=$2; shift 2;;
    --efi) efi=$2; shift 2;;
    --hello) hello=$2; shift 2;;
    --firmware) firmware=$2; shift 2;;
    --vars) vars=$2; shift 2;;
    --log) log=$2; shift 2;;
    --png) png=$2; shift 2;;
    --payload-image) payload_image=$2; shift 2;;
    --timeout) timeout=$2; shift 2;;
    -h|--help) sed -n '2,30p' "$0"; exit 0;;
    *) echo "unknown argument: $1" >&2; exit 2;;
  esac
done
for value in unixnu efi hello firmware log; do
  [ -n "${!value}" ] || { echo "--$value is required" >&2; exit 2; }
done
for file in "$unixnu/package/Scripts/qemu-run.py" "$efi" "$hello" "$firmware"; do
  [ -f "$file" ] || { echo "missing: $file" >&2; exit 2; }
done
if [ -n "$payload_image" ]; then
  [ -f "$payload_image" ] || { echo "missing: $payload_image" >&2; exit 2; }
fi
command -v qemu-system-aarch64 >/dev/null || { echo "qemu-system-aarch64 not on PATH" >&2; exit 2; }
command -v python3 >/dev/null || exit 2

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
esp=$work/esp
mkdir -p "$esp/EFI/BOOT"
cp "$efi" "$esp/EFI/BOOT/BOOTAA64.EFI"
if [ -z "$payload_image" ]; then
mkdir -p "$esp/EFI/UniXNU/Payloads"
cp "$hello" "$esp/EFI/UniXNU/Payloads/HelloPayload.efi"
cat > "$esp/EFI/UniXNU/unixnu.toml" <<'TOML'
version = 1
[boot]
timeout = 5
default = "hello"
[kits]
autoload = false

[[entry]]
id = "hello"
title = "fuxi: UEFI hello (no XNU)"
type = "efi"
path = "Payloads/HelloPayload.efi"

[[entry]]
id = "reset"
title = "Restart"
type = "action"
action = "reset"

[[entry]]
id = "shutdown"
title = "Power off"
type = "action"
action = "shutdown"
TOML
fi

runner=$unixnu/package/Scripts/qemu-run.py
if [ -n "$vars" ]; then
  # The upstream runner only uses a variables store when it finds the firmware
  # through its built-in candidate list; an explicit --firmware returns no vars.
  # Patch a copy so an explicit code/vars pair works without touching /usr/share.
  cp "$runner" "$work/qemu-run.py"
  UNIXNU_QEMU_VARS=$vars python3 - "$work/qemu-run.py" "$vars" <<'PY'
import sys
path, vars_path = sys.argv[1], sys.argv[2]
text = open(path).read()
old = "    if code:\n        return Path(code), None"
new = ("    if code:\n"
       "        code = Path(code)\n"
       f"        candidate = Path({vars_path!r})\n"
       "        return code, (candidate if candidate.is_file() else None)")
if old not in text:
    raise SystemExit("qemu-run.py changed: the --firmware branch was not found")
open(path, "w").write(text.replace(old, new, 1))
PY
  runner=$work/qemu-run.py
fi

args=(--firmware "$firmware" --log "$log" --timeout "$timeout" --finish-on-expect
      --expect "UNIXNU: menu: shown"
      --expect "UNIXNU-HELLO: Hello from the UniXNU test payload")
[ -n "$png" ] && args+=(--screenshot "$png")
[ -n "$payload_image" ] && args+=(--disk "$payload_image")
python3 "$runner" "$esp" "${args[@]}"