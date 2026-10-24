#!/usr/bin/env python3
"""Read a failed build's logs and say what is missing.

The macOS workflow uploads unixnu/out/fuxi/logs/*.log next to the boot image.
Download the artifact, unzip it, then:

```sh
python3 scripts/diagnose-logs.py path/to/logs
```

The patterns below are the failure modes this port hits in practice: a missing
static archive, a prelink candidate that only a bootstrap pass can produce, a
tool that a runner image does not ship, or the wrong IOStorageFamily revision.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

RULES = [
    ("missing static archive",
     re.compile(r"(ld|clang).*(library|file) not found|cannot find -l|ld: .*\.a' not found"),
     "The link wants an archive that no step built. The stage-2a defconfig does not set "
     "CONFIG_XNU_BUILD_ARCHIVES, so [kernel] archives in board.toml never run while "
     "BOOT_PROGRESS=1 and XNU_EXTRA_LD_ARCHIVES still reference libBootProgress.a / "
     "libLegacyStub.a. See docs/FULL-MODE.zh.md."),
    ("prelink candidate missing",
     re.compile(r"native-prelink/[^\s']+|No such file or directory: '.*native-prelink"),
     "build-native-aes-bindings.py / audit-storage-interface.py read a prelink candidate "
     "(native-defaults-candidate, native-apfs-load-positive) that is not in any repository. "
     "Produce it with a bootstrap pass in the pre_build input; see docs/FULL-MODE.zh.md."),
    ("IOStorageFamily revision",
     re.compile(r"Unexpected public Darwin 23 IOStorageFamily source"),
     "The feeds.toml pin does not match the revision audit-storage-interface.py asserts. "
     "Restore feeds.toml or override unixnu fetch."),
    ("NativeAES needs the prelink closure",
     re.compile(r"NativeAES currently requires|qualified original AES/crypto prelink"),
     "CONFIG_XNU_NATIVE_AES=y requires a prelink baseline that a fresh checkout does not have; "
     "a console-only configuration has to drop it or supply the candidate. See docs/FULL-MODE.zh.md."),
    ("tool missing",
     re.compile(r"\b(clang|cmake|ninja|unifdef|mformat|mcopy|llvm-objcopy|llvm-nm|ld\.lld|xcodebuild|xcrun|compression_tool)\b[^\n]*not found|command not found: (clang|cmake|ninja|unifdef|xcodebuild|xcrun|compression_tool)"),
     "The runner image or the brew step does not provide a tool the step needs."),
    ("Xcode / SDK",
     re.compile(r"xcode-select|unable to find utility|SDK .* cannot be located|invalid active developer path"),
     "The kernel step needs Xcode and the iphoneos SDK; check xcode-select and the image."),
    ("EDK II memory map / overflow",
     re.compile(r"Fv.*(too large|overflow|does not fit)|GenFv: ERROR"),
     "The firmware volume does not fit [uefi_fd] size. Either the FD layout or the module set changed."),
    ("panic / kernel boot",
     re.compile(r"panic\(cpu|panic: "),
     "Not a build failure: the kernel ran and panicked. The message names the missing provider."),
]

NOISE = re.compile(r"^\s*$|^\s*\+ |^== ")
MAX_HITS = 6


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("logs", nargs="+", help="log files or directories")
    parser.add_argument("--context", type=int, default=2, help="lines around a hit")
    args = parser.parse_args()

    files = []
    for item in args.logs:
        path = Path(item)
        if path.is_dir():
            files += sorted(p for p in path.rglob("*.log") if p.is_file())
        elif path.is_file():
            files.append(path)
    if not files:
        print("no log files found", file=sys.stderr)
        return 1

    found = {}
    for path in files:
        lines = path.read_text(errors="replace").splitlines()
        for name, pattern, hint in RULES:
            hits = [index for index, line in enumerate(lines) if pattern.search(line)]
            for index in hits[:MAX_HITS]:
                found.setdefault(name, []).append((path, index, lines, hint))

    if not found:
        print(f"scanned {len(files)} log files: no known failure pattern")
        print("the last lines of each log are usually the actual error:")
        for path in files[-3:]:
            tail = [line for line in path.read_text(errors="replace").splitlines() if not NOISE.match(line)][-8:]
            print(f"\n--- {path}")
            print("\n".join(tail))
        return 0

    for name, entries in found.items():
        hint = entries[0][3]
        print(f"\n=== {name} ({len(entries)} occurrence(s))")
        print(f"  {hint}")
        for path, index, lines, _ in entries[:3]:
            start, end = max(0, index - args.context), min(len(lines), index + args.context + 1)
            print(f"\n  --- {path}:{index + 1}")
            for offset in range(start, end):
                print(f"    {lines[offset][:200]}")
    return 1


if __name__ == "__main__":
    sys.exit(main())