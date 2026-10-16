import os
from pathlib import Path

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    # Check current directory and root project directory
    env_paths = [
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path(__file__).resolve().parent.parent / ".env",
        Path.cwd() / ".env",
    ]
    for p in env_paths:
        if p.exists():
            load_dotenv(p)
            break
except Exception:
    pass

# Base Paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "dataset" / "Garbage classification"
MODELS_DIR = BASE_DIR / "models"
MODEL_SAVE_PATH = MODELS_DIR / "ecotwin_garbage_classifier.keras"
HISTORY_PLOT_PATH = BASE_DIR / "training_history.png"

# Hyperparameters
IMG_HEIGHT = 224
IMG_WIDTH = 224
BATCH_SIZE = 32
EPOCHS = 10
LEARNING_RATE = 1e-4
VAL_SPLIT = 0.2
RANDOM_SEED = 42

# Target Classes
CLASSES = ["cardboard", "glass", "metal", "paper", "plastic", "trash"]
NUM_CLASSES = len(CLASSES)

# Digital Twin & Campus Settings
if os.environ.get("VERCEL"):
    import tempfile
    DB_PATH = Path(tempfile.gettempdir()) / "ecotwin.db"
else:
    DB_PATH = BASE_DIR / "ecotwin.db"

CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", 0.65))  # Flag for review if confidence < threshold
MAX_BIN_CAPACITY_DEFAULT = int(os.environ.get("MAX_BIN_CAPACITY_DEFAULT", 40))  # Default estimated max item capacity per bin
WARNING_FILL_THRESHOLD = 80.0  # Alert when fill >= 80%
FULL_FILL_THRESHOLD = 95.0     # Alert when fill >= 95%
MODEL_VERSION = os.environ.get("MODEL_VERSION", "mobilenetv2-v1.0")

# External Inference URL (Optional when deployed on serverless environments)
INFERENCE_API_URL = os.environ.get("INFERENCE_API_URL")

# Supabase PostgreSQL Configuration
SUPABASE_URL = os.environ.get("SUPABASE_URL")
if SUPABASE_URL:
    SUPABASE_URL = SUPABASE_URL.strip().split("/rest/v1")[0].rstrip("/")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_KEY")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_DB_URL = os.environ.get("SUPABASE_DB_URL") or os.environ.get("DATABASE_URL")
