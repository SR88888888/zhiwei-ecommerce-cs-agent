CREATE TABLE IF NOT EXISTS demo_orders (
    order_id TEXT PRIMARY KEY,
    buyer_id TEXT NOT NULL,
    status TEXT NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    item TEXT NOT NULL,
    carrier TEXT,
    tracking_no TEXT,
    latest_trace TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
INSERT INTO demo_orders (order_id, buyer_id, status, amount, item, carrier, tracking_no, latest_trace) VALUES
('PDD20260806001', 'buyer_001', '???', 199.00, '??????', '????', 'SF1234567890', '?????????????'),
('PDD20260806002', 'buyer_002', '???', 89.00, '???', NULL, NULL, NULL)
ON CONFLICT (order_id) DO NOTHING;
