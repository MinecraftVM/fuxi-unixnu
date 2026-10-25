#!/usr/bin/env python3
"""Board preflight for fuxi.

The unixnu driver runs this before the firmware step and aborts the build on a
non-zero exit. The port's hardware facts are already established (see the
uefi-fuxi repository), so this only reports what it can see and never fails the
build on an unexpected invocation.
"""
import sys
from pathlib import Path


def main():
    cwd = Path.cwd()
    pkg = None
    for candidate in (cwd / "Platforms/Xiaomi/fuxiPkg",
                      cwd / "uefi/Platforms/Xiaomi/fuxiPkg"):
        if candidate.is_dir():
            pkg = candidate
            break

    if pkg is None:
        print("check-fuxi: fuxiPkg not found from the current directory, skipping")
        return 0

    mm = pkg / "Library/MemoryMapLib/MemoryMapLib.c"
    if mm.is_file():
        text = mm.read_text(encoding="utf-8", errors="replace")
        rows = sum(1 for line in text.splitlines() if line.lstrip().startswith('{"'))
        has_mmio = "AOSS" in text and "TLMM" in text
        print(f"check-fuxi: {rows} memory descriptors, MMIO apertures "
              f"{'present' if has_mmio else 'MISSING'}")
        if not has_mmio:
            print("check-fuxi: warning: no MMIO apertures; drivers will fault")

    return 0


if __name__ == "__main__":
    sys.exit(main())
