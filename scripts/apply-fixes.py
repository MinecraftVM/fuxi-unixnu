#!/usr/bin/env python3
"""Apply the fuxi build fixes to the checked-out device and UEFI repositories.

Both edits are idempotent and print a unified diff. They exist because the
upstream port carries values copied from sheng where the fuxi numbers differ.

1. uefi/Resources/Configs/fuxi.toml, [uefi_fd] -- OPT-IN only

   The header of that file still says 0xA7000000 / 0x400000 while the table says
   0xA7100000 / 0x300000. That is intentional: commit ad576f83 ("use the 3 MiB FD
   layout every working device uses") moved the image to 1 MiB reserved plus a
   3 MiB FD inside the stock 4 MiB window, and updated fuxi.fdf and MemoryMapLib
   with it. The stale comment is kept here only as a note. Do not override the
   layout unless you are testing it on hardware: --fd-base and --fd-size change
   it explicitly.

2. device/board.toml, [[bootimg.dtb_node]]

   The port adds /reserved-memory/splash_region to the boot image device tree,
   but the fuxi device tree (Resources/DTBs/fuxi.dtb) already carries it:
   reg = <0x0 0xb8000000 0x0 0x2b00000>. fdtput cannot create an existing node,
   and the v3 packer used here appends the device tree unmodified, so the entry
   is removed instead of being applied.

Both files upstream use CRLF line endings; every write keeps them.
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

TABLE = re.compile(r"^\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")
KEY = re.compile(r"^(\s*)([A-Za-z_][\w-]*)(\s*)=.*$")


def read_text(path: Path) -> tuple[str, bool]:
    """Read UTF-8 text and report whether the file uses CRLF, so writing keeps it."""
    raw = path.read_bytes().decode("utf-8")
    if "\r\n" in raw:
        return raw.replace("\r\n", "\n"), True
    return raw, False


def write_text(path: Path, text: str, crlf: bool) -> None:
    if crlf:
        text = text.replace("\n", "\r\n")
    path.write_bytes(text.encode("utf-8"))


def rewrite(path: Path, before: str, after: str, label: str, crlf: bool) -> bool:
    if before == after:
        print(f"{label}: already applied")
        return False
    diff = list(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                     fromfile=str(path), tofile=str(path)))
    sys.stdout.writelines(diff)
    write_text(path, after, crlf)
    print(f"{label}: written")
    return True


def set_uefi_fd(text: str, base: str, size: str) -> str:
    """Set base and size inside the [uefi_fd] table, keeping every comment."""
    lines = text.splitlines(True)
    inside = False
    seen = set()
    for index, line in enumerate(lines):
        header = TABLE.match(line.strip())
        if header:
            inside = header.group(1) == "uefi_fd"
            continue
        if not inside:
            continue
        match = KEY.match(line)
        if match and match.group(2) in ("base", "size"):
            key = match.group(2)
            lines[index] = f"{key} = {base if key == 'base' else size}\n"
            seen.add(key)
    missing = {"base", "size"} - seen
    if missing:
        raise SystemExit(f"[uefi_fd] has no {', '.join(sorted(missing))}: the device config changed")
    return "".join(lines)


def drop_table(text: str, name: str) -> str:
    """Remove every [[name]] table and its keys."""
    lines, kept, skipping = text.splitlines(True), [], False
    for line in lines:
        header = TABLE.match(line.strip())
        if header:
            skipping = header.group(1) == name
            if skipping:
                continue
        if not skipping:
            kept.append(line)
    result = "".join(kept)
    return result.rstrip("\n") + "\n"


def check_settings(path: Path, text: str) -> None:
    """Report the two build-visible settings instead of silently trusting them."""
    if path.name.endswith(".toml") and "header_version" in text:
        header_version = re.search(r"^header_version\s*=\s*(\S+)", text, re.M)
        if header_version and header_version.group(1) != "3":
            print(f"warning: {path.name} has header_version = {header_version.group(1)}; the fuxi ABL wants 3")
        header = re.search(r"^requires_kernel_header\s*=\s*(\S+)", text, re.M)
        if header and header.group(1).lower() not in ("true", "1"):
            print(f"warning: {path.name} has requires_kernel_header = {header.group(1)}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uefi", required=True, help="uefi-fuxi checkout")
    parser.add_argument("--device", required=True, help="unixnu-fuxi checkout")
    parser.add_argument("--config", default="fuxi", help="device config name (default: fuxi)")
    parser.add_argument("--fd-base", default="", help="override [uefi_fd] base (opt-in; default: leave the file alone)")
    parser.add_argument("--fd-size", default="", help="override [uefi_fd] size (opt-in; default: leave the file alone)")
    args = parser.parse_args()

    uefi_config = Path(args.uefi) / "Resources" / "Configs" / f"{args.config}.toml"
    board = Path(args.device) / "board.toml"
    for path in (uefi_config, board):
        if not path.is_file():
            raise SystemExit(f"{path} not found: check --uefi and --device")

    config_text, config_crlf = read_text(uefi_config)
    check_settings(uefi_config, config_text)
    if args.fd_base and args.fd_size:
        rewrite(uefi_config, config_text, set_uefi_fd(config_text, args.fd_base, args.fd_size),
                f"{uefi_config} [uefi_fd]", config_crlf)
    else:
        print(f"{uefi_config} [uefi_fd]: left as checked in (pass --fd-base and --fd-size to override)")

    board_text, board_crlf = read_text(board)
    if "[[bootimg.dtb_node]]" in board_text:
        rewrite(board, board_text, drop_table(board_text, "bootimg.dtb_node"),
                f"{board} [[bootimg.dtb_node]]", board_crlf)
    else:
        print(f"{board} [[bootimg.dtb_node]]: already applied")

    # Report the layout the firmware and the packer must agree on.
    final_config, _ = read_text(uefi_config)
    base = re.search(r"^base\s*=\s*(0x[0-9A-Fa-f]+)", final_config, re.M)
    size = re.search(r"^size\s*=\s*(0x[0-9A-Fa-f]+)", final_config, re.M)
    print(f"uefi_fd: base {base.group(1) if base else '?'} size {size.group(1) if size else '?'}")
    if args.fd_base and base and int(base.group(1), 16) != int(args.fd_base, 16):
        raise SystemExit("the [uefi_fd] base did not take")
    if args.fd_size and size and int(size.group(1), 16) != int(args.fd_size, 16):
        raise SystemExit("the [uefi_fd] size did not take")
    return 0


if __name__ == "__main__":
    sys.exit(main())