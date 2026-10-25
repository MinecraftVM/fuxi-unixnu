#!/usr/bin/env python3
"""Patch an upstream fuxi loader payload: root extents in the ADT and the cmdline.

The upstream loader (bangprovn/ios-fuxi) embeds a prebuilt XNU payload with a
generated Apple Device Tree. The tree lives at adt_off/adt_size in the payload
header and carries /chosen "fuxi-root-extents" (u64 triples {file block,
partition block, blocks}, 4 KiB units) and "fuxi-root-blocks".

Usage:
  patch-upstream-payload.py IN OUT --root-bytes N [--extent FILE,PART,COUNT ...]
                              [--cmdline STRING]
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / ".." / ".." / ".scratch" / "ios-fuxi" / "tools"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source")
    parser.add_argument("out")
    parser.add_argument("--root-bytes", type=int, required=True)
    parser.add_argument("--extent", action="append", default=[],
                        help="file,partition,count in 4 KiB blocks (repeatable)")
    parser.add_argument("--cmdline")
    args = parser.parse_args()

    import adt  # from bangprovn/ios-fuxi tools/

    data = bytearray(Path(args.source).read_bytes())
    if data[:8] != b"SWEETXNU":
        print("not a SWEETXNU payload", file=sys.stderr)
        return 1
    adt_off, adt_size = struct.unpack_from("<2Q", data, 0x20)

    root, _ = adt.parse(bytes(data[adt_off:adt_off + adt_size]))
    chosen = next(n for n in root.children if n.name == "chosen")
    blocks = args.root_bytes // 4096

    extents = []
    for spec in args.extent:
        file, part, count = (int(v, 0) for v in spec.split(","))
        extents.append((file, part, count))
    if not extents:
        extents = [(0, 0, blocks)]

    words = []
    for file, part, count in extents:
        words += [file, part, count]
    props = [(k, f, v) for (k, f, v) in chosen.props
             if k not in ("fuxi-root-extents", "fuxi-root-blocks")]
    props.append(("fuxi-root-extents", 0, struct.pack(f"<{len(words)}Q", *words)))
    props.append(("fuxi-root-blocks", 0, struct.pack("<Q", blocks)))
    chosen.props = props

    blob = adt.serialize(root)
    if len(blob) > 0x10000:
        print("ADT too large", file=sys.stderr)
        return 1
    if len(data) < adt_off + len(blob):
        data.extend(b"\x00" * (adt_off + len(blob) - len(data)))
    data[adt_off:adt_off + len(blob)] = blob
    struct.pack_into("<Q", data, 0x28, len(blob))

    if args.cmdline:
        cmd = args.cmdline.encode()
        if len(cmd) >= 608:
            print("cmdline too long", file=sys.stderr)
            return 1
        data[0x48:0x48 + 608] = cmd + b"\x00" * (608 - len(cmd))

    Path(args.out).write_bytes(bytes(data))
    print(f"{args.out}: {len(data)} bytes, adt {len(blob)}, root {blocks} blocks")
    return 0


if __name__ == "__main__":
    sys.exit(main())
