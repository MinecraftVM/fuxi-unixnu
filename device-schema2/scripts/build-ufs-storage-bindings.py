#!/usr/bin/env python3
"""Bind NativeUfs (ShengUfs) to the qualified original IOBlockStorageDevice class."""
import argparse, hashlib, json, re, struct, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'kits/XnuPrelink'))
import native_prelink as np

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--directory', type=Path, default=ROOT / 'build/sheng-ufs')
args = parser.parse_args()

out = args.directory.resolve()
audit = json.loads((ROOT / 'build/storage-abi/interface-audit.json').read_text())
obj_path = out / 'NativeUfs.o'
obj = obj_path.read_bytes()
image = np.macho(obj)

offset = next(at for cmd, at, size in image['commands'] if cmd == 2)
sy, count, st, _ = struct.unpack_from('<4I', obj, offset + 8)
symbols = []
for i in range(count):
    ix, kind, _, _, value = struct.unpack_from('<IBBHQ', obj, sy + i * 16)
    symbols.append((obj[st + ix:obj.index(b'\0', st + ix)].decode(), value, kind))

possible = {r['reference_name']: int(r['candidate_target'], 16) for r in audit['methods'] if not r['kernel_names']}
possible.update({
    '__ZN20IOBlockStorageDeviceC2EPK11OSMetaClass': int(audit['original_protected_constructor'], 16),
    '__ZN20IOBlockStorageDeviceD2Ev': int(audit['original_base_destructor'], 16),
    '__ZN20IOBlockStorageDevice10gMetaClassE': int(audit['original_metaclass'], 16),
    '__ZTV20IOBlockStorageDevice': int(audit['vtable'], 16),
    '__ZTI20IOBlockStorageDevice': int(audit['vtable'], 16),
})
undefined = [name for name, value, kind in symbols if kind == 1 and 'IOBlockStorageDevice' in name]
missing = [name for name in undefined if name not in possible]
if missing:
    raise SystemExit('Unqualified original storage import: ' + ', '.join(missing[:5]))
if any(re.search(r'IOBlockStorageDeviceD[01]Ev$', n) for n in undefined):
    raise SystemExit('Concrete driver must not invoke abstract complete/deleting destruction')
bindings = {name: possible[name] for name in undefined}
assembly = out / 'original-bindings.S'
assembly.write_text(''.join(f'.globl {name}\n.set {name}, {value:#x}\n' for name, value in sorted(bindings.items())))
subprocess.run(['xcrun', 'clang', '-target', 'arm64-apple-ios17.0', '-c', assembly, '-o', out / 'original-bindings.o'], check=True)
archive = out / 'libShengUfs.a'
if archive.exists():
    archive.unlink()
subprocess.run(['xcrun', 'ar', 'rcs', archive, obj_path, out / 'original-bindings.o'], check=True)
print(f'PASS: bounds {len(bindings)} original IOBlockStorageDevice imports for NativeUfs')
