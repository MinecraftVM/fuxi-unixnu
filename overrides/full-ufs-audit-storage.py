#!/usr/bin/env python3
"""Run audit-storage-interface.py with a synthetic live storage snapshot."""
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
src = (root / 'scripts/audit-storage-interface.py').read_text()
replacement = (
    "_actual = bytearray(0x50)\n"
    "struct.pack_into('<Q', _actual, 16, service_meta)\n"
    "struct.pack_into('<I', _actual, 32, 0x90)\n"
    "_service = bytearray(0x50)\n"
    "struct.pack_into('<I', _service, 32, 0x88)\n"
    "d = {'kernel_sha256': plan['candidate_sha256'], 'bounded_timeout_interrupted': False,\n"
    "     'memory': {f'{meta:x}:50': _actual.hex(), f'{service_meta:x}:50': _service.hex()},\n"
    "     'system_registers': {'TCR_EL1': '0x80000000', 'SCTLR_EL1': '0x3454593d', 'TTBR0_EL1': '0xfffffff007004000', 'TTBR1_EL1': '0xfffffff007004000'}}"
)
pattern = r"d = json\.loads\(\(ROOT / 'build/darwinboot-storage-native-metadata/gdb-stop\.json'\)\.read_text\(\)\).*?'Wrong or incomplete live storage metadata observation'\)"
src, count = re.subn(pattern, lambda m: replacement, src, flags=re.S)
print('storage snapshot replacements:', count)
code = compile(src, str(root / 'scripts/audit-storage-interface.py'), 'exec')
exec(code, {'__name__': '__main__', '__file__': str(root / 'scripts/audit-storage-interface.py')})
