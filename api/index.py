import sys
from pathlib import Path

# Add project src directory to sys.path so modules resolve on Vercel
ROOT_DIR = Path(__file__).resolve().parent.parent
SRC_DIR = ROOT_DIR / "ecotwin" / "src"

if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import app
