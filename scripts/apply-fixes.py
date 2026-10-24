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
    "build-legacy-stub.py",
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
    """Compile FuxiProbeDxe into the firmware volume and make it visible without ConOut.

    The driver is in the upstream tree but not referenced by fuxi.dsc/fuxi.fdf. It
    clears the screen, prints the FUXIESP memory-map search result and halts, which
    is the only on-screen diagnostic this board has without a UART.

    Its Print() calls need ConOut (GOP + GraphicsConsole). The platform's SerialPortLib
    is FrameBufferSerialPortLib, so DEBUG output reaches the same panel through the
    "Display Reserved" region alone. A few DEBUG lines are injected around the Print
    calls so the probe still reports entry, hit count and halt when ConOut is broken.
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

    inf = package / "Drivers/FuxiProbeDxe/FuxiProbeDxe.inf"
    text, crlf = read_text(inf)
    changed = False
    if "DebugLib" not in text:
        anchor = "[LibraryClasses]\n"
        if anchor not in text:
            raise SystemExit(f"{inf}: no [LibraryClasses] section")
        text = text.replace(anchor, anchor + "  DebugLib\n", 1)
        changed = True
        print(f"{inf}: DebugLib added")
    if "gEfiGraphicsOutputProtocolGuid" not in text:
        text = text.replace("\\r\\n", "\\n")
        text = text.rstrip("\n") + "\n\n[Protocols]\n  gEfiGraphicsOutputProtocolGuid\n"
        changed = True
        print(f"{inf}: GraphicsOutput protocol added")
    if changed:
        write_text(inf, text, crlf)
    else:
        print(f"{inf}: already patched")

    source = package / "Drivers/FuxiProbeDxe/FuxiProbeDxe.c"
    text, crlf = read_text(source)
    if "FUXIPROBE: entry" in text:
        print(f"{source}: DEBUG lines already present")
        return
    if "#include <Library/DebugLib.h>" not in text:
        text = text.replace("#include <Uefi.h>\n",
                            "#include <Uefi.h>\n#include <Library/DebugLib.h>\n#include <Protocol/GraphicsOutput.h>\n", 1)
    replacements = [
        ("  gBS->Stall (4000000);",
         '  DEBUG ((DEBUG_ERROR, "FUXIPROBE: entry (framebuffer debug)\\n"));\n'
         '  {\n'
         '    EFI_GRAPHICS_OUTPUT_PROTOCOL  *Gop = NULL;\n'
         '    EFI_STATUS                    GopStatus;\n'
         '    GopStatus = gBS->LocateProtocol (&gEfiGraphicsOutputProtocolGuid, NULL, (VOID **)&Gop);\n'
         '    DEBUG ((DEBUG_ERROR, "FUXIPROBE: GOP %r\\n", GopStatus));\n'
         '    if (!EFI_ERROR (GopStatus) && (Gop->Mode != NULL)) {\n'
         '      DEBUG ((DEBUG_ERROR, "FUXIPROBE: GOP fb %lx %ux%u stride %u\\n",\n'
         '              (UINT64)Gop->Mode->FrameBufferBase, Gop->Mode->Info->HorizontalResolution,\n'
         '              Gop->Mode->Info->VerticalResolution, Gop->Mode->Info->PixelsPerScanLine));\n'
         '    }\n'
         '  }\n'
         '  gBS->Stall (4000000);'),
        ("  if (Hits == 0) {",
         '  DEBUG ((DEBUG_ERROR, "FUXIPROBE: %lu hits in %lu regions\\n", (UINT64)Hits, (UINT64)Count));\n  if (Hits == 0) {'),
        ("  CpuDeadLoop ();",
         '  DEBUG ((DEBUG_ERROR, "FUXIPROBE: halted for observation\\n"));\n  CpuDeadLoop ();'),
    ]
    for needle, replacement in replacements:
        if needle not in text:
            if needle == "    CpuDeadLoop ();":
                # Upstream commit 6dc976ee already returns instead of halting.
                print(f"{source}: dead loop already gone (newer upstream)")
                continue
            raise SystemExit(f"{source}: anchor not found: {needle!r}")
        text = text.replace(needle, replacement, 1)
    write_text(source, text, crlf)
    print(f"{source}: DEBUG lines injected")


def enable_ramdisk_debug(uefi: Path) -> None:
    """Make FuxiRamDiskDxe visible on the framebuffer console and stop it halting the boot.

    The driver reports through Print(), i.e. ConOut. On this board ConOut depends on
    the GOP that SimpleFbDxe publishes, while DEBUG goes straight to the "Display
    Reserved" framebuffer through FrameBufferSerialPortLib. When the payload volume is
    not found the driver also ends in CpuDeadLoop(), which looks exactly like a hang
    at "Loading driver ... FuxiRamDiskDxe.efi".

    This patch adds DEBUG lines for the HOB hint, the search result and the volume
    address, and turns the dead loop into an error return so the boot continues to
    BdsDxe and UniXNU (whose own logs are DEBUG too).
    """
    package = uefi / "Platforms/Xiaomi/fuxiPkg"
    source = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    inf = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.inf"
    text, crlf = read_text(source)
    if "FUXIRAMDISK: entry (debug path)" in text:
        print(f"{source}: already patched")
        return
    if "#include <Library/DebugLib.h>" not in text:
        text = text.replace("#include <Uefi.h>\n", "#include <Uefi.h>\n#include <Library/DebugLib.h>\n", 1)
    replacements = [
        ("  gBS->Stall (3000000);",
         '  DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: entry (debug path)\\n"));\n  gBS->Stall (3000000);'),
        (r'    Print (L"SIGNAL: no volume from the hint\n");',
         '    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: no volume; hob=%d base=%lx\\n", mHintSeen ? 1 : 0, mHintValue));\n'
         r'    Print (L"SIGNAL: no volume from the hint\n");'),
        ("    CpuDeadLoop ();",
         '    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: returning NOT_FOUND instead of halting\\n"));\n'
         "    return EFI_NOT_FOUND;"),
        (r'  Print (L"SIGNAL: summary\n");',
         '  DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: payload at %lx, %lu bytes\\n", (UINT64)(UINTN)mVolumeBase, (UINT64)mVolumeBytes));\n'
         r'  Print (L"SIGNAL: summary\n");'),
    ]
    for needle, replacement in replacements:
        if needle not in text:
            if needle == "    CpuDeadLoop ();":
                # Upstream commit 6dc976ee already returns instead of halting.
                print(f"{source}: dead loop already gone (newer upstream)")
                continue
            raise SystemExit(f"{source}: anchor not found: {needle!r}")
        text = text.replace(needle, replacement, 1)
    write_text(source, text, crlf)
    print(f"{source}: DEBUG lines added")

    inf_text, inf_crlf = read_text(inf)
    if "DebugLib" not in inf_text:
        anchor = "[LibraryClasses]\n"
        if anchor not in inf_text:
            raise SystemExit(f"{inf}: no [LibraryClasses] section")
        write_text(inf, inf_text.replace(anchor, anchor + "  DebugLib\n", 1), inf_crlf)
        print(f"{inf}: DebugLib added")
    else:
        print(f"{inf}: DebugLib already present")


def enable_ramdisk_scan(uefi: Path) -> None:
    """Scan upward from the SEC payload hint instead of downward.

    SEC computes the hint as gSecEntryArg1 + 0x300000, i.e. right after the 3 MiB
    firmware volume. The boot image appends the device tree to the kernel field, so
    the ramdisk the ABL loads lies *above* that point, not below it: the upstream
    loop walks 256 MiB downwards and never reaches it. This patch scans 16 MiB
    upwards from the hint and reports through DEBUG, which reaches the panel even
    when ConOut (GOP) does not.
    """
    source = uefi / "Platforms/Xiaomi/fuxiPkg/Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    text, crlf = read_text(source)
    if "scanning up from" in text:
        print(f"{source}: upward scan already applied")
        return
    if "#include <Library/DebugLib.h>" not in text:
        text = text.replace("#include <Uefi.h>\n", "#include <Uefi.h>\n#include <Library/DebugLib.h>\n", 1)
    old_loop = "        for (Probe = Base; (Probe > Base - 0x10000000ULL) && (Probe >= FUXI_BLOCK_SIZE); Probe -= FUXI_BLOCK_SIZE) {"
    new_loop = ('        DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: scanning up from %lx, 16 MiB\\n", Base));\n'
                "        for (Probe = Base; Probe < Base + 0x01000000ULL; Probe += FUXI_BLOCK_SIZE) {")
    if old_loop not in text:
        raise SystemExit(f"{source}: hint loop anchor not found")
    text = text.replace(old_loop, new_loop, 1)
    write_text(source, text, crlf)
    print(f"{source}: hint scan now walks upwards")


def enable_memmap_dump(uefi: Path) -> None:
    """Print the large UEFI memory descriptors to the framebuffer console.

    The payload has not shown up in the ranges tried so far. The memory map says
    where large RAM regions actually are (and which type they carry), which is what
    decides where a 192 MiB ramdisk can sit. Only regions of 16 MiB and up are
    printed so the screen keeps the FUXIRAMDISK lines visible.
    """
    source = uefi / "Platforms/Xiaomi/fuxiPkg/Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    text, crlf = read_text(source)
    if "FUXIMAP:" in text:
        print(f"{source}: memory map dump already present")
        return
    if "#include <Library/DebugLib.h>" not in text:
        text = text.replace("#include <Uefi.h>\n", "#include <Uefi.h>\n#include <Library/DebugLib.h>\n", 1)
    anchor = "  if (mVolumeBase == NULL) {"
    dump = (
        "  {\n"
        "    UINTN  MapSize = 0;\n"
        "    UINTN  MapKey;\n"
        "    UINTN  DescSize;\n"
        "    UINT32 DescVersion;\n"
        "    EFI_MEMORY_DESCRIPTOR  *MemMap;\n"
        "\n"
        "    gBS->GetMemoryMap (&MapSize, NULL, &MapKey, &DescSize, &DescVersion);\n"
        "    MapSize += 32 * DescSize;\n"
        "    MemMap = AllocatePool (MapSize);\n"
        "    if ((MemMap != NULL) &&\n"
        "        !EFI_ERROR (gBS->GetMemoryMap (&MapSize, MemMap, &MapKey, &DescSize, &DescVersion))) {\n"
        "      UINTN  Count = MapSize / DescSize;\n"
        "      UINTN  Index;\n"
        "      UINTN  Shown = 0;\n"
        "\n"
        "      DEBUG ((DEBUG_ERROR, \"FUXIMAP: %lu descriptors, showing >=16 MiB\\n\", (UINT64)Count));\n"
        "      for (Index = 0; Index < Count; Index++) {\n"
        "        EFI_MEMORY_DESCRIPTOR  *Entry = (EFI_MEMORY_DESCRIPTOR *)((UINT8 *)MemMap + Index * DescSize);\n"
        "        if ((Entry->NumberOfPages < (0x1000000 / EFI_PAGE_SIZE)) || (Shown >= 12)) {\n"
        "          continue;\n"
        "        }\n"
        "        DEBUG ((DEBUG_ERROR, \"FUXIMAP: %lx %lu MiB type %u attrs %lx\\n\",\n"
        "                (UINT64)Entry->PhysicalStart, (UINT64)(Entry->NumberOfPages >> 8),\n"
        "                Entry->Type, (UINT64)Entry->Attribute));\n"
        "        Shown++;\n"
        "      }\n"
        "      FreePool (MemMap);\n"
        "    }\n"
        "  }\n"
        "  if (mVolumeBase == NULL) {")
    if anchor not in text:
        raise SystemExit(f"{source}: no-volume anchor not found")
    text = text.replace(anchor, dump, 1)
    write_text(source, text, crlf)
    print(f"{source}: memory map dump added")


def enable_dt_chosen(uefi: Path) -> None:
    """Print the live device tree's /chosen ramdisk range from x0.

    SEC hands the previous stage's x0 to DXE in gSiliciumEntryArgHobGuid; SEC itself
    treats it as the device tree only when the magic and size check pass. If the ABL
    patched /chosen for this boot, linux,initrd-start/end name the ramdisk it loaded,
    which is the address FuxiRamDiskDxe is looking for. The driver prints it through
    DEBUG, which reaches the panel through FrameBufferSerialPortLib.
    """
    package = uefi / "Platforms/Xiaomi/fuxiPkg"
    source = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    inf = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.inf"
    text, crlf = read_text(source)
    if "FUXIDT:" in text:
        print(f"{source}: DT chosen dump already present")
        return
    if "#include <Library/FdtLib.h>" not in text:
        text = text.replace("#include <Library/DebugLib.h>\n",
                            "#include <Library/DebugLib.h>\n#include <Library/FdtLib.h>\n", 1)
    anchor = "  {\n    UINTN  MapSize = 0;"
    block = (
        "  {\n"
        "    EFI_PEI_HOB_POINTERS   Hob;\n"
        "    STATIC CONST EFI_GUID  EntryArgGuid = { 0xDAEE82E6, 0x8090, 0x4E72, { 0xBF, 0xB9, 0x78, 0xC1, 0x3C, 0xD4, 0xE3, 0x59 } };\n"
        "\n"
        "    Hob.Raw = GetHobList ();\n"
        "    while (!END_OF_HOB_LIST (Hob)) {\n"
        "      if ((Hob.Header->HobType == EFI_HOB_TYPE_GUID_EXTENSION) &&\n"
        "          (CompareGuid (&Hob.Guid->Name, &EntryArgGuid))) {\n"
        "        UINT64  Dt = *(UINT64 *)(Hob.Guid + 1);\n"
        "        DEBUG ((DEBUG_ERROR, \"FUXIDT: entry x0=%lx\\n\", Dt));\n"
        "        if ((Dt != 0) && ((Dt & 3) == 0) && (FdtCheckHeader ((CONST VOID *)(UINTN)Dt) == 0)) {\n"
        "          INT32  Node = FdtPathOffset ((CONST VOID *)(UINTN)Dt, \"/chosen\");\n"
        "          DEBUG ((DEBUG_ERROR, \"FUXIDT: fdt ok, chosen=%d\\n\", Node));\n"
        "          if (Node >= 0) {\n"
        "            INT32        Len;\n"
        "            CONST VOID   *P;\n"
        "            UINT64       Value;\n"
        "\n"
        "            Len = 0;\n"
        "            P = FdtGetProp ((CONST VOID *)(UINTN)Dt, Node, \"linux,initrd-start\", &Len);\n"
        "            if (P != NULL) {\n"
        "              Value = (Len >= 8) ? Fdt64ToCpu (*(CONST UINT64 *)P) : (UINT64)Fdt32ToCpu (*(CONST UINT32 *)P);\n"
        "              DEBUG ((DEBUG_ERROR, \"FUXIDT: initrd-start=%lx len=%d\\n\", Value, Len));\n"
        "            }\n"
        "            Len = 0;\n"
        "            P = FdtGetProp ((CONST VOID *)(UINTN)Dt, Node, \"linux,initrd-end\", &Len);\n"
        "            if (P != NULL) {\n"
        "              Value = (Len >= 8) ? Fdt64ToCpu (*(CONST UINT64 *)P) : (UINT64)Fdt32ToCpu (*(CONST UINT32 *)P);\n"
        "              DEBUG ((DEBUG_ERROR, \"FUXIDT: initrd-end=%lx len=%d\\n\", Value, Len));\n"
        "            }\n"
        "            Len = 0;\n"
        "            P = FdtGetProp ((CONST VOID *)(UINTN)Dt, Node, \"bootargs\", &Len);\n"
        "            if (P != NULL) {\n"
        "              DEBUG ((DEBUG_ERROR, \"FUXIDT: bootargs=%.64a\\n\", (CONST CHAR8 *)P));\n"
        "            }\n"
        "          }\n"
        "        } else {\n"
        "          DEBUG ((DEBUG_ERROR, \"FUXIDT: x0 is not an FDT\\n\"));\n"
        "        }\n"
        "      }\n"
        "      Hob.Raw = GET_NEXT_HOB (Hob);\n"
        "    }\n"
        "  }\n"
        "  {\n    UINTN  MapSize = 0;")
    if anchor not in text:
        raise SystemExit(f"{source}: memory map anchor not found")
    text = text.replace(anchor, block, 1)
    write_text(source, text, crlf)
    print(f"{source}: /chosen dump added")

    inf_text, inf_crlf = read_text(inf)
    if "FdtLib" not in inf_text:
        anchor = "[LibraryClasses]\n"
        write_text(inf, inf_text.replace(anchor, anchor + "  FdtLib\n", 1), inf_crlf)
        print(f"{inf}: FdtLib added")



def enable_ramdisk_full(uefi: Path) -> None:
    """Try every safe place a payload volume can be before giving up.

    Three staged attempts, each reported through DEBUG (framebuffer console):
      1. /chosen linux,initrd-start from the device tree the previous stage left in x0;
      2. the platform "Kernel" window 0xA8000000..0xB8000000 (Reserved SYS_MEM in the
         board's own memory table, so reading it cannot fault);
      3. every Conventional/Loader/BootServices descriptor (LocatePayload()).
    This does not replace the SEC hint scan; it runs only when that found nothing.
    """
    package = uefi / "Platforms/Xiaomi/fuxiPkg"
    source = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    inf = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.inf"
    text, crlf = read_text(source)
    if "FUXIRAMDISK: hint miss, trying" in text:
        print(f"{source}: full search already present")
        return
    for header in ("#include <Library/DebugLib.h>", "#include <Library/FdtLib.h>"):
        if header not in text:
            text = text.replace("#include <Uefi.h>\n", "#include <Uefi.h>\n" + header + "\n", 1)
    block = r"""  if (mVolumeBase == NULL) {
    EFI_PEI_HOB_POINTERS   Hob;
    STATIC CONST EFI_GUID  EntryArgGuid = { 0xDAEE82E6, 0x8090, 0x4E72, { 0xBF, 0xB9, 0x78, 0xC1, 0x3C, 0xD4, 0xE3, 0x59 } };

    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: hint miss, trying /chosen and full scans\n"));
    Hob.Raw = GetHobList ();
    while (!END_OF_HOB_LIST (Hob)) {
      if ((Hob.Header->HobType == EFI_HOB_TYPE_GUID_EXTENSION) &&
          (CompareGuid (&Hob.Guid->Name, &EntryArgGuid))) {
        UINT64  Dt = *(UINT64 *)(Hob.Guid + 1);
        if ((Dt != 0) && ((Dt & 3) == 0) && (FdtCheckHeader ((CONST VOID *)(UINTN)Dt) == 0)) {
          INT32  Node = FdtPathOffset ((CONST VOID *)(UINTN)Dt, "/chosen");
          if (Node >= 0) {
            INT32        Len = 0;
            CONST VOID   *Prop = FdtGetProp ((CONST VOID *)(UINTN)Dt, Node, "linux,initrd-start", &Len);
            if (Prop != NULL) {
              UINT64  Start = (Len >= 8) ? Fdt64ToCpu (*(CONST UINT64 *)Prop) : (UINT64)Fdt32ToCpu (*(CONST UINT32 *)Prop);
              UINT64  Total = 0;
              DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: chosen initrd-start=%lx\n", Start));
              {
                UINT64  Probe;
                UINT64  From = (Start > 0x400000ULL) ? (Start - 0x400000ULL) : 0;
                DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: chosen window scan +-4 MiB\n"));
                for (Probe = From; Probe < Start + 0x400000ULL; Probe += FUXI_BLOCK_SIZE) {
                  if (IsPayloadVolume ((UINT8 *)(UINTN)Probe, &Total)) {
                    mVolumeBase  = (UINT8 *)(UINTN)Probe;
                    mVolumeBytes = (UINTN)(Total * FUXI_BLOCK_SIZE);
                    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: payload at %lx near chosen initrd\n", Probe));
                    break;
                  }
                }
              }
            }
          }
        }
      }
      Hob.Raw = GET_NEXT_HOB (Hob);
    }
  }
  if (mVolumeBase == NULL) {
    UINT64  Probe;
    UINT64  Total;
    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: scanning kernel window 0xa8000000..0xb8000000\n"));
    for (Probe = 0xA8000000ULL; Probe < 0xB8000000ULL; Probe += FUXI_BLOCK_SIZE) {
      if (IsPayloadVolume ((UINT8 *)(UINTN)Probe, &Total)) {
        mVolumeBase  = (UINT8 *)(UINTN)Probe;
        mVolumeBytes = (UINTN)(Total * FUXI_BLOCK_SIZE);
        DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: payload at %lx in the kernel window\n", Probe));
        break;
      }
    }
  }
  if (mVolumeBase == NULL) {
    DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: conventional memory scan\n"));
    if (LocatePayload ()) {
      DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: payload at %lx from the memory map\n", (UINT64)(UINTN)mVolumeBase));
    }
  }
  if (mVolumeBase == NULL) {"""
    anchor = "  if (mVolumeBase == NULL) {"
    if anchor not in text:
        raise SystemExit(f"{source}: no-volume anchor missing")
    text = text.replace(anchor, block, 1)
    write_text(source, text, crlf)
    print(f"{source}: full search inserted")

    inf_text, inf_crlf = read_text(inf)
    for lib in ("DebugLib", "FdtLib"):
        if lib not in inf_text:
            anchor = "[LibraryClasses]\n"
            inf_text = inf_text.replace(anchor, anchor + "  " + lib + "\n", 1)
    write_text(inf, inf_text, inf_crlf)



def enable_ramdisk_below_dt(uefi: Path) -> None:
    """Scan 256 MiB below the device tree the previous stage handed over in x0.

    Upstream's own comment says the payload sits just below the device tree. When
    /chosen does not name it (the ABL leaves the stock initrd values in place), the
    tree address from gSiliciumEntryArgHobGuid is the only anchor left. The scan is
    bounded to 256 MiB and every step is reported through DEBUG.
    """
    package = uefi / "Platforms/Xiaomi/fuxiPkg"
    source = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.c"
    inf = package / "Drivers/FuxiRamDiskDxe/FuxiRamDiskDxe.inf"
    text, crlf = read_text(source)
    if "scanning below the device tree" in text:
        print(f"{source}: below-DT scan already present")
        return
    for header in ("#include <Library/DebugLib.h>",):
        if header not in text:
            text = text.replace("#include <Uefi.h>\n", "#include <Uefi.h>\n" + header + "\n", 1)
    anchor = "  if (mVolumeBase == NULL) {"
    block = r"""  if (mVolumeBase == NULL) {
    EFI_PEI_HOB_POINTERS   Hob;
    STATIC CONST EFI_GUID  EntryArgGuid = { 0xDAEE82E6, 0x8090, 0x4E72, { 0xBF, 0xB9, 0x78, 0xC1, 0x3C, 0xD4, 0xE3, 0x59 } };

    Hob.Raw = GetHobList ();
    while (!END_OF_HOB_LIST (Hob)) {
      if ((Hob.Header->HobType == EFI_HOB_TYPE_GUID_EXTENSION) &&
          (CompareGuid (&Hob.Guid->Name, &EntryArgGuid))) {
        UINT64  Dt = *(UINT64 *)(Hob.Guid + 1);
        if (Dt > 0x10000000ULL) {
          UINT64  Probe;
          UINT64  Total;
          DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: scanning below the device tree %lx\n", Dt));
          for (Probe = Dt & ~(UINT64)(FUXI_BLOCK_SIZE - 1); Probe > Dt - 0x10000000ULL; Probe -= FUXI_BLOCK_SIZE) {
            if (IsPayloadVolume ((UINT8 *)(UINTN)Probe, &Total)) {
              mVolumeBase  = (UINT8 *)(UINTN)Probe;
              mVolumeBytes = (UINTN)(Total * FUXI_BLOCK_SIZE);
              DEBUG ((DEBUG_ERROR, "FUXIRAMDISK: payload at %lx below the device tree\n", Probe));
              break;
            }
          }
        }
      }
      Hob.Raw = GET_NEXT_HOB (Hob);
    }
  }
  if (mVolumeBase == NULL) {"""
    if anchor not in text:
        raise SystemExit(f"{source}: no-volume anchor missing")
    text = text.replace(anchor, block, 1)
    write_text(source, text, crlf)
    print(f"{source}: below-DT scan inserted")

    inf_text, inf_crlf = read_text(inf)
    if "DebugLib" not in inf_text:
        anchor = "[LibraryClasses]\n"
        write_text(inf, inf_text.replace(anchor, anchor + "  DebugLib\n", 1), inf_crlf)


def enable_debug_conout(uefi: Path) -> None:
    """Route EDK II DEBUG output to ConOut so the screen shows the boot log.

    The platform inherits BaseDebugLibSerialPort from SiliciumPkg; this board has
    no accessible UART, so a screen log is the only way to see where the firmware
    stops. The platform DSC comes after the included .inc files, so this mapping
    wins.
    """
    dsc = uefi / "Platforms/Xiaomi/fuxiPkg/fuxi.dsc"
    text, crlf = read_text(dsc)
    if "UefiDebugLibConOut" in text:
        print(f"{dsc}: ConOut debug already selected")
        return
    # The SEC module cannot consume the UEFI ConOut library, so the override is
    # per module type: SEC keeps the serial port, DXE prints to the screen.
    block = ("[LibraryClasses.common.DXE_CORE]\n"
             "  DebugLib|MdePkg/Library/UefiDebugLibConOut/UefiDebugLibConOut.inf\n"
             "\n"
             "[LibraryClasses.common.DXE_DRIVER]\n"
             "  DebugLib|MdePkg/Library/UefiDebugLibConOut/UefiDebugLibConOut.inf\n"
             "\n")
    anchor = "[LibraryClasses]\n"
    if anchor not in text:
        raise SystemExit(f"{dsc}: no [LibraryClasses] section")
    text = text.replace(anchor, block + anchor, 1)
    write_text(dsc, text, crlf)
    print(f"{dsc}: DebugLib -> UefiDebugLibConOut")


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
    parser.add_argument("--debug-conout", action="store_true",
                        help="route EDK II DEBUG output to ConOut so the screen shows the boot log")
    parser.add_argument("--ramdisk-debug", action="store_true",
                        help="make FuxiRamDiskDxe log through DEBUG and stop it halting the boot")
    parser.add_argument("--ramdisk-scan", action="store_true",
                        help="scan upwards from the SEC payload hint (the ramdisk sits above the appened tree)")
    parser.add_argument("--memmap", action="store_true",
                        help="print the large memory descriptors to the framebuffer console")
    parser.add_argument("--dtchosen", action="store_true",
                        help="print /chosen linux,initrd-start/end from the DT the ABL handed over in x0")
    parser.add_argument("--ramdisk-full", action="store_true",
                        help="after the hint misses, try /chosen, the kernel window and conventional memory")
    parser.add_argument("--ramdisk-below-dt", action="store_true",
                        help="scan 256 MiB below the device tree handed over in x0")
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
    if args.debug_conout:
        enable_debug_conout(Path(args.uefi))
    if args.ramdisk_debug:
        enable_ramdisk_debug(Path(args.uefi))
    if args.ramdisk_scan:
        enable_ramdisk_scan(Path(args.uefi))
    if args.memmap:
        enable_memmap_dump(Path(args.uefi))
    if args.dtchosen:
        enable_dt_chosen(Path(args.uefi))
    if args.ramdisk_full:
        enable_ramdisk_full(Path(args.uefi))
    if args.ramdisk_below_dt:
        enable_ramdisk_below_dt(Path(args.uefi))

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