#!/usr/bin/env python3
"""Build the opt-in board diagnostic against this source kernel's actual ABI."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'build/boot-progress'
OUT.mkdir(parents=True, exist_ok=True)
record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/osfmk/DEVELOPMENT/startup.o.json').read_text().strip().rstrip(','))
source = ROOT / 'kits/sheng/BootProgress.c'
obj = OUT / 'BootProgress.o'
command = []
args = record['arguments']
i = 0
while i < len(args):
    arg = args[i]
    if arg == record['file']:
        command.append(str(source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o':
            command += ['-o', str(obj)]
        i += 1
    elif arg == '-Xclang' and args[i+1].startswith('-W'):
        i += 1
    elif arg == '-D' and args[i+1].startswith('XNU_BOOT_PROGRESS='):
        i += 1
    elif not arg.startswith(('-W', '-DXNU_BOOT_PROGRESS=')):
        command.append(arg)
    i += 1
command += ['-DXNU_BOOT_PROGRESS=1', '-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror',
            '-I', str(ROOT / '.cache/xnu-work/osfmk'),
            # Font.h and FrameBuffer.h are vendored into this device package;
            # pointing at a sibling uefi-sheng checkout only works on the
            # author's machine.
            '-I', str(ROOT / 'kits/sheng')]
subprocess.run(command, cwd=record['directory'], check=True, timeout=90)
(OUT / 'compile-command.json').write_text(json.dumps(command,indent=2)+'\n')
fault_source = ROOT / 'kits/sheng/BootFaultProbe.c'
fault_obj = OUT / 'BootFaultProbe.o'
fault_command = [str(fault_source) if arg == str(source) else str(fault_obj) if arg == str(obj) else arg for arg in command]
subprocess.run(fault_command, cwd=record['directory'], check=True, timeout=90)
policy_source = ROOT / 'kits/sheng/BootWatchdogPolicy.c'
policy_obj = OUT / 'BootWatchdogPolicy.o'
policy_record = json.loads((ROOT / 'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/bsd/DEVELOPMENT/kern_exec.o.json').read_text().strip().rstrip(','))
policy_command = []
policy_args = policy_record['arguments']
j = 0
while j < len(policy_args):
    arg = policy_args[j]
    if arg == policy_record['file']:
        policy_command.append(str(policy_source))
    elif arg in ('-o', '-MF', '-MT', '-MJ'):
        if arg == '-o': policy_command += ['-o', str(policy_obj)]
        j += 1
    elif arg == '-Xclang' and policy_args[j+1].startswith('-W'):
        j += 1
    elif not arg.startswith('-W'):
        policy_command.append(arg)
    j += 1
policy_command += ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror']
subprocess.run(policy_command, cwd=policy_record['directory'], check=True, timeout=90)

# Stage 2a: the legacy display hooks come from build-legacy-stub.py (linked via
# CONFIG_XNU_EXTRA_LD_ARCHIVES) and from the ArmDTPlatform archive, which carries
# the same stub; this archive must not define them a third time, because it is
# force loaded and would collide with whichever one the link pulls in.
stub_source = ROOT / 'kits/sheng/LegacyDisplayStub.c'
stub_obj = OUT / 'LegacyDisplayStub.o'
stub_command = [str(stub_source) if arg == str(policy_source)
                else str(stub_obj) if arg == str(policy_obj) else arg
                for arg in policy_command]
subprocess.run(stub_command, cwd=policy_record['directory'], check=True, timeout=90)

def compile_like(record, source, destination, extra):
    """Compile one source with the flags of a recorded XNU compile."""
    command, index = [], 0
    arguments = record['arguments']
    while index < len(arguments):
        value = arguments[index]
        if value == record['file']:
            command.append(str(source))
        elif value == '-o':
            command += ['-o', str(destination)]
            index += 1
        elif value in ('-MF', '-MT', '-MJ'):
            index += 1
        elif value == '-Xclang' and arguments[index + 1].startswith('-W'):
            index += 1
        elif not value.startswith('-W'):
            command.append(value)
        index += 1
    command += extra
    subprocess.run(command, cwd=record['directory'], check=True, timeout=90)
    return destination


# Stage 2a: the kernel references the virtio transport, the owner root and the
# sheng power poll because boot progress selects them, but their archives need
# inputs that no repository carries (a live storage capture, a completed
# verification of the original OS volume). The definitions below report absence;
# the VerifiedRoot code itself is linked from its real sources so a policy bundle
# would still be verified by the kit, not by this file.
storage_source = ROOT / 'kits/sheng/StageAStorageStubs.c'
storage_obj = compile_like(policy_record, storage_source, OUT / 'StageAStorageStubs.o',
                           ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter'])
probe_source = ROOT / 'kits/sheng/StageAProbeStubs.c'
probe_obj = compile_like(policy_record, probe_source, OUT / 'StageAProbeStubs.o',
                         ['-mgeneral-regs-only', '-Wall', '-Wextra', '-Werror', '-Wno-unused-parameter'])
verified_root_dir = ROOT / 'kits/VerifiedRoot'
verified_root_objects = []
for kit_source in (verified_root_dir / 'src/verified_root.c', verified_root_dir / 'adapters/xnu_crypto.c'):
    destination = compile_like(policy_record, kit_source, OUT / (kit_source.stem + '.o'),
                               ['-I', str(verified_root_dir / 'include')])
    verified_root_objects.append(destination)

archive = OUT / 'libBootProgress.a'
archive.unlink(missing_ok=True)
objects = [obj, fault_obj, policy_obj, storage_obj, probe_obj, *verified_root_objects]
subprocess.run(['xcrun', 'ar', 'rcs', str(archive)] + [str(o) for o in objects], check=True, timeout=30)
(OUT / 'build-report.json').write_text(json.dumps({
    'storage_stub_sha256': hashlib.sha256(storage_source.read_bytes()).hexdigest(),
    'probe_stub_sha256': hashlib.sha256(probe_source.read_bytes()).hexdigest(),
    'verified_root_source_sha256': {
        str(p.relative_to(verified_root_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (verified_root_dir / 'src/verified_root.c', verified_root_dir / 'adapters/xnu_crypto.c')},
    'watchdog_policy_sha256': hashlib.sha256(policy_source.read_bytes()).hexdigest(),
    'fault_sha256': hashlib.sha256(fault_source.read_bytes()).hexdigest(),
    'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
    'lifecycle_sha256': hashlib.sha256((source.parent / 'BootProgressLifecycle.h').read_bytes()).hexdigest(),
    'core_sha256': hashlib.sha256((source.parent / 'BootProgressCore.h').read_bytes()).hexdigest(),
    'display_sha256': hashlib.sha256((source.parent / 'BootProgressDisplay.h').read_bytes()).hexdigest(),
    'pulse_sha256': hashlib.sha256((source.parent / 'BootProgressPulse.h').read_bytes()).hexdigest(),
    'archive_sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    'hardware_execution': False,
}, indent=2) + '\n')
print('PASS: strict source-kernel ARM64 boot diagnostic build')
