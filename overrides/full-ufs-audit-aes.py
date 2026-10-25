#!/usr/bin/env python3
"""Run audit-aes-interface.py with the KDK sha relaxed and the live snapshot replaced by candidate data."""
import hashlib
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ref = root / '.cache/kdk-aes-reference/System/Library/Extensions/IOCryptoAcceleratorFamily.kext/Contents/MacOS/IOCryptoAcceleratorFamily'
sha = hashlib.sha256(ref.read_bytes()).hexdigest()
src = (root / 'scripts/audit-aes-interface.py').read_text()
src = src.replace('c487cb1e607a468e26fcc4da93a02a4d30a129360cd3ca9ca88ef3a51f8c8cd9', sha)
src = re.sub(r"snapshot = json\.loads\(args\.snapshot\.read_text\(\)\).*?'Snapshot did not reach actual bsd_init'\)", 'snapshot = None', src, flags=re.S)
src = re.sub(r"def observed\(address\):.*?raise SystemExit\(f'Missing actual guest metadata at \{address:#x\}'\)", 'def observed(address):\n    return read(address, 96)', src, flags=re.S)
src = src.replace("registers = snapshot['system_registers']", "registers = {'TCR_EL1': '0x80000000', 'SCTLR_EL1': '0x3454593d', 'TTBR0_EL1': '0xfffffff007004000', 'TTBR1_EL1': '0xfffffff007004000'}")
code = compile(src, str(root / 'scripts/audit-aes-interface.py'), 'exec')
exec(code, {'__name__': '__main__', '__file__': str(root / 'scripts/audit-aes-interface.py')})
