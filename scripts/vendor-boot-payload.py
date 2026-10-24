#!/usr/bin/env python3
"""Analyse a vendor_boot image and optionally append the payload to its ramdisk.

fuxi's ABL loads the vendor_boot ramdisk into the initrd (/chosen points at it) but
ignores the boot image's ramdisk. Appending the payload to the vendor ramdisk is
the remaining way to get it into memory without a storage driver.

The kernel's initramfs unpacker stops at the end of the compressed stream, so the
trailing FAT is ignored by Android; the firmware finds it with the byte-wise label
scan. Flashing vendor_boot is risky: keep the stock image for recovery.

    vendor-boot-payload.py analyze vendor_boot.img
    vendor-boot-payload.py add-payload vendor_boot.img payload.img -o vendor_boot-patched.img
"""
from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

MAGIC = b"VNDRBOOT"
V4_FIELDS = 2176  # header bytes through bootconfig_size


def parse(data: bytes) -> dict:
    if len(data) < V4_FIELDS or data[:8] != MAGIC:
        raise SystemExit("not a vendor_boot v4 image (magic VNDRBOOT missing)")
    version, page_size, kernel_addr, ramdisk_addr, ramdisk_size = struct.unpack_from("<5I", data, 8)
    tags_addr = struct.unpack_from("<I", data, 2076)[0]
    header_size, dtb_size = struct.unpack_from("<2I", data, 2144)
    table_size, entry_num, entry_size, bootconfig_size = struct.unpack_from("<4I", data, 2160)
    if version < 4:
        raise SystemExit(f"header version {version}: only v4 carries the table and bootconfig")
    return {
        "version": version, "page_size": page_size, "header_size": header_size,
        "kernel_addr": hex(kernel_addr), "ramdisk_addr": hex(ramdisk_addr),
        "tags_addr": hex(tags_addr), "vendor_ramdisk_size": ramdisk_size,
        "dtb_size": dtb_size, "vendor_ramdisk_table_size": table_size,
        "table_entries": entry_num, "table_entry_size": entry_size,
        "bootconfig_size": bootconfig_size,
    }


def sections(data: bytes, info: dict) -> dict:
    page = info["page_size"]
    start = (info["header_size"] + page - 1) // page * page
    ramdisk = data[start:start + info["vendor_ramdisk_size"]]
    pos = start + info["vendor_ramdisk_size"]
    pos = (pos + page - 1) // page * page
    dtb = data[pos:pos + info["dtb_size"]]
    pos += info["dtb_size"]
    pos = (pos + page - 1) // page * page
    table = b""
    bootconfig = b""
    if info["version"] >= 4:
        table = data[pos:pos + info["vendor_ramdisk_table_size"]]
        pos += info["vendor_ramdisk_table_size"]
        pos = (pos + page - 1) // page * page
        bootconfig = data[pos:pos + info["bootconfig_size"]]
    return {"ramdisk": ramdisk, "dtb": dtb, "table": table, "bootconfig": bootconfig, "start": start}


def detect(blob: bytes) -> str:
    if blob[:4] == b"\x02\x21\x4c\x18":
        return "lz4 legacy"
    if blob[:2] == b"\x1f\x8b":
        return "gzip"
    if blob[:6] in (b"070701", b"070702"):
        return "cpio"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    a = sub.add_parser("analyze", help="print the header and the ramdisk format")
    a.add_argument("image")
    b = sub.add_parser("add-payload", help="append the payload to the vendor ramdisk and rebuild")
    b.add_argument("image")
    b.add_argument("payload")
    b.add_argument("-o", "--out", required=True)
    args = parser.parse_args()

    data = Path(args.image).read_bytes()
    info = parse(data)
    parts = sections(data, info)
    print("header:", ", ".join(f"{k}={v}" for k, v in info.items()))
    print(f"vendor ramdisk: {len(parts['ramdisk'])} bytes, format {detect(parts['ramdisk'])}")
    print(f"dtb: {len(parts['dtb'])} bytes, table: {len(parts['table'])}, bootconfig: {len(parts['bootconfig'])}")

    if args.command == "analyze":
        return 0

    payload = Path(args.payload).read_bytes()
    ramdisk = parts["ramdisk"] + payload
    page = info["page_size"]

    def pad(blob: bytes) -> bytes:
        return blob + b"\x00" * ((-len(blob)) % page)

    header = bytearray(data[: (info["header_size"] + page - 1) // page * page])
    struct.pack_into("<I", header, 24, len(ramdisk))
    struct.pack_into("<2I", header, 2144, info["header_size"], len(parts["dtb"]))
    struct.pack_into("<4I", header, 2160, len(parts["table"]), info["table_entries"],
                     info["table_entry_size"], len(parts["bootconfig"]))
    out = bytes(header) + pad(ramdisk) + parts["dtb"]
    out += b"\x00" * ((-len(out)) % page) + parts["table"]
    out += b"\x00" * ((-len(out)) % page) + parts["bootconfig"]
    Path(args.out).write_bytes(out)
    print(f"wrote {args.out}: ramdisk {len(parts['ramdisk'])} -> {len(ramdisk)} bytes, total {len(out)}")
    print("recovery: keep the original vendor_boot and flash it back with fastboot if needed")
    return 0


if __name__ == "__main__":
    sys.exit(main())