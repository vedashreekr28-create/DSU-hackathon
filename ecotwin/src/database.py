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

    conn.close()

if __name__ == "__main__":
    init_db()
