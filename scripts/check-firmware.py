#!/usr/bin/env python3
"""Check that apply-fixes left the expected markers behind.

The fix script edits a moving upstream; an anchor that silently stops matching
would only show up on the phone. This runs in the Ubuntu preflight, right after
apply-fixes, and fails the run when a marker is missing.

    check-firmware.py --uefi ../uefi-fuxi --device ../unixnu-fuxi
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uefi", required=True, help="uefi-fuxi checkout after apply-fixes")
    parser.add_argument("--device", required=True, help="device repository after apply-fixes")
    args = parser.parse_args()

    uefi = Path(args.uefi).resolve()
    device = Path(args.device).resolve()
    driver = uefi / "Platforms/Xiaomi/fuxiPkg/Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    inf = driver.with_suffix(".inf")
    dsc = uefi / "Platforms/Xiaomi/fuxiPkg/fuxi.dsc"
    dxe_inc = uefi / "Platforms/Xiaomi/fuxiPkg/Include/DXE.inc"
    board = device / "board.toml"

    problems = []

    def read(path: Path) -> str:
        if not path.is_file():
            problems.append(f"missing file: {path}")
            return ""
        return path.read_text(errors="replace")

    driver_text = read(driver)
    for marker in ("FUXIRAMDISK: hint miss", "label scan", "FUXIRAMDISK: payload at"):
        if marker not in driver_text:
            problems.append(f"{driver.name}: marker {marker!r} missing (apply-fixes --discover did not apply)")

    if "DebugLib" not in read(inf):
        problems.append(f"{inf.name}: DebugLib is not linked")

    if "FuxiRamDiskDxe.inf" not in read(dsc):
        problems.append(f"{dsc.name}: FuxiRamDiskDxe.inf is not built")
    if "FuxiRamDiskDxe.inf" not in read(dxe_inc):
        problems.append(f"{dxe_inc.name}: FuxiRamDiskDxe.inf is not in the firmware volume")

    board_text = read(board)
    if "splash_region" in board_text:
        problems.append(f"{board.name}: the stock splash node is still declared (the ABL may power the panel off)")

    if problems:
        for problem in problems:
            print(f"  error: {problem}", file=sys.stderr)
        return 1
    print("firmware patches: all markers present")
    return 0


if __name__ == "__main__":
    sys.exit(main())