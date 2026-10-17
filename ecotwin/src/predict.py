import sys
import numpy as np
from pathlib import Path
from tensorflow import keras
from PIL import Image
import config

DISPOSAL_GUIDELINES = {
    "cardboard": "Recyclable (Dry Waste). Flatten boxes to save space in the paper/cardboard bin.",
    "glass": "Recyclable. Rinse bottles/jars clean and place in the glass collection bin. Avoid breaking.",
    "metal": "Recyclable. Rinse cans and tins. Valuable for secondary metal smelting.",
    "paper": "Recyclable (Dry Waste). Keep clean and dry. Not suitable if soiled with food or grease.",
    "plastic": "Recyclable (Dry Waste). Check resin code, rinse clean, and dispose in the plastics bin.",
    "trash": "Non-recyclable / General Waste. Dispose in sanitary landfill or municipal general waste bin."
}

_CACHED_PREDICT_MODEL = None

def predict_image(image_path: str, model_path: str = None):
    global _CACHED_PREDICT_MODEL
    if model_path is None:
        model_path = config.MODEL_SAVE_PATH

    if not Path(model_path).exists():
        raise FileNotFoundError(f"Trained model not found at {model_path}. Please run train.py first.")

    if _CACHED_PREDICT_MODEL is None or str(model_path) != str(config.MODEL_SAVE_PATH):
        _CACHED_PREDICT_MODEL = keras.models.load_model(str(model_path))
    model = _CACHED_PREDICT_MODEL
    
    # Load and preprocess image
    img = Image.open(image_path).convert("RGB")
    img_resized = img.resize((config.IMG_HEIGHT, config.IMG_WIDTH))
    img_array = np.array(img_resized, dtype=np.float32)
    img_batch = np.expand_dims(img_array, axis=0)

    # Predict
    preds = model.predict(img_batch, verbose=0)[0]
    top_idx = int(np.argmax(preds))
    predicted_class = config.CLASSES[top_idx]
    confidence = float(preds[top_idx]) * 100

    print("=" * 55)
    print("EcoTwin Garbage Classification & Recommendation")
    print("=" * 55)
    print(f"Target Image:      {image_path}")
    print(f"Predicted Class:   {predicted_class.upper()} ({confidence:.2f}%)")
    print(f"Eco Recommendation: {DISPOSAL_GUIDELINES.get(predicted_class, 'Standard waste stream.')}")
    print("-" * 55)
    print("Class Probabilities:")
    for cls, prob in sorted(zip(config.CLASSES, preds), key=lambda x: x[1], reverse=True):
        bar = "#" * int(prob * 20)
        print(f"  {cls:<12}: {prob * 100:6.2f}% {bar}")
    print("=" * 55)

    return predicted_class, confidence

if __name__ == "__main__":
    if len(sys.argv) > 1:
        test_img = sys.argv[1]
    else:
        sample_candidates = list((config.DATA_DIR / "plastic").glob("*.jpg"))
        if sample_candidates:
            test_img = str(sample_candidates[0])
        else:
            print("No test image found.")
            sys.exit(0)

    predict_image(test_img)
