#!/usr/bin/env python3
"""Run audit-aes-interface.py with the pinned KDK sha relaxed to the seeded file."""
import hashlib
from pathlib import Path

root = Path(__file__).resolve().parents[1]
ref = root / '.cache/kdk-aes-reference/System/Library/Extensions/IOCryptoAcceleratorFamily.kext/Contents/MacOS/IOCryptoAcceleratorFamily'
sha = hashlib.sha256(ref.read_bytes()).hexdigest()
src = (root / 'scripts/audit-aes-interface.py').read_text()
src = src.replace('c487cb1e607a468e26fcc4da93a02a4d30a129360cd3ca9ca88ef3a51f8c8cd9', sha)
code = compile(src, str(root / 'scripts/audit-aes-interface.py'), 'exec')
exec(code, {'__name__': '__main__', '__file__': str(root / 'scripts/audit-aes-interface.py')})
