"""Chatter module test configuration."""

import sys
from pathlib import Path

# Add python/ directory to sys.path BEFORE any app imports
# Path: python/app/simulation/chatter/tests/conftest.py -> python/
# 需要向上 5 级：tests -> chatter -> simulation -> app -> python
_python_dir = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_python_dir) not in sys.path:
    sys.path.insert(0, str(_python_dir))
