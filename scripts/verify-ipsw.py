#!/usr/bin/env python3
"""Check the IPSW inputs a fuxi build pins, without macOS.

The kernel step verifies the arm64 kernelcache against board.toml
[kernel.prelink] kernelcache_sha256. On macOS it uses /usr/bin/compression_tool;
any LZFSE decoder gives the same bytes, so the pin can be checked on Linux too:

```sh
git clone --depth 1 https://github.com/lzfse/lzfse
gcc -O2 -o lzfse_dec lzfse/src/*.c
python3 scripts/verify-ipsw.py --unixnu ../UniXNU --device ../unixnu-fuxi --lzfse ./lzfse_dec
```

It also prints the sha256 of the raw trust-cache members. Those are *not* the
pins in [payload] trust_cache_sha256: that list pins the two-module bundle built
by UniXNU scripts/build-original-system-trust.py, which also needs the device's
own static-trustcache.bin. The note is printed so nobody tunes the wrong value.
"""
from __future__ import annotations

import argparse
import hashlib
import struct
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--unixnu", required=True, help="UniXNU checkout (scripts/unixnu)")
    parser.add_argument("--device", required=True, help="device repository with board.toml")
    parser.add_argument("--lzfse", required=True, help="lzfse decoder binary that supports -decode -i IN -o OUT")
    parser.add_argument("--out", default="", help="cache directory (default: <unixnu>/out)")
    args = parser.parse_args()

    unixnu = Path(args.unixnu).resolve()
    sys.path.insert(0, str(unixnu / "scripts"))
    from unixnu.ipsw import Ipsw, im4p_payload

    board = tomllib.loads((Path(args.device).resolve() / "board.toml").read_text())
    pin = board.get("kernel", {}).get("prelink", {}).get("kernelcache_sha256")
    ctx = SimpleNamespace(board=board, out_root=Path(args.out) if args.out else unixnu / "out",
                          option_text=lambda *a, **k: None)
    source = Ipsw(ctx)
    cache = source.cache
    cache.mkdir(parents=True, exist_ok=True)

    packed, raw = cache / "kernelcache.lzfse", cache / "kernelcache.fat"
    packed.write_bytes(im4p_payload(source.read("KernelCache")))
    result = subprocess.run([args.lzfse, "-decode", "-i", str(packed), "-o", str(raw)],
                            capture_output=True, text=True)
    if result.returncode:
        return fail(f"lzfse failed: {result.stderr.strip() or result.stdout.strip()}")
    data = raw.read_bytes()
    if data[:4] == b"\xca\xfe\xba\xbe":
        count = struct.unpack(">I", data[4:8])[0]
        slices = [struct.unpack(">5I", data[8 + 20 * i:28 + 20 * i]) for i in range(count)]
        arm64 = [s for s in slices if s[0] == 0x0100000C]
        if len(arm64) != 1:
            return fail(f"expected one arm64 slice, found {len(arm64)}")
        _, _, offset, size, _ = arm64[0]
        data = data[offset:offset + size]
    digest = hashlib.sha256(data).hexdigest()
    print(f"kernelcache: {len(data)} bytes, mach-o {data[:4].hex()}, sha256 {digest}")
    if pin and digest != pin:
        return fail(f"kernelcache does not match board.toml pin {pin}")
    print("kernelcache pin: OK" if pin else "kernelcache pin: not set in board.toml")

    pins = set(board.get("payload", {}).get("trust_cache_sha256", []))
    for component in ("StaticTrustCache", "Cryptex1,SystemTrustCache"):
        path = source.extract(component)
        member = hashlib.sha256(path.read_bytes()).hexdigest()
        print(f"{component}: raw member sha256 {member} (bundle pin: {member in pins})")
    print("note: [payload] trust_cache_sha256 pins the two-module bundle from "
          "scripts/build-original-system-trust.py, not these raw members")
    return 0


def fail(message: str) -> int:
    print(f"verify-ipsw: error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
