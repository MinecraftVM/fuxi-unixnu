#!/usr/bin/env python3
"""Pack the Xiaomi 13 (fuxi) boot image that the stock ABL accepts.

Layout written here (Android boot image, header v3):

    0x0000  header v3, 1580 bytes, zero padded to 4096
    0x1000  kernel field = gzip(BootShim + UEFI firmware volume) + fuxi device tree
    ...     ramdisk field = the UniXNU payload FAT volume

The ABL decompresses the gzip stream and jumps to BootShim; BootShim copies the
firmware volume that follows it to FD_BASE and branches there. The device tree
sits right behind the gzip stream, which is where BootShim and the firmware look
for it.

"unixnu bootimg" writes a v0 image and verifies it as v0. The fuxi ABL rejects
that container, so this script packs v3 with the port's own mkbootimg_v3.py and
verifies the result independently.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import zlib
from pathlib import Path

PAGE = 4096
HEADER_SIZE = 1580
HEADER_VERSION = 3
BOOT_MAGIC = b"ANDROID!"
FDT_MAGIC = b"\xd0\x0d\xfe\xed"


class PackError(RuntimeError):
    pass


def run(command, **kwargs):
    result = subprocess.run([str(part) for part in command], text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise PackError(f"{Path(str(command[0])).name} failed ({result.returncode}):\n"
                        f"{(result.stderr or result.stdout or '').strip()}")
    return result


def find_tool(name, hint=""):
    search = [os.environ.get("PATH", "")]
    for prefix in ("/opt/homebrew", "/usr/local"):
        search += [f"{prefix}/opt/llvm/bin", f"{prefix}/opt/lld/bin", f"{prefix}/bin"]
    for directory in search:
        found = shutil.which(name, path=directory)
        if found:
            return found
    raise PackError(f"{name} not found on PATH" + (f" ({hint})" if hint else ""))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def describe_ramdisk(ramdisk: bytes) -> str:
    """Say what the payload volume looks like; UniXNU needs the FAT config on it."""
    if not ramdisk:
        return "empty"
    if len(ramdisk) >= 512 and ramdisk[510:512] == b"\x55\xaa":
        bytes_per_sector = struct.unpack_from("<H", ramdisk, 11)[0]
        total16 = struct.unpack_from("<H", ramdisk, 19)[0]
        total32 = struct.unpack_from("<I", ramdisk, 32)[0]
        sectors = total16 or total32
        label = ramdisk[0x47:0x52].split(b"\0")[0].decode("latin1", "replace").rstrip()
        return (f"FAT label={label!r} bytes_per_sector={bytes_per_sector} "
                f"sectors={sectors} image_bytes={len(ramdisk)}")
    return f"not a FAT volume ({len(ramdisk)} bytes)"


def uefi_config(uefi, name):
    path = Path(uefi) / "Resources" / "Configs" / f"{name}.toml"
    if not path.is_file():
        raise PackError(f"{path} not found: --uefi must be the Mu-Silicium checkout and --config its device name")
    return tomllib.loads(path.read_text())


def build_shim(uefi, base, size, requires_header, scratch):
    """Compile BootShim.S and check the header and _Payload contract the packer relies on."""
    source = Path(uefi) / "BootShim" / "BootShim.S"
    if not source.is_file():
        raise PackError(f"{source} not found: --uefi must be a Mu-Silicium checkout")
    clang = find_tool("clang", "brew install llvm or the Xcode command line tools")
    objcopy = find_tool("llvm-objcopy", "brew install llvm")
    nm = find_tool("llvm-nm", "brew install llvm")
    obj, binary = Path(scratch) / "shim.o", Path(scratch) / "shim.bin"
    run([clang, "--target=aarch64-none-elf", "-c", f"-DFD_BASE={base:#x}", f"-DFD_SIZE={size:#x}",
         f"-DREQUIRES_KERNEL_HEADER={int(requires_header)}", source, "-o", obj])
    run([objcopy, "-O", "binary", obj, binary])
    shim = binary.read_bytes()
    symbols = run([nm, "-n", obj]).stdout
    offset = next((int(line.split()[0], 16) for line in symbols.splitlines() if line.endswith(" _Payload")), None)
    if offset != len(shim):
        raise PackError(f"a literal pool or padding separates _Payload from the firmware volume "
                        f"(_Payload {offset:#x}, shim {len(shim):#x})")
    if requires_header:
        if shim[56:60] != b"ARM\x64":
            raise PackError("BootShim has no ARM64 image header magic at offset 56")
        got = struct.unpack_from("<QQ", shim, 8)
        if got != (base, len(shim) + size):
            raise PackError(f"the ARM64 header says base={got[0]:#x} size={got[1]:#x}, "
                            f"expected {base:#x} and {len(shim) + size:#x}")
    return shim


def pack(uefi, gzip_stream, dtb, ramdisk, out):
    """Write the v3 container with the port's verified packer."""
    packer = Path(uefi) / "tools" / "mkbootimg_v3.py"
    if not packer.is_file():
        raise PackError(f"{packer} not found: the pinned uefi-fuxi commit carries the v3 packer")
    command = [sys.executable, packer, "pack", "--kernel", gzip_stream, "--dtb", dtb, "--out", out]
    if ramdisk:
        command += ["--ramdisk", ramdisk]
    result = run(command)
    return result.stdout.strip()


def verify(image, combined, gzip_stream, dtb, ramdisk, ramdisk_path):
    """Unpack the image and compare every part with its input."""
    data = Path(image).read_bytes()
    magic, kernel_size, ramdisk_size, _os, header_size, *rest = struct.unpack_from("<8sIIII8I", data, 0)
    header_version = struct.unpack_from("<I", data, 40)[0]
    if magic != BOOT_MAGIC:
        raise PackError("not an ANDROID! image")
    if header_version != HEADER_VERSION:
        raise PackError(f"header version {header_version}, expected {HEADER_VERSION}")
    if header_size != HEADER_SIZE:
        raise PackError(f"header size {header_size}, expected {HEADER_SIZE}")
    kernel_at = (header_size + PAGE - 1) // PAGE * PAGE
    field = data[kernel_at:kernel_at + kernel_size]
    if field != gzip_stream + dtb:
        raise PackError("the kernel field is not gzip(BootShim + firmware) followed by the device tree")
    decompressed = zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(gzip_stream)
    if decompressed != combined:
        raise PackError("the gzip stream does not decompress to BootShim + firmware")
    ram_at = kernel_at + kernel_size
    if data[ram_at:ram_at + ramdisk_size] != ramdisk:
        raise PackError("the ramdisk field differs from the payload")
    if ramdisk_path and len(ramdisk) != Path(ramdisk_path).stat().st_size:
        raise PackError("the ramdisk size differs from the file on disk")
    return {"header_version": header_version, "header_size": header_size, "kernel_at": hex(kernel_at),
            "kernel_size": kernel_size, "ramdisk_size": ramdisk_size, "image_bytes": len(data)}


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--uefi", required=True, help="Mu-Silicium checkout (uefi-fuxi)")
    parser.add_argument("--config", default="fuxi", help="Resources/Configs/<name>.toml (default: fuxi)")
    parser.add_argument("--fd", required=True, help="built firmware volume (SILICIUM_UEFI.fd)")
    parser.add_argument("--dtb", required=True, help="compiled device tree (Resources/DTBs/fuxi.dtb)")
    parser.add_argument("--ramdisk", default="", help="UniXNU payload image; omitted means an empty ramdisk")
    parser.add_argument("--gzip-ramdisk", action="store_true",
                        help="gzip the ramdisk first; some bootloaders only decompress a compressed ramdisk")
    parser.add_argument("--out", default="boot.img", help="output boot image")
    parser.add_argument("--fd-base", default="", help="override [uefi_fd] base from the device config")
    parser.add_argument("--fd-size", default="", help="override [uefi_fd] size from the device config")
    args = parser.parse_args()

    uefi = Path(args.uefi).resolve()
    config = uefi_config(uefi, args.config)
    fd_config = config.get("uefi_fd", {})
    base = int(args.fd_base or fd_config.get("base", 0))
    size = int(args.fd_size or fd_config.get("size", 0))
    if not base or not size:
        raise PackError("the device config has no [uefi_fd] base and size")
    requires_header = config.get("boot_shim", {}).get("requires_kernel_header", True)

    firmware = Path(args.fd).read_bytes()
    if len(firmware) != size:
        raise PackError(f"the firmware volume is {len(firmware):#x} bytes; [uefi_fd] size is {size:#x}")
    dtb = Path(args.dtb).read_bytes()
    if dtb[:4] != FDT_MAGIC or struct.unpack_from(">I", dtb, 4)[0] != len(dtb):
        raise PackError(f"{args.dtb} is not a complete flattened device tree")
    ramdisk = Path(args.ramdisk).read_bytes() if args.ramdisk else b""
    ramdisk_kind = describe_ramdisk(ramdisk)
    if ramdisk and args.gzip_ramdisk:
        raw_size = len(ramdisk)
        ramdisk = gzip.compress(ramdisk, compresslevel=9, mtime=0)
        ramdisk_kind = f"{ramdisk_kind}, gzipped {raw_size} -> {len(ramdisk)} bytes"
        print(f"gzip ramdisk: {raw_size} -> {len(ramdisk)} bytes")
    if ramdisk and not ramdisk_kind.startswith("FAT"):
        print(f"warning: the ramdisk is {ramdisk_kind}; UniXNU expects its FAT payload volume here")

    with tempfile.TemporaryDirectory(prefix="fuxi-bootimg-") as scratch:
        shim = build_shim(uefi, base, size, requires_header, scratch)
        combined = shim + firmware
        gzip_stream = gzip.compress(combined, mtime=0)
        gzip_path = Path(scratch) / "kernel.gz"
        gzip_path.write_bytes(gzip_stream)
        ramdisk_path = args.ramdisk
        if ramdisk and args.gzip_ramdisk:
            ramdisk_path = str(Path(scratch) / "ramdisk.gz")
            Path(ramdisk_path).write_bytes(ramdisk)
        pack_output = pack(uefi, gzip_path, str(Path(args.dtb).resolve()), ramdisk_path, args.out)
        report = verify(args.out, combined, gzip_stream, dtb, ramdisk, ramdisk_path)

    report.update({
        "fd_base": hex(base), "fd_size": hex(size), "firmware_bytes": len(firmware),
        "shim_bytes": len(shim), "gzip_bytes": len(gzip_stream), "dtb_bytes": len(dtb),
        "fd_sha256": sha256(firmware), "image_sha256": sha256(Path(args.out).read_bytes()),
        "ramdisk_kind": ramdisk_kind,
        "ramdisk_gzip": bool(args.gzip_ramdisk),
        "config": args.config, "packer": pack_output,
    })
    Path(args.out).with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"boot image: {args.out} ({report['image_bytes']} bytes)")
    print(f"  header v3, kernel at {report['kernel_at']}: gzip {len(gzip_stream)} + dtb {len(dtb)} "
          f"= {report['kernel_size']}")
    print(f"  firmware: {len(firmware):#x} bytes at {base:#x}; shim {len(shim)} bytes")
    print(f"  ramdisk (payload): {len(ramdisk)} bytes, {ramdisk_kind}")
    print(pack_output and "\n" + pack_output)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PackError as error:
        print(f"pack-bootimg-v3: error: {error}", file=sys.stderr)
        sys.exit(1)