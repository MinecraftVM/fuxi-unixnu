#!/usr/bin/env python3
"""Find an ASCII marker in a raw partition dump and interpret the numbers around it.

Used to confirm the firmware window that the stock ABL expects, from this
phone's own xbl_config partition:

```sh
adb shell su -c 'dd if=/dev/block/by-name/xbl_config_a of=/sdcard/xblcfg.bin'
adb pull /sdcard/xblcfg.bin
python3 scripts/blob-scan.py xblcfg.bin --find "UEFI FD"
```

For every hit it prints the surrounding bytes and tries to read the preceding
two little-endian u32 values as (base, size). That is the pair the upstream
fuxi.toml documents as `0xA7000000, 0x00400000`, and it is what decides
[uefi_fd] in the device config. The port currently uses 0xA7100000/0x300000
(commit ad576f83); this tool is how you check that against your own unit
instead of trusting either comment.
"""
from __future__ import annotations

import argparse
import re
import struct
import sys
from pathlib import Path

BASE_RANGE = (0x80000000, 0xC0000000)
SIZE_RANGE = (0x100000, 0x2000000)


def plausible(base: int, size: int) -> bool:
    return (BASE_RANGE[0] <= base < BASE_RANGE[1] and SIZE_RANGE[0] <= size <= SIZE_RANGE[1]
            and size & (size - 1) == 0)


def context(data: bytes, offset: int, span: int = 96) -> str:
    start = max(0, offset - span)
    end = min(len(data), offset + span)
    lines = []
    for line_start in range(start - start % 16, end, 16):
        chunk = data[line_start:line_start + 16]
        hexpart = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {line_start:08x}  {hexpart:<47}  {text}")
    return "\n".join(lines)


def candidates(data: bytes, offset: int):
    found = []
    for delta in range(4, 96, 4):
        if offset - delta < 0:
            break
        base, size = struct.unpack_from("<II", data, offset - delta)
        if plausible(base, size):
            found.append((delta, base, size))
    text = data[max(0, offset - 96):offset].decode("ascii", "replace")
    for match in re.finditer(r"0x([0-9A-Fa-f]{8})[\s,]+0x([0-9A-Fa-f]{8})", text):
        found.append((None, int(match.group(1), 16), int(match.group(2), 16)))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("blob", help="raw partition dump")
    parser.add_argument("--find", default="UEFI FD", help='ASCII marker (default: "UEFI FD")')
    parser.add_argument("--limit", type=int, default=8, help="maximum hits to print")
    args = parser.parse_args()

    data = Path(args.blob).read_bytes()
    marker = args.find.encode()
    print(f"{args.blob}: {len(data)} bytes, scanning for {marker!r}")
    hits = 0
    start = 0
    while True:
        offset = data.find(marker, start)
        if offset < 0:
            break
        hits += 1
        if hits > args.limit:
            print(f"... more hits suppressed (-hits > {args.limit})")
            break
        print(f"\nhit {hits} at {offset:#x}")
        print(context(data, offset))
        for delta, base, size in candidates(data, offset):
            where = f"{delta} bytes before" if delta else "text nearby"
            print(f"  candidate: base {base:#010x} size {size:#09x} ({size // 1024} KiB, {where})")
        start = offset + 1
    if not hits:
        print("no hit: this dump may not be the partition that carries the config, "
              "or the marker is different on this build")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
