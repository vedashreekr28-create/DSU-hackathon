import os
from pathlib import Path

# Base Paths (all on D: drive)
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
DB_PATH = BASE_DIR / "ecotwin.db"
CONFIDENCE_THRESHOLD = 0.65  # If confidence < 0.65, flagged for manual review
MAX_BIN_CAPACITY_DEFAULT = 40  # Default estimated max item capacity per bin
WARNING_FILL_THRESHOLD = 80.0  # Alert when fill >= 80%
FULL_FILL_THRESHOLD = 95.0     # Alert when fill >= 95%

