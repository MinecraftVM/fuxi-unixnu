#!/usr/bin/env python3
"""Check a device defconfig against the hard requirements of the XNU build script.

The kernel step exits immediately on a few combinations, and the message is easy
to miss in a 40-minute log. This checker runs the same rules in a second:

  * CONFIG_XNU_BOOT_PROGRESS needs NATIVE_UFS, OWNER_ROOT_AUDIT,
    NATIVE_VIRTIO_BLOCK and SOURCE_CORECRYPTO14;
  * ArmCpu needs the source crypto provider and a real platform driver;
  * the pthread source link needs the source crypto provider;
  * NativeAES needs the prelinked crypto bootstrap and the source provider;
  * NativeVirtioBlock / NativeRamBlock need NativeAES;
  * the architectural timer needs ArmCpu;
  * the mockfs fixture needs ArmCpu and pthread;
  * the owner-root audit needs the verified-root probe, which needs the
    prelinked source crypto provider.

It also checks that an archive named by CONFIG_XNU_EXTRA_LD_ARCHIVES has a
builder in board.toml [kernel] archives.

Usage:
  check-defconfig.py --device ../unixnu-fuxi --config fuxi_console_defconfig
"""
from __future__ import annotations

import argparse
import re
import sys
import tomllib
from pathlib import Path

TRUE = {"y", "1", "true", "yes"}


def parse_defconfig(path: Path) -> dict[str, str]:
    values = {}
    for number, line in enumerate(path.read_text().splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"CONFIG_XNU_([A-Z0-9_]+)=(.*)", line)
        if not match:
            print(f"warning: {path.name}:{number}: not a CONFIG_XNU_ line: {line}")
            continue
        key, value = match.group(1), match.group(2).strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        values[key] = value
    return values


def enabled(values: dict[str, str], key: str) -> bool:
    return values.get(key, "n").lower() in TRUE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="device repository with board.toml and configs/")
    parser.add_argument("--config", required=True, help="defconfig file name or path")
    args = parser.parse_args()

    device = Path(args.device).resolve()
    config_path = Path(args.config)
    if not config_path.is_file():
        config_path = device / "configs" / args.config
    if not config_path.is_file():
        return fail(f"defconfig not found: {args.config}")
    values = parse_defconfig(config_path)

    problems: list[str] = []

    def need(key: str, why: str) -> None:
        if not enabled(values, key):
            problems.append(f"{key}=1 is required: {why}")

    if enabled(values, "BOOT_PROGRESS"):
        for key in ("NATIVE_UFS", "OWNER_ROOT_AUDIT", "NATIVE_VIRTIO_BLOCK", "SOURCE_CORECRYPTO14"):
            need(key, "BOOT_PROGRESS=1 needs the audited UFS/owner-root, VirtIO and source-crypto build")
    if enabled(values, "ARMCPU"):
        need("SOURCE_CORECRYPTO14", "ArmCpu needs the source crypto provider")
        if not (enabled(values, "ARM_DT_PLATFORM") or enabled(values, "ARMVIRT_PLATFORM")):
            problems.append("ARMCPU=1 requires ARM_DT_PLATFORM or ARMVIRT_PLATFORM")
    if enabled(values, "SOURCE_PTHREAD"):
        need("SOURCE_CORECRYPTO14", "the pthread source link needs the source crypto provider")
    if enabled(values, "NATIVE_AES"):
        need("SOURCE_CORECRYPTO14", "NativeAES needs the source crypto provider")
        if values.get("CRYPTO_BOOTSTRAP") != "prelinked":
            problems.append('NATIVE_AES=1 requires CRYPTO_BOOTSTRAP="prelinked"')
    for key in ("NATIVE_VIRTIO_BLOCK", "NATIVE_RAM_BLOCK"):
        if enabled(values, key):
            need("NATIVE_AES", f"{key}=1 needs the qualified AES prelink baseline")
    if enabled(values, "NATIVE_ARCH_TIMER"):
        need("ARMCPU", "the architectural timer needs ArmCpu")
    if enabled(values, "SOURCE_MOCKFS"):
        need("ARMCPU", "the mockfs fixture needs ArmCpu")
        need("SOURCE_PTHREAD", "the mockfs fixture needs the real pthread components")
    if enabled(values, "VERIFIED_ROOT_PROBE"):
        need("SOURCE_CORECRYPTO14", "the verified-root probe needs the prelinked crypto provider")
        if values.get("CRYPTO_BOOTSTRAP") != "prelinked":
            problems.append('VERIFIED_ROOT_PROBE=1 requires CRYPTO_BOOTSTRAP="prelinked"')
    if enabled(values, "OWNER_ROOT_AUDIT"):
        need("VERIFIED_ROOT_PROBE", "the owner-root audit needs the verified-root probe")

    board = tomllib.loads((device / "board.toml").read_text())
    archives = board.get("kernel", {}).get("archives", [])
    extra = values.get("EXTRA_LD_ARCHIVES", "")
    producers = {
        "build/legacy-stub/libLegacyStub.a": "build-legacy-stub.py",
        "build/boot-progress/libBootProgress.a": "build-boot-progress.py",
        "build/armcpu/libArmCpu.a": "build-arm-driver.py ArmCpu",
        "build/armdt-platform/libArmDTPlatform.a": "build-arm-driver.py ArmDTPlatform",
        "build/corecrypto-2023/libcorecrypto_kernel_noasm-namespaced.a": "build-corecrypto-14.py --namespace",
    }
    for archive, builder in producers.items():
        if archive in extra and not any(entry.startswith(builder) for entry in archives):
            problems.append(f"EXTRA_LD_ARCHIVES names {archive} but [kernel] archives has no \"{builder}\"")

    active = sorted(key for key, value in values.items() if enabled(values, key) or value not in ("n", "0", ""))
    print(f"{config_path.name}: {len(values)} keys, {len(active)} enabled")
    problems = list(dict.fromkeys(problems))
    if problems:
        for problem in problems:
            print(f"  error: {problem}", file=sys.stderr)
        if enabled(values, "BOOT_PROGRESS"):
            print("  hint: a fresh build cannot enable BOOT_PROGRESS; use config=console "
                  "(source-only archives) or seed the prelink closure first", file=sys.stderr)
        return 1
    print("  defconfig is consistent with the XNU build script guards")
    return 0


def fail(message: str) -> int:
    print(f"check-defconfig: error: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())