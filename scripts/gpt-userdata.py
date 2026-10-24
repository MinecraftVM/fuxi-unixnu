#!/usr/bin/env python3
"""Read the GPT of a Xiaomi 13 and print the userdata geometry board.toml needs.

```sh
adb shell su -c 'dd if=/dev/block/sda of=/sdcard/gpt.bin bs=4096 count=34'
adb pull /sdcard/gpt.bin
python3 scripts/gpt-userdata.py gpt.bin
```

The header and the entry array are enough; the file may also be a full disk
image. Qualcomm's UFS uses 4096-byte logical sectors on this platform, so the
header sits at 4096, not at 512 -- the script finds it and reports which.

The output prints the [userdata] block for board.toml, the size of the
partition in GiB (the 256 GB and 512 GB fuxi models differ) and every other
partition, which is useful when the port gains a native UFS root.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import uuid
import zlib
from pathlib import Path

SIGNATURE = b"EFI PART"
HEADER_MIN = 92
NAME_BYTES = 72


class GptError(RuntimeError):
    pass


def find_header(data: bytes):
    for sector in (512, 4096, 1024, 2048):
        offset = sector
        if data[offset:offset + 8] == SIGNATURE:
            return offset, sector
    raise GptError("no GPT header at LBA 1 for 512/1024/2048/4096-byte sectors")


def parse_header(data: bytes, offset: int):
    (revision, header_size) = struct.unpack_from("<II", data, offset + 8)
    if header_size < HEADER_MIN or header_size > 512:
        raise GptError(f"implausible header size {header_size}")
    header = bytearray(data[offset:offset + header_size])
    stored_crc = struct.unpack_from("<I", header, 16)[0]
    header[16:20] = b"\0\0\0\0"
    actual_crc = zlib.crc32(bytes(header)) & 0xFFFFFFFF
    my_lba, alternate_lba, first_usable, last_usable = struct.unpack_from("<QQQQ", data, offset + 24)
    entry_lba, count, entry_size, entries_crc = struct.unpack_from("<QIII", data, offset + 72)
    return {
        "revision": f"{revision >> 16}.{revision & 0xFFFF}",
        "header_size": header_size,
        "header_crc_ok": stored_crc == actual_crc,
        "my_lba": my_lba,
        "alternate_lba": alternate_lba,
        "first_usable_lba": first_usable,
        "last_usable_lba": last_usable,
        "disk_guid": str(uuid.UUID(bytes_le=bytes(data[offset + 56:offset + 72]))),
        "entries_lba": entry_lba,
        "entries": count,
        "entry_size": entry_size,
        "entries_crc": entries_crc,
    }


def parse_entries(data: bytes, header: dict, sector: int):
    offset = header["entries_lba"] * sector
    size = header["entries"] * header["entry_size"]
    blob = data[offset:offset + size]
    if len(blob) != size:
        raise GptError(f"the file ends inside the entry array ({len(blob)} of {size} bytes); "
                       f"dump at least {header['entries_lba'] + header['entries'] * header['entry_size'] // sector + 1} sectors")
    if header["entry_size"] < 128:
        raise GptError(f"entry size {header['entry_size']} is too small")
    crc = zlib.crc32(blob) & 0xFFFFFFFF
    partitions = []
    for index in range(header["entries"]):
        entry = blob[index * header["entry_size"]:(index + 1) * header["entry_size"]]
        type_guid = uuid.UUID(bytes_le=entry[0:16])
        if type_guid.int == 0:
            continue
        unique_guid = uuid.UUID(bytes_le=entry[16:32])
        first, last, attrs = struct.unpack_from("<QQQ", entry, 32)
        name = entry[56:56 + NAME_BYTES].decode("utf-16-le", "replace").split("\0", 1)[0]
        partitions.append({"index": index + 1, "name": name, "type": str(type_guid),
                           "uuid": str(unique_guid), "first_lba": first, "last_lba": last,
                           "sectors": last - first + 1, "attributes": attrs})
    return partitions, crc == header["entries_crc"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("image", help="raw GPT head dump or a full disk image")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--sector", type=int, default=0, help="force the logical sector size")
    args = parser.parse_args()

    data = Path(args.image).read_bytes()
    if args.sector:
        offset, sector = args.sector, args.sector
        if data[offset:offset + 8] != SIGNATURE:
            raise GptError(f"no GPT header at LBA 1 with {sector}-byte sectors")
    else:
        offset, sector = find_header(data)
    header = parse_header(data, offset)
    partitions, entries_crc_ok = parse_entries(data, header, sector)

    userdata = next((p for p in partitions if p["name"] == "userdata"), None)
    report = {
        "sector_bytes": sector,
        "header_crc_ok": header["header_crc_ok"],
        "entries_crc_ok": entries_crc_ok,
        "disk_guid": header["disk_guid"],
        "userdata": userdata,
        "partitions": partitions,
    }
    if args.json:
        print(json.dumps(report, indent=2))
        return 0 if userdata else 1

    print(f"sector size      {sector}")
    print(f"header crc       {'ok' if header['header_crc_ok'] else 'MISMATCH'}")
    print(f"entries crc      {'ok' if entries_crc_ok else 'MISMATCH'}")
    print(f"disk guid        {header['disk_guid']}")
    print(f"partitions       {len(partitions)}")
    print()
    if not userdata:
        print("no partition named 'userdata': this dump is not the fuxi LUN 0 GPT", file=sys.stderr)
        return 1
    gib = userdata["sectors"] * sector / (1024 ** 3)
    print("[userdata] for board.toml:")
    print(f"first_lba = {userdata['first_lba']:#x}")
    print(f"last_lba  = {userdata['last_lba']:#x}")
    print(f"lba_bytes = {sector}")
    print(f"# {gib:.1f} GiB, {userdata['sectors']} sectors")
    print()
    print(f"{'#':>3}  {'name':<28} {'first_lba':>12} {'last_lba':>12} {'size':>9}")
    for p in partitions:
        size = p["sectors"] * sector / (1024 ** 3)
        print(f"{p['index']:>3}  {p['name']:<28} {p['first_lba']:>12} {p['last_lba']:>12} {size:>7.1f} G")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except GptError as error:
        print(f"gpt-userdata: error: {error}", file=sys.stderr)
        sys.exit(1)
