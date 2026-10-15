import io
import uuid
import sqlite3
import numpy as np
from datetime import datetime
from pathlib import Path
from PIL import Image
import requests


try:
    from tensorflow import keras
    HAS_LOCAL_TF = True
except ImportError:
    keras = None
    HAS_LOCAL_TF = False

import config
from database import get_db_connection
from waste_streams import (
    CLASS_TO_STREAM_MAPPING,
    CAMPUS_STREAMS,
    calculate_co2_saving,
    get_stream_for_class,
)

# Global cached model instance to avoid repeated disk reads
_CACHED_MODEL = None

def get_model():
    """Loads and caches the trained Keras model."""
    global _CACHED_MODEL
    if not HAS_LOCAL_TF:
        raise ImportError(
            "TensorFlow is not installed in this environment. "
            "Please configure INFERENCE_API_URL for remote inference or install TensorFlow locally."
        )
    if _CACHED_MODEL is None:
        if not Path(config.MODEL_SAVE_PATH).exists():
            raise FileNotFoundError(
                f"Trained model not found at {config.MODEL_SAVE_PATH}. Please wait for training to complete."
            )
        print(f"Loading Keras model from {config.MODEL_SAVE_PATH}...")
        _CACHED_MODEL = keras.models.load_model(str(config.MODEL_SAVE_PATH))
    return _CACHED_MODEL

def classify_waste_image(image_file_or_bytes):
    """
    Runs actual inference using either:
    1. Remote AI inference API (if INFERENCE_API_URL is configured)
    2. Local CNN model (if TensorFlow is installed locally)
    Never fabricates predictions or confidence values.
    """
    # 1. Check if external inference endpoint is configured
    if config.INFERENCE_API_URL:
        if isinstance(image_file_or_bytes, (str, Path)):
            with open(image_file_or_bytes, "rb") as f:
                img_data = f.read()
        elif isinstance(image_file_or_bytes, bytes):
            img_data = image_file_or_bytes
        else:
            img_data = image_file_or_bytes.read()

        resp = requests.post(
            f"{config.INFERENCE_API_URL.rstrip('/')}/api/classify",
            files={"image": ("waste.jpg", img_data, "image/jpeg")},
            timeout=10,
        )
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("success"):
                return res_json["data"]
        raise RuntimeError(f"Remote inference API returned error: {resp.text}")

    # 2. Local CNN inference
    if not HAS_LOCAL_TF:
        raise RuntimeError(
            "TensorFlow is not installed in this serverless environment (exceeds 250MB limit). "
            "Please configure INFERENCE_API_URL in your Vercel settings to point to your AI inference endpoint, "
            "or run EcoTwin locally."
        )

    model = get_model()


    if isinstance(image_file_or_bytes, (str, Path)):
        img = Image.open(image_file_or_bytes).convert("RGB")
    elif isinstance(image_file_or_bytes, bytes):
        img = Image.open(io.BytesIO(image_file_or_bytes)).convert("RGB")
    else:
        # File-like object (e.g. from Flask request.files['image'])
        img = Image.open(image_file_or_bytes).convert("RGB")

    # Resize to model input dimensions
    img_resized = img.resize((config.IMG_HEIGHT, config.IMG_WIDTH))
    img_array = np.array(img_resized, dtype=np.float32)
    img_batch = np.expand_dims(img_array, axis=0)

    # Actual model prediction
    preds = model.predict(img_batch, verbose=0)[0]
    top_idx = int(np.argmax(preds))
    predicted_class = config.CLASSES[top_idx]
    confidence = float(preds[top_idx])

    # Class probabilities dictionary
    probabilities = {
        cls: round(float(prob), 4) for cls, prob in zip(config.CLASSES, preds)
    }

    # Manual review evaluation rule
    needs_manual_review = confidence < config.CONFIDENCE_THRESHOLD
    stream_id = get_stream_for_class(predicted_class)
    stream_info = CAMPUS_STREAMS.get(stream_id, {})

    mapping = CLASS_TO_STREAM_MAPPING.get(predicted_class, {})
    co2_est = calculate_co2_saving(predicted_class)

    return {
        "predicted_class": predicted_class,
        "confidence": round(confidence, 4),
        "confidence_pct": round(confidence * 100.0, 2),
        "needs_manual_review": needs_manual_review,
        "review_status": "Needs manual review" if needs_manual_review else "High Confidence",
        "probabilities": probabilities,
        "stream_id": stream_id,
        "stream_name": stream_info.get("name", "Unknown"),
        "is_recyclable": mapping.get("is_recyclable", False),
        "potential_ecocoins": mapping.get("ecocoin_reward", 0),
        "estimated_co2_kg": co2_est,
        "rationale": mapping.get("rationale", ""),
    }

def process_disposal_event(
    student_id: str,
    predicted_class: str,
    confidence: float,
    accepted_class: str,
    location_id: str = "LIB",
    event_uuid: str = None,
    was_overridden: bool = False,
    image_name: str = None,
):
    """
    Processes and accepts a waste disposal event.
    Guarantees idempotency via event_uuid.
    Updates the exact virtual bin once, calculates rewards, and logs transactions.
    """
    if not event_uuid:
        event_uuid = str(uuid.uuid4())

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Idempotency Check: prevent duplicate submission & double counting
        cursor.execute("SELECT * FROM disposal_events WHERE event_uuid = ?", (event_uuid,))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            return {
                "success": True,
                "is_duplicate": True,
                "message": "Disposal event was already processed (idempotent).",
                "event_uuid": event_uuid,
            }

        # 2. Determine target stream & campus bin
        stream_id = get_stream_for_class(accepted_class)
        bin_id = f"BIN-{location_id}-{stream_id.upper()}"

        # Fetch bin
        cursor.execute("SELECT * FROM bins WHERE bin_id = ?", (bin_id,))
        bin_record = cursor.fetchone()
        if not bin_record:
            # Fallback to any bin with matching stream in that location
            cursor.execute(
                "SELECT * FROM bins WHERE location_id = ? AND stream_id = ?",
                (location_id, stream_id),
            )
            bin_record = cursor.fetchone()
            if not bin_record:
                raise ValueError(f"No campus bin found for location {location_id} and stream {stream_id}")
            bin_id = bin_record["bin_id"]

        # 3. Contamination Check
        # Contamination is flagged if the predicted stream didn't match the accepted stream,
        # or if an override was made from recyclable to residual / vice versa
        pred_stream = get_stream_for_class(predicted_class)
        is_contamination = 1 if (was_overridden and pred_stream != stream_id) else 0

        # 4. Reward Logic (Simulated EcoCoins)
        # Rules:
        # - Points only awarded for accepted recyclable streams
        # - 0 points if item is non-recyclable or unverified
        mapping = CLASS_TO_STREAM_MAPPING.get(accepted_class, {})
        eligible = mapping.get("eligible_for_rewards", False)
        reward_amount = mapping.get("ecocoin_reward", 0) if eligible else 0
        co2_saved = calculate_co2_saving(accepted_class)

        now = datetime.now().isoformat()

        # 5. Insert Disposal Event
        cursor.execute(
            """
            INSERT INTO disposal_events (
                event_uuid, student_id, predicted_class, confidence, accepted_class,
                stream_id, bin_id, was_overridden, is_contamination, ecocoins_awarded,
                estimated_co2_saved_kg, status, image_name, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', ?, ?)
            """,
            (
                event_uuid,
                student_id,
                predicted_class,
                confidence,
                accepted_class,
                stream_id,
                bin_id,
                1 if was_overridden else 0,
                is_contamination,
                reward_amount,
                co2_saved,
                image_name or "uploaded_waste.jpg",
                now,
            ),
        )

        # 6. Insert Reward Ledger entry (guarantees single reward per event)
        if reward_amount > 0:
            cursor.execute(
                """
                INSERT INTO rewards_ledger (event_uuid, student_id, amount, reason, timestamp)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    event_uuid,
                    student_id,
                    reward_amount,
                    f"Recycled {accepted_class} ({CAMPUS_STREAMS[stream_id]['name']})",
                    now,
                ),
            )

        # 7. Update Virtual Bin Capacity and Counters
        new_count = bin_record["item_count"] + 1
        max_cap = bin_record["max_capacity_items"]
        new_fill = min(100.0, round((new_count / max_cap) * 100.0, 1))

        if new_fill >= config.FULL_FILL_THRESHOLD or new_count >= max_cap:
            new_status = "Collection Required"
        elif new_fill >= config.WARNING_FILL_THRESHOLD:
            new_status = "Nearly Full"
        else:
            new_status = "Available"

        cursor.execute(
            """
            UPDATE bins
            SET item_count = ?, estimated_fill_percent = ?, status = ?, last_updated = ?
            WHERE bin_id = ?
            """,
            (new_count, new_fill, new_status, now, bin_id),
        )

        # 8. Alert Management (Deduplicated alert check)
        if new_status in ["Nearly Full", "Collection Required"]:
            cursor.execute(
                "SELECT id FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,)
            )
            existing_alert = cursor.fetchone()
            if not existing_alert:
                alert_type = (
                    "COLLECTION_REQUIRED"
                    if new_status == "Collection Required"
                    else "NEARLY_FULL"
                )
                msg = f"{bin_record['location_name']} {bin_record['stream_name']} is at {new_fill}% capacity ({new_count}/{max_cap} items)."
                cursor.execute(
                    """
                    INSERT INTO alerts (bin_id, alert_type, message, is_active, created_at)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (bin_id, alert_type, msg, now),
                )

        conn.commit()

        return {
            "success": True,
            "is_duplicate": False,
            "event_uuid": event_uuid,
            "bin_id": bin_id,
            "bin_location": bin_record["location_name"],
            "stream_name": bin_record["stream_name"],
            "new_count": new_count,
            "new_fill_percent": new_fill,
            "status": new_status,
            "ecocoins_awarded": reward_amount,
            "co2_saved_kg": co2_saved,
            "is_contamination": bool(is_contamination),
        }

    finally:
        conn.close()

def simulate_collection_event(bin_id: str, operator_name: str = "Campus Green Operations"):
    """
    Simulates a collection truck emptying a virtual bin.
    Resets item count to 0 and fill to 0.0%.
    Resolves active alerts for that bin and records the event.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM bins WHERE bin_id = ?", (bin_id,))
        bin_record = cursor.fetchone()
        if not bin_record:
            raise ValueError(f"Bin {bin_id} not found.")

        items_cleared = bin_record["item_count"]
        fill_before = bin_record["estimated_fill_percent"]
        now = datetime.now().isoformat()

        # Reset bin
        cursor.execute(
            """
            UPDATE bins
            SET item_count = 0, estimated_fill_percent = 0.0, status = 'Available', last_updated = ?
            WHERE bin_id = ?
            """,
            (now, bin_id),
        )

        # Log collection
        cursor.execute(
            """
            INSERT INTO collection_events (
                bin_id, items_cleared, fill_before, fill_after, operator_name, notes, timestamp
            ) VALUES (?, ?, ?, 0.0, ?, 'Routine campus digital twin pickup', ?)
            """,
            (bin_id, items_cleared, fill_before, operator_name, now),
        )

        # Resolve active alerts for this bin
        cursor.execute(
            """
            UPDATE alerts
            SET is_active = 0, resolved_at = ?
            WHERE bin_id = ? AND is_active = 1
            """,
            (now, bin_id),
        )

        conn.commit()
        return {
            "success": True,
            "bin_id": bin_id,
            "items_cleared": items_cleared,
            "fill_before": fill_before,
            "fill_after": 0.0,
            "status": "Available",
        }
    finally:
        conn.close()

def simulate_sensor_telemetry(bin_id: str, fill_percent: float):
    """
    Software-only sensor simulator: updates bin fill reading and triggers alerts if thresholds crossed.
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("SELECT * FROM bins WHERE bin_id = ?", (bin_id,))
        bin_record = cursor.fetchone()
        if not bin_record:
            raise ValueError(f"Bin {bin_id} not found.")

        fill_percent = max(0.0, min(100.0, float(fill_percent)))
        max_cap = bin_record["max_capacity_items"]
        simulated_items = int(round((fill_percent / 100.0) * max_cap))

        if fill_percent >= config.FULL_FILL_THRESHOLD:
            new_status = "Collection Required"
        elif fill_percent >= config.WARNING_FILL_THRESHOLD:
            new_status = "Nearly Full"
        else:
            new_status = "Available"

        now = datetime.now().isoformat()

        cursor.execute(
            """
            UPDATE bins
            SET item_count = ?, estimated_fill_percent = ?, status = ?, last_updated = ?
            WHERE bin_id = ?
            """,
            (simulated_items, fill_percent, new_status, now, bin_id),
        )

        # Deduplicated alert creation
        if new_status in ["Nearly Full", "Collection Required"]:
            cursor.execute("SELECT id FROM alerts WHERE bin_id = ? AND is_active = 1", (bin_id,))
            if not cursor.fetchone():
                alert_type = "COLLECTION_REQUIRED" if new_status == "Collection Required" else "NEARLY_FULL"
                msg = f"Virtual sensor reported {fill_percent:.1f}% fill at {bin_record['location_name']} {bin_record['stream_name']}."
                cursor.execute(
                    """
                    INSERT INTO alerts (bin_id, alert_type, message, is_active, created_at)
                    VALUES (?, ?, ?, 1, ?)
                    """,
                    (bin_id, alert_type, msg, now),
                )
        elif new_status == "Available":
            cursor.execute(
                "UPDATE alerts SET is_active = 0, resolved_at = ? WHERE bin_id = ? AND is_active = 1",
                (now, bin_id),
            )

        conn.commit()
        return {
            "success": True,
            "bin_id": bin_id,
            "fill_percent": fill_percent,
            "status": new_status,
            "item_count": simulated_items,
        }
    finally:
        conn.close()

def get_dashboard_data(student_id: str = "student_101"):
    """
    Fetches the live campus digital twin state.
    Guaranteed read-only (idempotent GET, never mutates counters).
    """
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        # 1. Bins
        cursor.execute("SELECT * FROM bins ORDER BY location_id, stream_id")
        bins = [dict(row) for row in cursor.fetchall()]
        for b in bins:
            b["remaining_capacity_est"] = max(0, b["max_capacity_items"] - b["item_count"])

        # 2. Overall counts
        cursor.execute("SELECT COUNT(*), COALESCE(SUM(estimated_co2_saved_kg), 0.0) FROM disposal_events")
        total_items, total_co2 = cursor.fetchone()

        cursor.execute("SELECT COUNT(*) FROM disposal_events WHERE is_contamination = 1")
        total_contamination = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM disposal_events WHERE stream_id != 'residual'")
        recycled_count = cursor.fetchone()[0]

        recycling_rate = round((recycled_count / total_items * 100.0), 1) if total_items > 0 else 0.0
        contamination_rate = round((total_contamination / total_items * 100.0), 1) if total_items > 0 else 0.0

        # 3. Category Breakdown
        cursor.execute(
            """
            SELECT accepted_class, COUNT(*) as count
            FROM disposal_events
            GROUP BY accepted_class
            """
        )
        category_breakdown = {cls: 0 for cls in config.CLASSES}
        for row in cursor.fetchall():
            category_breakdown[row["accepted_class"]] = row["count"]

        # 4. Stream Breakdown
        cursor.execute(
            """
            SELECT stream_id, COUNT(*) as count
            FROM disposal_events
            GROUP BY stream_id
            """
        )
        stream_breakdown = {s: 0 for s in CAMPUS_STREAMS.keys()}
        for row in cursor.fetchall():
            stream_breakdown[row["stream_id"]] = row["count"]

        # 5. Active Alerts
        cursor.execute(
            """
            SELECT a.*, b.location_name, b.stream_name
            FROM alerts a
            JOIN bins b ON a.bin_id = b.bin_id
            WHERE a.is_active = 1
            ORDER BY a.created_at DESC
            """
        )
        active_alerts = [dict(row) for row in cursor.fetchall()]

        # 6. Recent Disposal Events (Last 15)
        cursor.execute(
            """
            SELECT d.*, b.location_name
            FROM disposal_events d
            JOIN bins b ON d.bin_id = b.bin_id
            ORDER BY d.id DESC LIMIT 15
            """
        )
        recent_events = [dict(row) for row in cursor.fetchall()]

        # 7. Student EcoCoins Balance & Ledger
        cursor.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM rewards_ledger WHERE student_id = ?",
            (student_id,),
        )
        student_balance = cursor.fetchone()[0]

        cursor.execute(
            "SELECT * FROM rewards_ledger WHERE student_id = ? ORDER BY id DESC LIMIT 10",
            (student_id,),
        )
        student_ledger = [dict(row) for row in cursor.fetchall()]

        # 8. Recent Collections (Last 5)
        cursor.execute(
            """
            SELECT c.*, b.location_name, b.stream_name
            FROM collection_events c
            JOIN bins b ON c.bin_id = b.bin_id
            ORDER BY c.id DESC LIMIT 5
            """
        )
        recent_collections = [dict(row) for row in cursor.fetchall()]

        return {
            "bins": bins,
            "total_items": total_items,
            "recycled_count": recycled_count,
            "recycling_rate": recycling_rate,
            "contamination_count": total_contamination,
            "contamination_rate": contamination_rate,
            "total_co2_kg": round(total_co2, 2),
            "category_breakdown": category_breakdown,
            "stream_breakdown": stream_breakdown,
            "active_alerts": active_alerts,
            "recent_events": recent_events,
            "student_id": student_id,
            "student_balance": student_balance,
            "student_ledger": student_ledger,
            "recent_collections": recent_collections,
            "confidence_threshold": config.CONFIDENCE_THRESHOLD,
        }
    finally:
        conn.close()
