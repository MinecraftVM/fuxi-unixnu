#!/usr/bin/env python3
"""Pack a raw ARM64 kernel Image (an upstream fuxi loader) into a v3 boot image.

The upstream loader carries its own payload, device tree and GPU firmware, so
the kernel field is just gzip(Image): no ramdisk and no appended DTB.
"""
from __future__ import annotations

import argparse
import gzip
import struct
import sys
from pathlib import Path

PAGE = 4096
HEADER_SIZE = 1580


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True, help="raw ARM64 Image")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    image = Path(args.image).read_bytes()
    if image[56:60] != b"ARM\x64":
        print(f"{args.image}: not an ARM64 Image", file=sys.stderr)
        return 1
    payload = gzip.compress(image, 9)

    header = bytearray(HEADER_SIZE)
    header[0:8] = b"ANDROID!"
    struct.pack_into("<I", header, 8, len(payload))
    struct.pack_into("<I", header, 12, 0)
    struct.pack_into("<I", header, 16, 0)
    struct.pack_into("<I", header, 20, HEADER_SIZE)
    struct.pack_into("<I", header, 40, 3)
    header[44:44 + 8] = b"console=1"

    out = bytearray()
    out += header
    out += b"\x00" * (PAGE - len(header))
    out += payload
    out += b"\x00" * ((-len(payload)) % PAGE)
    Path(args.out).write_bytes(out)
    print(f"{args.out}: {len(out)} bytes (image {len(image)}, gzip {len(payload)})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
