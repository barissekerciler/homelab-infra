import sys
from pathlib import Path

# Test modules in subdirectories import tests/private_values.py.
sys.path.insert(0, str(Path(__file__).resolve().parent))
