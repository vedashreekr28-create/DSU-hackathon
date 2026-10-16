import os
import random
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory

import uuid
import config
from database import init_db, get_db_connection
from waste_streams import get_stream_for_class, CLASS_TO_STREAM_MAPPING
import supabase_client
from digital_twin_service import (
    classify_waste_image,
    process_disposal_event,
    simulate_collection_event,
    simulate_sensor_telemetry,
    get_dashboard_data,
)

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
raw_secret = os.environ.get("SECRET_KEY")
if not raw_secret or "replace-with" in raw_secret:
    raw_secret = "ecotwin-campus-digital-twin-2026"
app.config["SECRET_KEY"] = raw_secret

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
@app.route("/simu")
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

import io
import base64
from PIL import Image

# Allowed live capture image formats
ALLOWED_FRAME_FORMATS = {"JPEG", "JPG", "PNG", "WEBP"}
PROHIBITED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".webm", ".mkv", ".m4v", ".wmv", ".3gp"}

@app.route("/api/classify", methods=["POST"])
def api_classify():
    """
    Classifies waste from a live device camera photo capture.
    Strictly rejects external file pickers, gallery uploads, and prerecorded videos.
    """
    capture_source = (
        request.headers.get("X-Capture-Source")
        or request.form.get("capture_source")
    )
    image_bytes = None

    # 1. Handle multipart form-data capture
    if "image" in request.files or "live_frame" in request.files:
        file = request.files.get("live_frame") or request.files.get("image")
        filename = (file.filename or "").lower()
        content_type = (file.content_type or "").lower()

        # Reject prerecorded videos
        if content_type.startswith("video/") or any(filename.endswith(ext) for ext in PROHIBITED_VIDEO_EXTENSIONS):
            return jsonify({
                "success": False,
                "error": "Prerecorded video files and video streams are strictly prohibited. Single-frame live camera capture is required."
            }), 415

        # Verify live camera attestation
        if capture_source != "live_camera":
            return jsonify({
                "success": False,
                "error": "Direct file uploads, gallery selection, and external files are not supported. Waste classification requires verified live camera capture."
            }), 400

        image_bytes = file.read()

    # 2. Handle JSON base64 frame from live camera canvas
    elif request.is_json:
        payload = request.get_json() or {}
        capture_source = capture_source or payload.get("capture_source")

        if capture_source != "live_camera":
            return jsonify({
                "success": False,
                "error": "Direct file uploads and external inputs are not supported. Waste classification requires verified live camera capture."
            }), 400

        data_url = payload.get("image_data") or payload.get("image")
        if not data_url:
            return jsonify({"success": False, "error": "No camera frame data received"}), 400

        # Reject video data URIs
        if data_url.startswith("data:video/"):
            return jsonify({
                "success": False,
                "error": "Video data is prohibited. Single-frame live camera capture is required."
            }), 415

        try:
            if "," in data_url:
                header, encoded = data_url.split(",", 1)
            else:
                encoded = data_url
            image_bytes = base64.b64decode(encoded)
        except Exception as e:
            return jsonify({"success": False, "error": f"Invalid base64 frame payload: {str(e)}"}), 400
    else:
        return jsonify({
            "success": False,
            "error": "Invalid request. Please capture a live photograph using the device camera."
        }), 400

    # 3. Size validation
    if not image_bytes or len(image_bytes) < 200:
        return jsonify({"success": False, "error": "Empty or corrupted camera frame received"}), 400

    if len(image_bytes) > 10 * 1024 * 1024:
        return jsonify({"success": False, "error": "Captured frame exceeds maximum size limit (10MB)"}), 413

    # 4. Strict format & dimension validation via PIL
    try:
        img = Image.open(io.BytesIO(image_bytes))
        img_fmt = (img.format or "").upper()
        if img_fmt not in ALLOWED_FRAME_FORMATS:
            return jsonify({
                "success": False,
                "error": f"Unsupported frame format '{img_fmt}'. Live camera frames must be JPEG, PNG, or WebP."
            }), 400

        width, height = img.size
        if width < 32 or height < 32:
            return jsonify({"success": False, "error": "Captured frame resolution is too low"}), 400
        if width > 4096 or height > 4096:
            return jsonify({"success": False, "error": "Captured frame resolution exceeds maximum dimensions"}), 400

    except Exception as e:
        return jsonify({"success": False, "error": f"Frame validation failed: {str(e)}"}), 400

    # 5. Execute Deep Learning CNN Inference
    try:
        result = classify_waste_image(image_bytes)
        result["input_mode"] = "live_camera_capture"
        result["verified_live"] = True

        # Record verified classification in Supabase PostgreSQL schema
        pred_class = result["predicted_class"]
        conf_val = result["confidence"]
        event_uuid = f"cls-{uuid.uuid4()}"

        classification_rec = supabase_client.record_waste_classification(
            waste_category=pred_class,
            confidence=conf_val,
            model_version=config.MODEL_VERSION,
            capture_source="live_camera",
            classification_status="successful",
            metadata={
                "event_uuid": event_uuid,
                "needs_manual_review": result.get("needs_manual_review", False),
                "review_status": result.get("review_status"),
                "probabilities": result.get("probabilities", {}),
            }
        )

        # Create recycling operation based on verified material and confidence
        pathway = supabase_client.determine_processing_pathway(
            pred_class,
            conf_val,
            needs_manual_review=result.get("needs_manual_review", False)
        )
        op_status = "pending" if result.get("needs_manual_review") else "completed"

        operation_rec = supabase_client.create_recycling_operation(
            classification_id=classification_rec["id"],
            processing_pathway=pathway,
            operation_status=op_status,
            is_simulated=True
        )

        result["classification_id"] = classification_rec["id"]
        result["operation_id"] = operation_rec["id"]
        result["processing_pathway"] = pathway
        result["operation_status"] = op_status
        result["database_status"] = supabase_client.get_database_status()

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/classify_sample", methods=["POST"])
def api_classify_sample():
    """Disabled: EcoTwin strictly requires live device camera capture."""
    return jsonify({
        "success": False,
        "error": "Dataset sample selection has been disabled. EcoTwin now enforces live device camera capture only."
    }), 403

@app.route("/api/sample_image", methods=["GET"])
def api_sample_image():
    """Disabled: EcoTwin strictly requires live device camera capture."""
    return jsonify({
        "success": False,
        "error": "Sample image browsing is disabled. Please capture a photo with your device camera."
    }), 403

@app.route("/dataset_image/<path:filename>")
def serve_dataset_image(filename):
    """Disabled: Direct dataset image previewing is disabled."""
    return "Dataset image preview disabled in live camera capture mode", 403


@app.route("/api/dispose", methods=["POST"])
def api_dispose():
    """
    Submits and accepts a waste disposal event.
    Guarantees idempotency via event_uuid.
    Synchronizes digital twin and updates Supabase recycling_bins load.
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

        # Synchronize recycling_bins load in Supabase PostgreSQL / replica
        loc_id = payload.get("location_id", "LIB")
        acc_class = payload["accepted_class"]
        stream_id = get_stream_for_class(acc_class)
        mapping = CLASS_TO_STREAM_MAPPING.get(acc_class, {})
        weight_delta = mapping.get("est_weight_kg", 0.05)

        bin_telemetry = supabase_client.update_recycling_bin_load(
            location_code=loc_id,
            accepted_category=stream_id,
            item_delta=1,
            weight_kg_delta=weight_delta
        )
        result["bin_telemetry"] = bin_telemetry
        result["database_status"] = supabase_client.get_database_status()

        return jsonify({"success": True, "data": result})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/database_status", methods=["GET"])
def api_database_status():
    """Returns database backend status (Supabase PostgreSQL vs local replica)."""
    return jsonify({"success": True, "data": supabase_client.get_database_status()})

@app.route("/api/supabase/views", methods=["GET"])
def api_supabase_views():
    """Returns real-time database view statistics."""
    try:
        views = supabase_client.fetch_dashboard_views()
        return jsonify({"success": True, "data": views})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/supabase/bins", methods=["GET"])
def api_supabase_bins():
    """Returns virtual bin capacity and utilization view."""
    try:
        views = supabase_client.fetch_dashboard_views()
        return jsonify({"success": True, "data": views.get("bins_utilization", [])})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/supabase/classifications", methods=["GET"])
def api_supabase_classifications():
    """Returns recent classification activity view."""
    try:
        views = supabase_client.fetch_dashboard_views()
        return jsonify({"success": True, "data": views.get("recent_classifications", [])})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route("/api/supabase/operations", methods=["GET"])
def api_supabase_operations():
    """Returns pathway summary view."""
    try:
        views = supabase_client.fetch_dashboard_views()
        return jsonify({
            "success": True,
            "data": {
                "pathway_summary": views.get("pathway_summary", []),
                "operations_status": views.get("operations_status", {}),
            }
        })
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
