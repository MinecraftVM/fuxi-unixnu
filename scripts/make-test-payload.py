#!/usr/bin/env python3
"""Build a FAT payload volume that runs a plain UEFI application, without XNU.

This is the first on-device test after the firmware menu: it proves that the
boot image ramdisk is found, the FAT volume is mounted, EFI/UniXNU/unixnu.toml
is parsed and an entry actually launches. No macOS and no Apple inputs needed.

```sh
python3 scripts/make-test-payload.py \
  --hello /path/to/HelloPayload.efi \
  --out payload-hello.img
```

The volume is written with mtools, exactly like the payload step of the build
driver (same maker code, same layout), so a failure here is a firmware or
configuration problem, not a packaging difference.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

CONFIG = """version = 1
[boot]
timeout = 5
default = "hello"
[kits]
autoload = false

[[entry]]
id = "hello"
title = "fuxi: UEFI hello (no XNU)"
type = "efi"
path = "Payloads/HelloPayload.efi"

[[entry]]
id = "reset"
title = "Restart"
type = "action"
action = "reset"

[[entry]]
id = "shutdown"
title = "Power off"
type = "action"
action = "shutdown"
"""


def tool(name):
    found = shutil.which(name)
    if not found:
        raise SystemExit(f"{name} not found (brew install mtools, or a mtools build on PATH)")
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hello", required=True, help="HelloPayload.efi from the UniXNU package build")
    parser.add_argument("--out", default="payload-hello.img", help="output FAT image")
    parser.add_argument("--size-mib", type=int, default=64, help="volume size in MiB (default: 64)")
    parser.add_argument("--label", default="FUXIESP", help="FAT label (default: FUXIESP)")
    args = parser.parse_args()

    hello = Path(args.hello)
    if not hello.is_file():
        raise SystemExit(f"{hello} not found: build the UniXNU package first")
    if hello.read_bytes()[:2] != b"MZ":
        raise SystemExit(f"{hello} does not look like a PE/COFF EFI application")

    mformat, mcopy = tool("mformat"), tool("mcopy")
    out = Path(args.out)
    with tempfile.TemporaryDirectory(prefix="fuxi-payload-") as scratch_name:
        stage = Path(scratch_name)
        payloads = stage / "EFI/UniXNU/Payloads"
        payloads.mkdir(parents=True)
        shutil.copyfile(hello, payloads / "HelloPayload.efi")
        (stage / "EFI/UniXNU/unixnu.toml").write_text(CONFIG)
        out.unlink(missing_ok=True)
        env = dict(os.environ, MTOOLS_SKIP_CHECK="1", TZ="UTC")
        subprocess.run([mformat, "-C", "-F", "-T", str(args.size_mib * 2048), "-v", args.label,
                        "-N", "53484e47", "-i", str(out), "::"], check=True, env=env)
        subprocess.run([mcopy, "-i", str(out), "-s", "-Q", "-m", str(stage / "EFI"), "::/"], check=True, env=env)

    listing = subprocess.run([tool("mdir"), "-i", str(out), "-/", "::/"], text=True, capture_output=True)
    print(listing.stdout.strip())
    # mdir shows the 8.3 names, so ask for the long paths directly: mtools resolves them case-insensitively.
    for member in ("::/EFI/UniXNU/unixnu.toml", "::/EFI/UniXNU/Payloads/HelloPayload.efi"):
        probe = subprocess.run([tool("mdir"), "-i", str(out), member], text=True, capture_output=True)
        if probe.returncode:
            raise SystemExit(f"{member} is missing from the volume: {probe.stderr.strip()}")
    print(f"payload: {out} ({out.stat().st_size} bytes, label {args.label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())