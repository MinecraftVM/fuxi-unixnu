#!/usr/bin/env python3
"""Apply the fuxi build fixes to the checked-out device and UEFI repositories.

Every edit is idempotent and prints a unified diff; the files keep their CRLF
line endings.

1. device/board.toml, [[bootimg.dtb_node]]

   The port adds /reserved-memory/splash_region to the boot image device tree,
   but the fuxi device tree (Resources/DTBs/fuxi.dtb) already carries it:
   reg = <0x0 0xb8000000 0x0 0x2b00000>. The v3 packer used here appends the
   device tree unmodified, and fdtput cannot create an existing node.

2. Optional: --console (experimental console-only configuration)

   The upstream fuxi_defconfig enables NativeAES and the boot-progress layer.
   Their archives need a prelink candidate no repository carries, and the
   configuration leaves CONFIG_XNU_BUILD_ARCHIVES unset, so a fresh checkout
   never builds the archives the link wants. --console selects a configuration
   built only from source archives: corecrypto, libpthread, ArmCpu and
   ArmDTPlatform. It also rewrites [kernel] defconfig and archives in board.toml
   so the two stay consistent. See docs/FULL-MODE.zh.md.

The firmware layout in uefi/Resources/Configs/<device>.toml is left alone by
default: the values checked in (0xA7100000 / 0x300000) come from upstream
commit ad576f83 and a stale header comment still mentions 4 MiB. --fd-base and
--fd-size override them explicitly for on-device A/B.
"""
from __future__ import annotations

import argparse
import difflib
import re
import shutil
import sys
from pathlib import Path

TABLE = re.compile(r"^\[\[?\s*([^\]]+?)\s*\]\]?\s*(?:#.*)?$")
KEY = re.compile(r"^(\s*)([A-Za-z_][\w-]*)(\s*)=.*$")

CONSOLE_DEFCONFIG = "fuxi_console_defconfig"
PROBE_INF = "fuxiPkg/Drivers/FuxiProbeDxe/FuxiProbeDxe.inf"
CONSOLE_ARCHIVES = [
    "build-corecrypto-14.py",
    "build-corecrypto-14.py --namespace",
    "build-arm-driver.py ArmDTPlatform",
    "build-arm-driver.py ArmCpu",
    "build-libpthread-kernel.py",
]


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
    lines, inside, seen = text.splitlines(True), False, set()
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
    return "".join(kept).rstrip("\n") + "\n"


def set_key(text: str, table: str, key: str, value: str) -> str:
    """Set key = value inside a table, keeping indentation and comments."""
    lines, inside, done = text.splitlines(True), False, False
    for index, line in enumerate(lines):
        header = TABLE.match(line.strip())
        if header:
            inside = header.group(1) == table
            continue
        match = KEY.match(line)
        if inside and match and match.group(2) == key:
            lines[index] = f"{match.group(1)}{key} = {value}\n"
            done = True
    if not done:
        raise SystemExit(f"[{table}] has no {key}: the device config changed")
    return "".join(lines)


def set_archives(text: str, entries: list[str]) -> str:
    """Replace the [kernel] archives array with the given entries."""
    lines, out, inside_kernel, inside_array, done = text.splitlines(True), [], False, False, False
    for line in lines:
        stripped = line.strip()
        header = TABLE.match(stripped)
        if header:
            inside_kernel = header.group(1) == "kernel"
        if not inside_array and inside_kernel and stripped.startswith("archives = ["):
            out.append("archives = [\n")
            out += [f'  "{entry}",\n' for entry in entries]
            out.append("]\n")
            done = True
            inside_array = "]" not in stripped.split("[", 1)[1]
            continue
        if inside_array:
            if stripped.startswith("]"):
                inside_array = False
            continue
        out.append(line)
    if not done:
        raise SystemExit("[kernel] has no archives array: the device config changed")
    return "".join(out)


def enable_probe(uefi: Path) -> None:
    """Compile FuxiProbeDxe into the firmware volume (a separate, diagnostic image).

    The driver is in the upstream tree but not referenced by fuxi.dsc/fuxi.fdf. It
    clears the screen, prints the FUXIESP memory-map search result and halts, which
    is the only on-screen diagnostic this board has without a UART.
    """
    package = uefi / "Platforms/Xiaomi/fuxiPkg"
    dsc, fdf = package / "fuxi.dsc", package / "fuxi.fdf"
    for path in (dsc, fdf):
        if not path.is_file():
            raise SystemExit(f"{path} not found: check --uefi")
    text, crlf = read_text(dsc)
    if PROBE_INF in text:
        print(f"{dsc}: probe already present")
    else:
        anchor = "  fuxiPkg/Drivers/FuxiKeyInputDxe/FuxiKeyInputDxe.inf"
        if anchor not in text:
            raise SystemExit(f"{dsc}: the FuxiKeyInputDxe component anchor is gone")
        write_text(dsc, text.replace(anchor, anchor + "\n  " + PROBE_INF + "\n"), crlf)
        print(f"{dsc}: probe added to [Components]")
    text, crlf = read_text(fdf)
    if PROBE_INF in text:
        print(f"{fdf}: probe already present")
    else:
        anchor = "  !include QcomPkg/Extra.fdf.inc"
        if anchor not in text:
            raise SystemExit(f"{fdf}: the QcomPkg include anchor is gone")
        block = ("  # Bring-up diagnostic: print the FUXIESP search result and halt.\n"
                 f"  INF {PROBE_INF}\n\n")
        write_text(fdf, text.replace(anchor, block + anchor), crlf)
        print(f"{fdf}: probe added to FvMain")


def check_settings(path: Path, text: str) -> None:
    """Report the two build-visible settings instead of silently trusting them."""
    if path.name.endswith(".toml") and "header_version" in text:
        header_version = re.search(r"^header_version\s*=\s*(\S+)", text, re.M)
        if header_version and header_version.group(1) != "3":
            print(f"warning: {path.name} has header_version = {header_version.group(1)}; the fuxi ABL wants 3")
        header = re.search(r"^requires_kernel_header\s*=\s*(\S+)", text, re.M)
        if header and header.group(1).lower() not in ("true", "1"):
            print(f"warning: {path.name} has requires_kernel_header = {header.group(1)}")


def enable_console(board: Path, device: Path, text: str) -> str:
    """Write the console defconfig and make board.toml select it with its own archive list."""
    template = Path(__file__).resolve().parents[1] / "overrides" / CONSOLE_DEFCONFIG
    if not template.is_file():
        raise SystemExit(f"{template} missing: this script is not running from the repository")
    configs = device / "configs"
    if not configs.is_dir():
        raise SystemExit(f"{configs} missing: is --device a unixnu device repository?")
    shutil.copyfile(template, configs / CONSOLE_DEFCONFIG)
    print(f"console: wrote {configs / CONSOLE_DEFCONFIG}")
    text = set_key(text, "kernel", "defconfig", f'"{CONSOLE_DEFCONFIG}"')
    text = set_archives(text, CONSOLE_ARCHIVES)
    print(f"console: [kernel] defconfig = {CONSOLE_DEFCONFIG}, {len(CONSOLE_ARCHIVES)} archives")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uefi", required=True, help="uefi-fuxi checkout")
    parser.add_argument("--device", required=True, help="unixnu-fuxi checkout")
    parser.add_argument("--config", default="fuxi", help="device config name (default: fuxi)")
    parser.add_argument("--fd-base", default="", help="override [uefi_fd] base (opt-in; default: leave the file alone)")
    parser.add_argument("--fd-size", default="", help="override [uefi_fd] size (opt-in; default: leave the file alone)")
    parser.add_argument("--console", action="store_true",
                        help="select the experimental console-only defconfig and archive list")
    parser.add_argument("--probe", action="store_true",
                        help="compile the FuxiProbeDxe bring-up diagnostic into the firmware volume")
    args = parser.parse_args()

    uefi_config = Path(args.uefi) / "Resources" / "Configs" / f"{args.config}.toml"
    device = Path(args.device)
    board = device / "board.toml"
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
    before = board_text
    if "[[bootimg.dtb_node]]" in board_text:
        board_text = drop_table(board_text, "bootimg.dtb_node")
    if args.console:
        board_text = enable_console(board, device, board_text)
    if board_text != before:
        rewrite(board, before, board_text, f"{board} edits", board_crlf)
    else:
        print(f"{board}: already applied")

    if args.probe:
        enable_probe(Path(args.uefi))

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