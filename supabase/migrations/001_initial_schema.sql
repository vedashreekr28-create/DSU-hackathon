-- ====================================================================
-- EcoTwin Smart Waste Management Digital Twin - Supabase PostgreSQL Schema
-- Migration: 001_initial_schema.sql
-- ====================================================================

-- 1. EXTENSIONS
CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- 2. TRIGGER FUNCTION FOR UPDATED_AT TIMESTAMPS
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- 3. TABLE: waste_classifications
-- Stores every verified classification from live camera captures.
-- Explicitly prohibits permanent image storage or base64 strings in PostgreSQL.
CREATE TABLE IF NOT EXISTS waste_classifications (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    waste_category TEXT NOT NULL CHECK (
        waste_category IN ('cardboard', 'glass', 'metal', 'paper', 'plastic', 'trash')
    ),
    confidence NUMERIC(5, 4) CHECK (confidence >= 0.0 AND confidence <= 1.0),
    model_version TEXT NOT NULL DEFAULT 'mobilenetv2-v1.0',
    capture_source TEXT NOT NULL DEFAULT 'live_camera' CHECK (capture_source = 'live_camera'),
    classification_status TEXT NOT NULL DEFAULT 'successful' CHECK (
        classification_status IN ('successful', 'failed')
    ),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata JSONB DEFAULT '{}'::jsonb
);

-- 4. TABLE: recycling_operations
-- Tracks the digital twin circular processing workflow.
CREATE TABLE IF NOT EXISTS recycling_operations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    classification_id UUID NOT NULL REFERENCES waste_classifications(id) ON DELETE CASCADE,
    processing_pathway TEXT NOT NULL CHECK (
        processing_pathway IN ('recycling', 'composting', 'landfill', 'special_handling', 'pending_review')
    ),
    operation_status TEXT NOT NULL DEFAULT 'completed' CHECK (
        operation_status IN ('pending', 'processing', 'completed', 'failed')
    ),
    is_simulated BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- 5. TABLE: recycling_bins
-- Represents the virtual digital-twin segregation bins across campus.
CREATE TABLE IF NOT EXISTS recycling_bins (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    bin_name TEXT NOT NULL,
    location_code TEXT NOT NULL DEFAULT 'LIB' CHECK (
        location_code IN ('LIB', 'CAF', 'ENG', 'STU')
    ),
    location_name TEXT NOT NULL DEFAULT 'Central Library',
    accepted_category TEXT NOT NULL CHECK (
        accepted_category IN ('cardboard', 'glass', 'metal', 'paper', 'plastic', 'trash', 'dry_recyclable', 'glass_metal', 'residual')
    ),
    stream_name TEXT NOT NULL DEFAULT 'Dry Recyclables',
    capacity_kg NUMERIC(6, 2) DEFAULT 50.00 CHECK (capacity_kg > 0),
    current_load_kg NUMERIC(6, 2) DEFAULT 0.00 CHECK (current_load_kg >= 0),
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    max_capacity_items INTEGER NOT NULL DEFAULT 40 CHECK (max_capacity_items > 0),
    status TEXT NOT NULL DEFAULT 'available' CHECK (
        status IN ('available', 'near_capacity', 'full')
    ),
    is_simulated BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_bin_location_category UNIQUE (location_code, accepted_category)
);

-- 6. TRIGGERS
DROP TRIGGER IF EXISTS trg_recycling_operations_updated_at ON recycling_operations;
CREATE TRIGGER trg_recycling_operations_updated_at
    BEFORE UPDATE ON recycling_operations
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

DROP TRIGGER IF EXISTS trg_recycling_bins_updated_at ON recycling_bins;
CREATE TRIGGER trg_recycling_bins_updated_at
    BEFORE UPDATE ON recycling_bins
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- 7. PERFORMANCE INDEXES
CREATE INDEX IF NOT EXISTS idx_classifications_created_at ON waste_classifications(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_classifications_category ON waste_classifications(waste_category);
CREATE INDEX IF NOT EXISTS idx_classifications_status ON waste_classifications(classification_status);
CREATE INDEX IF NOT EXISTS idx_operations_classification_id ON recycling_operations(classification_id);
CREATE INDEX IF NOT EXISTS idx_operations_pathway ON recycling_operations(processing_pathway);
CREATE INDEX IF NOT EXISTS idx_operations_status ON recycling_operations(operation_status);
CREATE INDEX IF NOT EXISTS idx_bins_status ON recycling_bins(status);
CREATE INDEX IF NOT EXISTS idx_bins_location ON recycling_bins(location_code);

-- 8. DASHBOARD VIEWS
-- View: Total Successful Classifications
CREATE OR REPLACE VIEW view_total_classifications AS
SELECT 
    COUNT(*)::INTEGER AS total_classifications,
    COUNT(CASE WHEN classification_status = 'successful' THEN 1 END)::INTEGER AS successful_classifications,
    ROUND(AVG(confidence)::NUMERIC, 4) AS avg_confidence,
    MAX(created_at) AS last_classified_at
FROM waste_classifications;

-- View: Category-Wise Distribution
CREATE OR REPLACE VIEW view_category_distribution AS
SELECT 
    waste_category,
    COUNT(*)::INTEGER AS item_count,
    ROUND(AVG(confidence)::NUMERIC, 4) AS avg_confidence,
    ROUND((COUNT(*) * 100.0 / NULLIF((SELECT COUNT(*) FROM waste_classifications), 0))::NUMERIC, 1) AS percentage_of_total
FROM waste_classifications
WHERE classification_status = 'successful'
GROUP BY waste_category
ORDER BY item_count DESC;

-- View: Recent Classification Activity
CREATE OR REPLACE VIEW view_recent_classifications AS
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
LIMIT 25;

-- View: Processing Pathway Summary
CREATE OR REPLACE VIEW view_pathway_summary AS
SELECT 
    processing_pathway,
    COUNT(*)::INTEGER AS total_count,
    COUNT(CASE WHEN operation_status = 'completed' THEN 1 END)::INTEGER AS completed_count,
    COUNT(CASE WHEN operation_status = 'pending_review' OR processing_pathway = 'pending_review' THEN 1 END)::INTEGER AS pending_review_count,
    COUNT(CASE WHEN is_simulated THEN 1 END)::INTEGER AS simulated_count
FROM recycling_operations
GROUP BY processing_pathway
ORDER BY total_count DESC;

-- View: Operations Status Summary
CREATE OR REPLACE VIEW view_operations_status AS
SELECT 
    COUNT(CASE WHEN operation_status = 'pending' THEN 1 END)::INTEGER AS pending_operations,
    COUNT(CASE WHEN operation_status = 'processing' THEN 1 END)::INTEGER AS processing_operations,
    COUNT(CASE WHEN operation_status = 'completed' THEN 1 END)::INTEGER AS completed_operations,
    COUNT(CASE WHEN operation_status = 'failed' THEN 1 END)::INTEGER AS failed_operations,
    COUNT(*)::INTEGER AS total_operations
FROM recycling_operations;

-- View: Virtual Bin Capacity and Utilization
CREATE OR REPLACE VIEW view_bin_utilization AS
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
    ROUND((item_count * 100.0 / NULLIF(max_capacity_items, 0))::NUMERIC, 1) AS fill_percentage,
    GREATEST(0, max_capacity_items - item_count) AS remaining_items_capacity,
    status,
    is_simulated,
    updated_at
FROM recycling_bins
ORDER BY location_code, accepted_category;

-- 9. ROW LEVEL SECURITY (RLS) POLICIES
ALTER TABLE waste_classifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE recycling_operations ENABLE ROW LEVEL SECURITY;
ALTER TABLE recycling_bins ENABLE ROW LEVEL SECURITY;

-- Clean existing policies if re-running migration
DROP POLICY IF EXISTS "Allow public read-only access to recycling bins" ON recycling_bins;
DROP POLICY IF EXISTS "Allow public read access to waste classifications" ON waste_classifications;
DROP POLICY IF EXISTS "Allow public read access to recycling operations" ON recycling_operations;
DROP POLICY IF EXISTS "Allow service_role full control on waste_classifications" ON waste_classifications;
DROP POLICY IF EXISTS "Allow service_role full control on recycling_operations" ON recycling_operations;
DROP POLICY IF EXISTS "Allow service_role full control on recycling_bins" ON recycling_bins;

-- Bins: Read-only for public and authenticated
CREATE POLICY "Allow public read-only access to recycling bins"
    ON recycling_bins FOR SELECT
    TO anon, authenticated
    USING (true);

-- Classifications: Public read of classifications metadata/stats
CREATE POLICY "Allow public read access to waste classifications"
    ON waste_classifications FOR SELECT
    TO anon, authenticated
    USING (true);

-- Operations: Public read of recycling operations
CREATE POLICY "Allow public read access to recycling operations"
    ON recycling_operations FOR SELECT
    TO anon, authenticated
    USING (true);

-- Trusted Backend (service_role) Full Access
CREATE POLICY "Allow service_role full control on waste_classifications"
    ON waste_classifications FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Allow service_role full control on recycling_operations"
    ON recycling_operations FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

CREATE POLICY "Allow service_role full control on recycling_bins"
    ON recycling_bins FOR ALL
    TO service_role
    USING (true)
    WITH CHECK (true);

-- 10. SEED DEFAULT CAMPUS VIRTUAL BINS
INSERT INTO recycling_bins (bin_name, location_code, location_name, accepted_category, stream_name, capacity_kg, current_load_kg, max_capacity_items, status, is_simulated)
VALUES
    ('Central Library - Dry Recyclables', 'LIB', 'Central Library', 'dry_recyclable', 'Dry Recyclables', 50.0, 0.0, 40, 'available', true),
    ('Central Library - Glass & Metals', 'LIB', 'Central Library', 'glass_metal', 'Glass & Metals', 50.0, 0.0, 40, 'available', true),
    ('Central Library - Residual Trash', 'LIB', 'Central Library', 'residual', 'Residual Waste', 50.0, 0.0, 40, 'available', true),
    ('Food Court - Dry Recyclables', 'CAF', 'Food Court / Cafeteria', 'dry_recyclable', 'Dry Recyclables', 50.0, 0.0, 40, 'available', true),
    ('Food Court - Glass & Metals', 'CAF', 'Food Court / Cafeteria', 'glass_metal', 'Glass & Metals', 50.0, 0.0, 40, 'available', true),
    ('Food Court - Residual Trash', 'CAF', 'Food Court / Cafeteria', 'residual', 'Residual Waste', 50.0, 0.0, 40, 'available', true),
    ('Engineering Quad - Dry Recyclables', 'ENG', 'Engineering Quad', 'dry_recyclable', 'Dry Recyclables', 50.0, 0.0, 40, 'available', true),
    ('Engineering Quad - Glass & Metals', 'ENG', 'Engineering Quad', 'glass_metal', 'Glass & Metals', 50.0, 0.0, 40, 'available', true),
    ('Engineering Quad - Residual Trash', 'ENG', 'Engineering Quad', 'residual', 'Residual Waste', 50.0, 0.0, 40, 'available', true),
    ('Student Activities Center - Dry Recyclables', 'STU', 'Student Activities Center', 'dry_recyclable', 'Dry Recyclables', 50.0, 0.0, 40, 'available', true),
    ('Student Activities Center - Glass & Metals', 'STU', 'Student Activities Center', 'glass_metal', 'Glass & Metals', 50.0, 0.0, 40, 'available', true),
    ('Student Activities Center - Residual Trash', 'STU', 'Student Activities Center', 'residual', 'Residual Waste', 50.0, 0.0, 40, 'available', true)
ON CONFLICT (location_code, accepted_category) DO NOTHING;
