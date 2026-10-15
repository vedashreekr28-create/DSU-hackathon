import os
import random
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory

import config
from database import init_db, get_db_connection
from digital_twin_service import (
    classify_waste_image,
    process_disposal_event,
    simulate_collection_event,
    simulate_sensor_telemetry,
    get_dashboard_data,
)

app = Flask(__name__)
app.config["SECRET_KEY"] = "ecotwin-campus-digital-twin-2026"

# Ensure database is initialized at launch
init_db()

@app.route("/")
def dashboard():
    """Main Digital Twin Dashboard (Idempotent GET - never mutates state)."""
    student_id = request.args.get("student_id", "student_101")
    data = get_dashboard_data(student_id=student_id)
    return render_template("dashboard.html", data=data, student_balance=data["student_balance"])

@app.route("/classify")
def classify_view():
    """AI Classification and verified waste segregation interface."""
    data = get_dashboard_data()
    return render_template("classify.html", student_balance=data["student_balance"])

@app.route("/simulator")
def simulator_view():
    """Virtual Sensor Telemetry and Fleet Collection simulator."""
    data = get_dashboard_data()
    return render_template("simulator.html", bins=data["bins"], student_balance=data["student_balance"])

# --- API ENDPOINTS ---

@app.route("/api/dashboard", methods=["GET"])
def api_dashboard():
    """Returns real-time digital twin state in JSON."""
    student_id = request.args.get("student_id", "student_101")
    data = get_dashboard_data(student_id=student_id)
    return jsonify({"success": True, "data": data})

@app.route("/api/classify", methods=["POST"])
def api_classify():
    """Uploads an image file and runs CNN inference."""
    if "image" not in request.files:
        return jsonify({"success": False, "error": "No image uploaded"}), 400

    file = request.files["image"]
    if file.filename == "":
        return jsonify({"success": False, "error": "Empty filename"}), 400

    try:
        image_bytes = file.read()
        result = classify_waste_image(image_bytes)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/classify_sample", methods=["POST"])
def api_classify_sample():
    """Classifies an existing sample image from the dataset path."""
    payload = request.get_json() or {}
    image_path = payload.get("image_path")

    if not image_path or not Path(image_path).exists():
        return jsonify({"success": False, "error": "Valid image_path required"}), 400

    try:
        result = classify_waste_image(image_path)
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/sample_image", methods=["GET"])
def api_sample_image():
    """Provides a sample image path and preview URL from the dataset for testing."""
    waste_class = request.args.get("class", "plastic")
    target_dir = config.DATA_DIR / waste_class

    if not target_dir.exists():
        return jsonify({"success": False, "error": f"Class '{waste_class}' folder not found"}), 404

    images = list(target_dir.glob("*.jpg"))
    if not images:
        return jsonify({"success": False, "error": "No images found"}), 404

    # Pick a sample image (deterministic or random)
    chosen = random.choice(images)
    rel_path = f"{waste_class}/{chosen.name}"
    preview_url = f"/dataset_image/{rel_path}"

    return jsonify({
        "success": True,
        "class": waste_class,
        "image_path": str(chosen),
        "image_url": preview_url,
    })

@app.route("/dataset_image/<path:filename>")
def serve_dataset_image(filename):
    """Serves dataset images for browser previewing in the UI."""
    return send_from_directory(str(config.DATA_DIR), filename)

@app.route("/api/dispose", methods=["POST"])
def api_dispose():
    """
    Submits and accepts a waste disposal event.
    Guarantees idempotency via event_uuid.
    """
    payload = request.get_json() or {}
    required = ["predicted_class", "confidence", "accepted_class"]
    for field in required:
        if field not in payload:
            return jsonify({"success": False, "error": f"Missing required field: {field}"}), 400

    try:
        result = process_disposal_event(
            student_id=payload.get("student_id", "student_101"),
            predicted_class=payload["predicted_class"],
            confidence=float(payload["confidence"]),
            accepted_class=payload["accepted_class"],
            location_id=payload.get("location_id", "LIB"),
            event_uuid=payload.get("event_uuid"),
            was_overridden=bool(payload.get("was_overridden", False)),
            image_name=payload.get("image_name"),
        )
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/sensor", methods=["POST"])
def api_sensor():
    """Simulates a virtual fill level telemetry transmission."""
    payload = request.get_json() or {}
    bin_id = payload.get("bin_id")
    fill_percent = payload.get("fill_percent")

    if not bin_id or fill_percent is None:
        return jsonify({"success": False, "error": "bin_id and fill_percent are required"}), 400

    try:
        result = simulate_sensor_telemetry(bin_id, float(fill_percent))
        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/collect", methods=["POST"])
def api_collect():
    """Simulates a collection truck emptying a virtual bin."""
    payload = request.get_json() or {}
    bin_id = payload.get("bin_id")
    operator = payload.get("operator", "Campus Facilities Operations")

    if not bin_id:
        return jsonify({"success": False, "error": "bin_id is required"}), 400

    try:
        result = simulate_collection_event(bin_id, operator)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/training_plot")
def serve_training_plot():
    """Serves the training history plot."""
    if config.HISTORY_PLOT_PATH.exists():
        return send_file(str(config.HISTORY_PLOT_PATH), mimetype="image/png")
    return "Training plot not found", 404

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"Starting EcoTwin Digital Twin Server on http://127.0.0.1:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
