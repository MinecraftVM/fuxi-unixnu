#!/usr/bin/env bash
# Assemble the XNU payload and the boot image from a kernel.macho produced by
# the "mode=kernel" workflow job. Everything here runs on Linux; the panic
# screen injection needs ld.lld and llvm-nm on PATH (the LLVM release has them).
set -euo pipefail
trap 'echo "assemble-full.sh: line $LINENO: $BASH_COMMAND failed" >&2' ERR

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
kernel=""
chip=""
uefi="$ROOT/uefi-fuxi"
device="$ROOT/unixnu-fuxi"
unixnu="$ROOT/UniXNU"
fd=""
out="$ROOT/out/boot-fuxi.img"
python=${PYTHON:-python3}
gzip_ramdisk=0

usage() {
  cat <<EOF
usage: $0 --kernel PATH [options]

  --kernel PATH     kernel.macho from the mode=kernel artifact (required)
  --chip-id HEX     1-16 hex digits for --unique-chip-id (default: random)
  --unixnu DIR      UniXNU checkout        (default: $unixnu)
  --device DIR      device repository      (default: $device)
  --uefi DIR        uefi-fuxi checkout     (default: $uefi)
  --fd PATH         firmware volume        (default: build output under --uefi)
  --out PATH        boot image             (default: $out)
  -h, --help        this text
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --kernel) kernel=$2; shift 2 ;;
    --chip-id) chip=$2; shift 2 ;;
    --unixnu) unixnu=$2; shift 2 ;;
    --device) device=$2; shift 2 ;;
    --uefi) uefi=$2; shift 2 ;;
    --fd) fd=$2; shift 2 ;;
    --out) out=$2; shift 2 ;;
    --gzip-ramdisk) gzip_ramdisk=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

[ -n "$kernel" ] || { echo "--kernel is required" >&2; usage >&2; exit 2; }
# The driver runs from the UniXNU directory, so every path handed to it must be absolute.
absolute() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$PWD" "$1" ;; esac; }
kernel=$(absolute "$kernel")
unixnu=$(absolute "$unixnu")
device=$(absolute "$device")
uefi=$(absolute "$uefi")
out=$(absolute "$out")
[ -f "$kernel" ] || { echo "kernel not found: $kernel" >&2; exit 1; }
if [ -z "$chip" ]; then
  if command -v openssl >/dev/null; then chip=$(openssl rand -hex 8); else chip=$(printf '%016x' $((RANDOM * RANDOM + RANDOM))); fi
  echo "assemble-full: random chip id $chip (pass --chip-id to keep a fixed seed)"
fi
if ! [[ "$chip" =~ ^[0-9A-Fa-f]{1,16}$ ]]; then
  echo "--chip-id must be 1-16 hex digits: $chip" >&2
  exit 2
fi

echo "== payload"
(cd "$unixnu" && "$python" ./unixnu build payload -D "$device" --uefi "$uefi" \
  --kernel "$kernel" --unique-chip-id "$chip")

payload="$unixnu/out/fuxi/payload/payload.img"
[ -f "$payload" ] || { echo "payload not produced: $payload" >&2; exit 1; }

if [ -z "$fd" ]; then
  fd="$uefi/Build/fuxiPkg/DEBUG_CLANGPDB/FV/SILICIUM_UEFI.fd"
fi
if [ ! -f "$fd" ]; then
  echo "== firmware (no firmware volume yet)"
  (cd "$unixnu" && "$python" ./unixnu build firmware -D "$device" --uefi "$uefi")
fi
[ -f "$fd" ] || { echo "firmware volume not found: $fd" >&2; exit 1; }

echo "== boot image"
pack_args=(--uefi "$uefi" --fd "$fd" --dtb "$uefi/Resources/DTBs/fuxi.dtb"
           --ramdisk "$payload" --out "$out")
if [ "$gzip_ramdisk" = 1 ]; then pack_args+=(--gzip-ramdisk); fi
"$python" "$ROOT/fuxi-unixnu/scripts/pack-bootimg-v3.py" "${pack_args[@]}"

echo
echo "ready: $out"
sha256sum "$out" 2>/dev/null || shasum -a 256 "$out"
echo "flash: fastboot boot $(basename "$out")"