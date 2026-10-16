import sqlite3
from datetime import datetime
import config
from waste_streams import CAMPUS_STREAMS, CAMPUS_LOCATIONS

def get_db_connection():
    """Returns a SQLite connection with row factory configured."""
    conn = sqlite3.connect(str(config.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes schema and seeds campus virtual bins if empty."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # 1. Bins Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS bins (
        bin_id TEXT PRIMARY KEY,
        stream_id TEXT NOT NULL,
        stream_name TEXT NOT NULL,
        location_id TEXT NOT NULL,
        location_name TEXT NOT NULL,
        item_count INTEGER NOT NULL DEFAULT 0,
        max_capacity_items INTEGER NOT NULL DEFAULT 40,
        estimated_fill_percent REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL DEFAULT 'Available',
        last_updated TEXT NOT NULL
    )
    """)

    # 2. Disposal Events Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS disposal_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_uuid TEXT UNIQUE NOT NULL,
        student_id TEXT NOT NULL,
        predicted_class TEXT NOT NULL,
        confidence REAL NOT NULL,
        accepted_class TEXT NOT NULL,
        stream_id TEXT NOT NULL,
        bin_id TEXT NOT NULL,
        was_overridden INTEGER NOT NULL DEFAULT 0,
        is_contamination INTEGER NOT NULL DEFAULT 0,
        ecocoins_awarded INTEGER NOT NULL DEFAULT 0,
        estimated_co2_saved_kg REAL NOT NULL DEFAULT 0.0,
        status TEXT NOT NULL,
        image_name TEXT,
        timestamp TEXT NOT NULL
    )
    """)

    # 3. Rewards Ledger Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS rewards_ledger (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_uuid TEXT UNIQUE NOT NULL,
        student_id TEXT NOT NULL,
        amount INTEGER NOT NULL,
        reason TEXT NOT NULL,
        timestamp TEXT NOT NULL
    )
    """)

    # 4. Collection Events Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS collection_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bin_id TEXT NOT NULL,
        items_cleared INTEGER NOT NULL,
        fill_before REAL NOT NULL,
        fill_after REAL NOT NULL,
        operator_name TEXT NOT NULL,
        notes TEXT,
        timestamp TEXT NOT NULL
    )
    """)

    # 5. Alerts Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bin_id TEXT NOT NULL,
        alert_type TEXT NOT NULL,
        message TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        resolved_at TEXT
    )
    """)

    # 6. Supabase Schema: waste_classifications
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS waste_classifications (
        id TEXT PRIMARY KEY,
        waste_category TEXT NOT NULL CHECK (waste_category IN ('cardboard', 'glass', 'metal', 'paper', 'plastic', 'trash')),
        confidence REAL CHECK (confidence >= 0.0 AND confidence <= 1.0),
        model_version TEXT NOT NULL DEFAULT 'mobilenetv2-v1.0',
        capture_source TEXT NOT NULL DEFAULT 'live_camera' CHECK (capture_source = 'live_camera'),
        classification_status TEXT NOT NULL DEFAULT 'successful' CHECK (classification_status IN ('successful', 'failed')),
        created_at TEXT NOT NULL,
        metadata TEXT DEFAULT '{}'
    )
    """)

    # 7. Supabase Schema: recycling_operations
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS recycling_operations (
        id TEXT PRIMARY KEY,
        classification_id TEXT NOT NULL REFERENCES waste_classifications(id) ON DELETE CASCADE,
        processing_pathway TEXT NOT NULL CHECK (processing_pathway IN ('recycling', 'composting', 'landfill', 'special_handling', 'pending_review')),
        operation_status TEXT NOT NULL DEFAULT 'completed' CHECK (operation_status IN ('pending', 'processing', 'completed', 'failed')),
        is_simulated INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)

    # 8. Supabase Schema: recycling_bins
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS recycling_bins (
        id TEXT PRIMARY KEY,
        bin_name TEXT NOT NULL,
        location_code TEXT NOT NULL DEFAULT 'LIB' CHECK (location_code IN ('LIB', 'CAF', 'ENG', 'STU')),
        location_name TEXT NOT NULL DEFAULT 'Central Library',
        accepted_category TEXT NOT NULL,
        stream_name TEXT NOT NULL DEFAULT 'Dry Recyclables',
        capacity_kg REAL DEFAULT 50.0 CHECK (capacity_kg > 0),
        current_load_kg REAL DEFAULT 0.0 CHECK (current_load_kg >= 0),
        item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
        max_capacity_items INTEGER NOT NULL DEFAULT 40 CHECK (max_capacity_items > 0),
        status TEXT NOT NULL DEFAULT 'available' CHECK (status IN ('available', 'near_capacity', 'full')),
        is_simulated INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE (location_code, accepted_category)
    )
    """)

    # 9. Supabase Dashboard Views
    cursor.execute("""
    CREATE VIEW IF NOT EXISTS view_total_classifications AS
    SELECT 
        COUNT(*) AS total_classifications,
        SUM(CASE WHEN classification_status = 'successful' THEN 1 ELSE 0 END) AS successful_classifications,
        ROUND(AVG(confidence), 4) AS avg_confidence,
        MAX(created_at) AS last_classified_at
    FROM waste_classifications
    """)

    cursor.execute("""
    CREATE VIEW IF NOT EXISTS view_category_distribution AS
    SELECT 
        waste_category,
        COUNT(*) AS item_count,
        ROUND(AVG(confidence), 4) AS avg_confidence,
        ROUND((COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM waste_classifications), 0)), 1) AS percentage_of_total
    FROM waste_classifications
    WHERE classification_status = 'successful'
    GROUP BY waste_category
    ORDER BY item_count DESC
    """)

    cursor.execute("""
    CREATE VIEW IF NOT EXISTS view_recent_classifications AS
    SELECT 
        c.id AS classification_id,
        c.waste_category,
        c.confidence,
        c.model_version,
        c.capture_source,
        c.classification_status,
        c.created_at,
        o.id AS operation_id,
        o.processing_pathway,
        o.operation_status,
        o.is_simulated
    FROM waste_classifications c
    LEFT JOIN recycling_operations o ON c.id = o.classification_id
    ORDER BY c.created_at DESC
    LIMIT 25
    """)

    cursor.execute("""
    CREATE VIEW IF NOT EXISTS view_pathway_summary AS
    SELECT 
        processing_pathway,
        COUNT(*) AS total_count,
        SUM(CASE WHEN operation_status = 'completed' THEN 1 ELSE 0 END) AS completed_count,
        SUM(CASE WHEN operation_status = 'pending_review' OR processing_pathway = 'pending_review' THEN 1 ELSE 0 END) AS pending_review_count,
        SUM(CASE WHEN is_simulated = 1 THEN 1 ELSE 0 END) AS simulated_count
    FROM recycling_operations
    GROUP BY processing_pathway
    ORDER BY total_count DESC
    """)

    cursor.execute("""
    CREATE VIEW IF NOT EXISTS view_operations_status AS
    SELECT 
        SUM(CASE WHEN operation_status = 'pending' THEN 1 ELSE 0 END) AS pending_operations,
        SUM(CASE WHEN operation_status = 'processing' THEN 1 ELSE 0 END) AS processing_operations,
        SUM(CASE WHEN operation_status = 'completed' THEN 1 ELSE 0 END) AS completed_operations,
        SUM(CASE WHEN operation_status = 'failed' THEN 1 ELSE 0 END) AS failed_operations,
        COUNT(*) AS total_operations
    FROM recycling_operations
    """)

    cursor.execute("""
    CREATE VIEW IF NOT EXISTS view_bin_utilization AS
    SELECT 
        id AS bin_id,
        bin_name,
        location_code,
        location_name,
        accepted_category,
        stream_name,
        item_count,
        max_capacity_items,
        capacity_kg,
        current_load_kg,
        ROUND((item_count * 100.0 / NULLIF(max_capacity_items, 0)), 1) AS fill_percentage,
        MAX(0, max_capacity_items - item_count) AS remaining_items_capacity,
        status,
        is_simulated,
        updated_at
    FROM recycling_bins
    ORDER BY location_code, accepted_category
    """)

    conn.commit()

    # Seed recycling_bins if empty
    cursor.execute("SELECT COUNT(*) FROM recycling_bins")
    rb_count = cursor.fetchone()[0]
    if rb_count == 0:
        now = datetime.now().isoformat()
        for loc in CAMPUS_LOCATIONS:
            for stream_id, stream_info in CAMPUS_STREAMS.items():
                bin_uid = f"bin-{loc['id'].lower()}-{stream_id}"
                bname = f"{loc['name']} - {stream_info['name']}"
                cursor.execute("""
                INSERT OR IGNORE INTO recycling_bins (
                    id, bin_name, location_code, location_name, accepted_category,
                    stream_name, capacity_kg, current_load_kg, item_count, max_capacity_items,
                    status, is_simulated, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 50.0, 0.0, 0, 40, 'available', 1, ?, ?)
                """, (
                    bin_uid,
                    bname,
                    loc["id"],
                    loc["name"],
                    stream_id,
                    stream_info["name"],
                    now,
                    now
                ))
        conn.commit()

    # Seed campus bins across locations if not yet present
    cursor.execute("SELECT COUNT(*) FROM bins")
    count = cursor.fetchone()[0]

    if count == 0:
        now = datetime.now().isoformat()
        for loc in CAMPUS_LOCATIONS:
            for stream_id, stream_info in CAMPUS_STREAMS.items():
                bin_id = f"BIN-{loc['id']}-{stream_id.upper()}"
                cursor.execute("""
                INSERT INTO bins (
                    bin_id, stream_id, stream_name, location_id, location_name,
                    item_count, max_capacity_items, estimated_fill_percent, status, last_updated
                ) VALUES (?, ?, ?, ?, ?, 0, ?, 0.0, 'Available', ?)
                """, (
                    bin_id,
                    stream_id,
                    stream_info["name"],
                    loc["id"],
                    loc["name"],
                    config.MAX_BIN_CAPACITY_DEFAULT,
                    now
                ))
        conn.commit()
        print(f"Initialized database with {len(CAMPUS_LOCATIONS) * len(CAMPUS_STREAMS)} virtual campus bins.")

    # 10. C-Twin Specific Tables for UI and Full Supabase Persistence
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS c_twin_bins (
        id TEXT PRIMARY KEY,
        landmark TEXT NOT NULL,
        dx INTEGER NOT NULL,
        dy INTEGER NOT NULL,
        cat TEXT NOT NULL,
        fill INTEGER NOT NULL DEFAULT 0,
        off INTEGER NOT NULL DEFAULT 0,
        updated_at INTEGER NOT NULL,
        last_collected INTEGER NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS c_twin_reports (
        id TEXT PRIMARY KEY,
        bin_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        problem TEXT NOT NULL,
        description TEXT,
        photo_url TEXT,
        status TEXT NOT NULL DEFAULT 'Submitted',
        created_at INTEGER NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS c_twin_redemptions (
        id TEXT PRIMARY KEY,
        student_id TEXT NOT NULL,
        reward_id TEXT NOT NULL,
        reward_name TEXT NOT NULL,
        points_cost INTEGER NOT NULL,
        voucher_code TEXT NOT NULL,
        created_at INTEGER NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS c_twin_guest_codes (
        code TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        bin_id TEXT NOT NULL,
        waste_type TEXT NOT NULL,
        expires_at INTEGER NOT NULL,
        is_used INTEGER NOT NULL DEFAULT 0,
        created_at INTEGER NOT NULL
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS c_twin_escalations (
        id TEXT PRIMARY KEY,
        bin_id TEXT NOT NULL,
        report_count INTEGER NOT NULL,
        window_hours INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'Open',
        created_at INTEGER NOT NULL,
        resolved_at INTEGER
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS c_twin_config (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)

    # Seed c_twin_config
    cursor.execute("INSERT OR IGNORE INTO c_twin_config (key, value) VALUES ('escalation_threshold', '5')")
    cursor.execute("INSERT OR IGNORE INTO c_twin_config (key, value) VALUES ('escalation_window_hours', '24')")
    cursor.execute("INSERT OR IGNORE INTO c_twin_config (key, value) VALUES ('sustainability_weights', '[40, 30, 20, 10]')")

    # Seed c_twin_bins
    cursor.execute("SELECT COUNT(*) FROM c_twin_bins")
    if cursor.fetchone()[0] == 0:
        import time
        now_ms = int(time.time() * 1000)
        seed_bins = [
            ('B01', 'Library', -55, 40, 'Recycle', 42, 0),
            ('B02', 'Library', 55, 40, 'Paper', 68, 0),
            ('B03', 'Cafeteria', -55, 40, 'Organic', 91, 0),
            ('B04', 'Cafeteria', 55, 40, 'General', 55, 0),
            ('B05', 'CSE Block', -55, 40, 'Recycle', 78, 0),
            ('B06', 'CSE Block', 55, 40, 'Paper', 20, 0),
            ('B07', 'ECE Block', 55, 40, 'General', 33, 0),
            ('B08', 'Labs', -55, 40, 'Recycle', 96, 0),
            ('B09', 'Hostel Quad', -55, 40, 'Organic', 60, 1),
            ('B10', 'Main Gate', 55, -10, 'General', 15, 0),
        ]
        for bid, lm, dx, dy, cat, fill, off in seed_bins:
            cursor.execute("""
            INSERT OR IGNORE INTO c_twin_bins (id, landmark, dx, dy, cat, fill, off, updated_at, last_collected)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (bid, lm, dx, dy, cat, fill, off, now_ms - 1800000, now_ms - 36000000))

        # Seed initial reports on B03
        for i in range(4):
            cursor.execute("""
            INSERT OR IGNORE INTO c_twin_reports (id, bin_id, user_id, problem, description, photo_url, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"R-SEED-{i+1}", "B03", f"S-{i}", "Full / overflowing", "Overflowing onto pavement", "", "Submitted", now_ms - (i + 1) * 3600000))

        conn.commit()

    conn.close()

if __name__ == "__main__":
    init_db()
