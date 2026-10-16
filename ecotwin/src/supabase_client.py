"""
EcoTwin Supabase PostgreSQL Integration Client.
Provides a unified, production-ready interface for:
- waste_classifications
- recycling_operations
- recycling_bins
- SQL dashboard views
Automatically uses live Supabase Cloud when credentials are configured in .env,
and falls back to local SQLite replica for offline development without breakage.
"""

import os
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional

import config
from database import get_db_connection

# Attempt to load Supabase client
_SUPABASE_CLIENT = None
_IS_CONNECTED = False
_INIT_ATTEMPTED = False

_LAST_ERROR = None

def get_supabase_client():
    """Initializes and returns cached Supabase client if valid credentials exist."""
    global _SUPABASE_CLIENT, _IS_CONNECTED, _INIT_ATTEMPTED, _LAST_ERROR
    if _INIT_ATTEMPTED:
        return _SUPABASE_CLIENT

    _INIT_ATTEMPTED = True
    url = config.SUPABASE_URL
    if url:
        url = url.strip().split("/rest/v1")[0].rstrip("/")
    # Prefer service role key on backend to bypass RLS, fallback to anon key
    key = config.SUPABASE_SERVICE_ROLE_KEY or config.SUPABASE_ANON_KEY

    # Check for placeholder or unset values
    if not url or not key or "your-project-ref" in url or "your-supabase" in key:
        _SUPABASE_CLIENT = None
        _IS_CONNECTED = False
        _LAST_ERROR = "Missing or placeholder Supabase credentials in .env"
        return None

    try:
        from supabase import create_client, Client
        _SUPABASE_CLIENT = create_client(url, key)
        # Test connectivity with a lightweight ping
        res = _SUPABASE_CLIENT.table("recycling_bins").select("id").limit(1).execute()
        _IS_CONNECTED = True
        _LAST_ERROR = None
        print(f"Connected to Supabase PostgreSQL at: {url}")
    except Exception as e:
        _LAST_ERROR = str(e)
        print(f"Supabase connection warning: {e}. Falling back to local database replica.")
        _SUPABASE_CLIENT = None
        _IS_CONNECTED = False

    return _SUPABASE_CLIENT

def is_supabase_active() -> bool:
    """Returns True if connected to live Supabase cloud."""
    get_supabase_client()
    return _IS_CONNECTED

def get_database_status() -> Dict[str, Any]:
    """Returns detailed database connectivity and mode status."""
    connected = is_supabase_active()
    return {
        "is_connected": connected,
        "engine": "Supabase PostgreSQL" if connected else "SQLite Local Replica",
        "supabase_url": config.SUPABASE_URL if config.SUPABASE_URL else None,
        "mode": "production_cloud" if connected else "local_fallback",
        "has_credentials": bool(config.SUPABASE_URL and (config.SUPABASE_SERVICE_ROLE_KEY or config.SUPABASE_ANON_KEY)),
        "is_simulated_backend": not connected,
        "model_version": config.MODEL_VERSION,
        "last_error": _LAST_ERROR,
    }

def determine_processing_pathway(
    waste_category: str,
    confidence: Optional[float],
    needs_manual_review: bool = False
) -> str:
    """
    Determines the safe processing pathway:
    - Uncertain classifications (<0.65 confidence) are held for review ('pending_review').
    - Clean recyclables (plastic, paper, cardboard, metal, glass) -> 'recycling'.
    - Residual trash -> 'landfill'.
    - Organic/food waste -> 'composting'.
    - Hazardous/e-waste -> 'special_handling'.
    """
    if needs_manual_review or (confidence is not None and confidence < config.CONFIDENCE_THRESHOLD):
        return "pending_review"

    cat = (waste_category or "").lower().strip()
    if cat in ("cardboard", "paper", "plastic", "metal", "glass"):
        return "recycling"
    elif cat == "trash":
        return "landfill"
    elif cat in ("organic", "food", "compost"):
        return "composting"
    elif cat in ("battery", "electronic", "chemical", "hazard"):
        return "special_handling"
    return "pending_review"

def record_waste_classification(
    waste_category: str,
    confidence: float,
    model_version: str = None,
    capture_source: str = "live_camera",
    classification_status: str = "successful",
    metadata: Optional[Dict[str, Any]] = None,
    classification_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Inserts a record into waste_classifications table.
    Enforces live_camera capture_source, confidence bounds [0, 1], and allowed categories.
    Explicitly refuses to store image bytes or base64 strings in PostgreSQL.
    """
    cat = (waste_category or "").lower().strip()
    if cat not in config.CLASSES:
        raise ValueError(f"Invalid waste category '{cat}'. Must be one of {config.CLASSES}")

    conf = float(confidence) if confidence is not None else 0.0
    if not (0.0 <= conf <= 1.0):
        raise ValueError(f"Confidence score {conf} must be between 0.0 and 1.0")

    if capture_source != "live_camera":
        raise ValueError("Strict security constraint: capture_source must be 'live_camera'")

    rec_id = classification_id or str(uuid.uuid4())
    version = model_version or config.MODEL_VERSION
    now_iso = datetime.now().isoformat()

    # Sanitize metadata: strip any large/image fields
    safe_metadata = {}
    if metadata and isinstance(metadata, dict):
        for k, v in metadata.items():
            if k.lower() in ("image", "image_data", "base64", "photo", "frame_bytes"):
                continue
            safe_metadata[k] = v

    client = get_supabase_client()
    if client and _IS_CONNECTED:
        try:
            payload = {
                "id": rec_id,
                "waste_category": cat,
                "confidence": round(conf, 4),
                "model_version": version,
                "capture_source": "live_camera",
                "classification_status": classification_status,
                "created_at": now_iso,
                "metadata": safe_metadata,
            }
            res = client.table("waste_classifications").insert(payload).execute()
            if res.data:
                return res.data[0]
            return payload
        except Exception as e:
            print(f"Supabase insert failed: {e}. Writing to local replica.")

    # Local Replica Fallback
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO waste_classifications (
                id, waste_category, confidence, model_version, capture_source,
                classification_status, created_at, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec_id,
                cat,
                round(conf, 4),
                version,
                "live_camera",
                classification_status,
                now_iso,
                json.dumps(safe_metadata),
            ),
        )
        conn.commit()
        return {
            "id": rec_id,
            "waste_category": cat,
            "confidence": round(conf, 4),
            "model_version": version,
            "capture_source": "live_camera",
            "classification_status": classification_status,
            "created_at": now_iso,
            "metadata": safe_metadata,
        }
    finally:
        conn.close()

def create_recycling_operation(
    classification_id: str,
    processing_pathway: str,
    operation_status: str = "completed",
    is_simulated: bool = True,
    operation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Inserts a record into recycling_operations table.
    Enforces valid pathway and operation status constraints.
    """
    allowed_pathways = {"recycling", "composting", "landfill", "special_handling", "pending_review"}
    allowed_statuses = {"pending", "processing", "completed", "failed"}

    if processing_pathway not in allowed_pathways:
        raise ValueError(f"Invalid processing_pathway '{processing_pathway}'. Allowed: {allowed_pathways}")
    if operation_status not in allowed_statuses:
        raise ValueError(f"Invalid operation_status '{operation_status}'. Allowed: {allowed_statuses}")

    op_id = operation_id or str(uuid.uuid4())
    now_iso = datetime.now().isoformat()

    client = get_supabase_client()
    if client and _IS_CONNECTED:
        try:
            payload = {
                "id": op_id,
                "classification_id": classification_id,
                "processing_pathway": processing_pathway,
                "operation_status": operation_status,
                "is_simulated": is_simulated,
                "created_at": now_iso,
                "updated_at": now_iso,
            }
            res = client.table("recycling_operations").insert(payload).execute()
            if res.data:
                return res.data[0]
            return payload
        except Exception as e:
            print(f"Supabase operation insert failed: {e}. Writing to local replica.")

    # Local Replica Fallback
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO recycling_operations (
                id, classification_id, processing_pathway, operation_status,
                is_simulated, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                op_id,
                classification_id,
                processing_pathway,
                operation_status,
                1 if is_simulated else 0,
                now_iso,
                now_iso,
            ),
        )
        conn.commit()
        return {
            "id": op_id,
            "classification_id": classification_id,
            "processing_pathway": processing_pathway,
            "operation_status": operation_status,
            "is_simulated": is_simulated,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
    finally:
        conn.close()

def update_recycling_bin_load(
    location_code: str,
    accepted_category: str,
    item_delta: int = 1,
    weight_kg_delta: float = 0.04,
) -> Dict[str, Any]:
    """
    Updates virtual bin telemetry (item count, current simulated load kg, and capacity status).
    """
    now_iso = datetime.now().isoformat()
    client = get_supabase_client()

    if client and _IS_CONNECTED:
        try:
            # Query bin
            res = client.table("recycling_bins").select("*").eq("location_code", location_code).eq("accepted_category", accepted_category).execute()
            if res.data:
                bin_row = res.data[0]
                new_count = max(0, bin_row["item_count"] + item_delta)
                new_load = max(0.0, round(float(bin_row["current_load_kg"]) + weight_kg_delta, 2))
                max_cap = bin_row["max_capacity_items"]
                fill_pct = min(100.0, round((new_count / max_cap) * 100.0, 1))

                new_status = "available"
                if fill_pct >= config.FULL_FILL_THRESHOLD or new_load >= float(bin_row["capacity_kg"]):
                    new_status = "full"
                elif fill_pct >= config.WARNING_FILL_THRESHOLD:
                    new_status = "near_capacity"

                client.table("recycling_bins").update({
                    "item_count": new_count,
                    "current_load_kg": new_load,
                    "status": new_status,
                    "updated_at": now_iso,
                }).eq("id", bin_row["id"]).execute()

                return {
                    "bin_id": bin_row["id"],
                    "bin_name": bin_row["bin_name"],
                    "item_count": new_count,
                    "current_load_kg": new_load,
                    "fill_percentage": fill_pct,
                    "status": new_status,
                }
        except Exception as e:
            print(f"Supabase bin update error: {e}. Using local replica.")

    # Local Replica
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT * FROM recycling_bins WHERE location_code = ? AND accepted_category = ?
            """,
            (location_code, accepted_category),
        )
        row = cursor.fetchone()
        if not row:
            # Try matching by stream_name
            cursor.execute("SELECT * FROM recycling_bins WHERE location_code = ? LIMIT 1", (location_code,))
            row = cursor.fetchone()
            if not row:
                return {}

        new_count = max(0, row["item_count"] + item_delta)
        new_load = max(0.0, round(float(row["current_load_kg"]) + weight_kg_delta, 2))
        max_cap = row["max_capacity_items"]
        fill_pct = min(100.0, round((new_count / max_cap) * 100.0, 1))

        new_status = "available"
        if fill_pct >= config.FULL_FILL_THRESHOLD or new_load >= float(row["capacity_kg"]):
            new_status = "full"
        elif fill_pct >= config.WARNING_FILL_THRESHOLD:
            new_status = "near_capacity"

        cursor.execute(
            """
            UPDATE recycling_bins
            SET item_count = ?, current_load_kg = ?, status = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_count, new_load, new_status, now_iso, row["id"]),
        )
        conn.commit()
        return {
            "bin_id": row["id"],
            "bin_name": row["bin_name"],
            "item_count": new_count,
            "current_load_kg": new_load,
            "fill_percentage": fill_pct,
            "status": new_status,
        }
    finally:
        conn.close()

def fetch_dashboard_views() -> Dict[str, Any]:
    """
    Fetches aggregate statistics from the database views:
    - view_total_classifications
    - view_category_distribution
    - view_recent_classifications
    - view_pathway_summary
    - view_operations_status
    - view_bin_utilization
    """
    client = get_supabase_client()
    if client and _IS_CONNECTED:
        try:
            total_res = client.table("view_total_classifications").select("*").execute().data or []
            cat_res = client.table("view_category_distribution").select("*").execute().data or []
            recent_res = client.table("view_recent_classifications").select("*").limit(15).execute().data or []
            pathway_res = client.table("view_pathway_summary").select("*").execute().data or []
            ops_res = client.table("view_operations_status").select("*").execute().data or []
            bins_res = client.table("view_bin_utilization").select("*").execute().data or []

            total_row = total_res[0] if total_res else {}
            ops_row = ops_res[0] if ops_res else {}

            category_counts = {c: 0 for c in config.CLASSES}
            for row in cat_res:
                category_counts[row["waste_category"]] = row["item_count"]

            return {
                "source": "supabase_postgresql",
                "is_simulated_backend": False,
                "total_classifications": total_row.get("total_classifications", 0),
                "successful_classifications": total_row.get("successful_classifications", 0),
                "avg_confidence": total_row.get("avg_confidence", 0.0),
                "last_classified_at": total_row.get("last_classified_at"),
                "category_distribution": cat_res,
                "category_counts": category_counts,
                "recent_classifications": recent_res,
                "pathway_summary": pathway_res,
                "operations_status": ops_row,
                "bins_utilization": bins_res,
            }
        except Exception as e:
            print(f"Supabase views query error: {e}. Falling back to local replica views.")

    # Local Replica Views Query
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        # Total
        cursor.execute("SELECT * FROM view_total_classifications")
        total_row = dict(cursor.fetchone() or {})

        # Category distribution
        cursor.execute("SELECT * FROM view_category_distribution")
        cat_res = [dict(r) for r in cursor.fetchall()]

        category_counts = {c: 0 for c in config.CLASSES}
        for row in cat_res:
            category_counts[row["waste_category"]] = row["item_count"]

        # Recent
        cursor.execute("SELECT * FROM view_recent_classifications LIMIT 15")
        recent_res = [dict(r) for r in cursor.fetchall()]

        # Pathway
        cursor.execute("SELECT * FROM view_pathway_summary")
        pathway_res = [dict(r) for r in cursor.fetchall()]

        # Operations status
        cursor.execute("SELECT * FROM view_operations_status")
        ops_row = dict(cursor.fetchone() or {})

        # Bins
        cursor.execute("SELECT * FROM view_bin_utilization")
        bins_res = [dict(r) for r in cursor.fetchall()]

        return {
            "source": "local_sqlite_replica",
            "is_simulated_backend": True,
            "total_classifications": total_row.get("total_classifications", 0),
            "successful_classifications": total_row.get("successful_classifications", 0),
            "avg_confidence": total_row.get("avg_confidence", 0.0),
            "last_classified_at": total_row.get("last_classified_at"),
            "category_distribution": cat_res,
            "category_counts": category_counts,
            "recent_classifications": recent_res,
            "pathway_summary": pathway_res,
            "operations_status": ops_row,
            "bins_utilization": bins_res,
        }
    finally:
        conn.close()
