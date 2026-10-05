import sys
from pathlib import Path
root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(root / "index"))
sys.path.insert(0, str(root / "mcp"))
