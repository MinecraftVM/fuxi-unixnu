#!/usr/bin/env python3
"""Insert a one-off low-memory read probe into FuxiRamDiskDxe.

The SEC hint is 0x5059D4 on fuxi: the ABL decompressed the boot image into the
first few MiB, which the UEFI memory map does not describe. This probe reads a
few candidate addresses directly; it may fault, but each line is printed first,
so the last line on screen shows how far it got.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROBE = r"""  {
    UINT64  Candidates[6];
    UINTN   Index;
    Candidates[0] = 0x00000000005059D4ULL;
    Candidates[1] = 0x00000000002059D4ULL;
    Candidates[2] = 0x0000000000200000ULL;
    Candidates[3] = 0x0000000000300000ULL;
    Candidates[4] = 0x0000000000400000ULL;
    Candidates[5] = 0x0000000000500000ULL;
    DEBUG ((DEBUG_ERROR, "LOWMEM: start\n"));
    for (Index = 0; Index < 6; Index++) {
      UINT32  *Words;
      DEBUG ((DEBUG_ERROR, "LOWMEM: try %016lx\n", Candidates[Index]));
      Words = (UINT32 *)(UINTN)Candidates[Index];
      DEBUG ((DEBUG_ERROR, "LOWMEM: %016lx %08x %08x %08x %08x\n", Candidates[Index], Words[0], Words[1], Words[2], Words[3]));
    }
    DEBUG ((DEBUG_ERROR, "LOWMEM: done\n"));
  }
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uefi", required=True)
    args = parser.parse_args()
    source = Path(args.uefi).resolve() / "Platforms/Xiaomi/fuxiPkg/Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    text = source.read_text()
    if "LOWMEM: start" in text:
        print("probe already present")
        return 0
    newline = chr(10)
    anchor = '  Print (L"FUXIRAMDISK: driver entry' + chr(92) + 'n");' + newline
    if anchor not in text:
        print("entry Print line not found", file=sys.stderr)
        return 1
    text = text.replace(anchor, anchor + PROBE, 1)
    source.write_text(text)
    print(f"{source}: low-memory probe inserted")
    return 0


if __name__ == "__main__":
    sys.exit(main())