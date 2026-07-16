-- Employee Leave Management MCP — Postgres schema
-- Run once against a fresh database:
--   psql "$DATABASE_URL" -f schema.sql

BEGIN;

DO $$ BEGIN
    CREATE TYPE employee_role AS ENUM ('employee', 'manager');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE leave_status AS ENUM ('pending', 'approved', 'rejected', 'cancelled');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS employees (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    role          employee_role NOT NULL,
    manager_id    TEXT REFERENCES employees(id),
    -- e.g. {"sick": 12, "casual": 12, "earned": 15}
    leave_balance JSONB NOT NULL DEFAULT '{"sick": 12, "casual": 12, "earned": 15}'
);

-- Drives the human-friendly LR0001, LR0002... IDs
CREATE SEQUENCE IF NOT EXISTS leave_request_id_seq START 1;

CREATE TABLE IF NOT EXISTS leave_requests (
    id             TEXT PRIMARY KEY DEFAULT ('LR' || LPAD(nextval('leave_request_id_seq')::text, 4, '0')),
    employee_id    TEXT NOT NULL REFERENCES employees(id),
    leave_type     TEXT NOT NULL,
    start_date     DATE NOT NULL,
    end_date       DATE NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    status         leave_status NOT NULL DEFAULT 'pending',
    approved_by    TEXT REFERENCES employees(id),
    decision_note  TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

    CONSTRAINT end_after_start CHECK (end_date >= start_date)
);

CREATE INDEX IF NOT EXISTS idx_leave_requests_employee ON leave_requests(employee_id);
CREATE INDEX IF NOT EXISTS idx_leave_requests_status   ON leave_requests(status);

-- Optional but recommended hardening: let Postgres itself refuse overlapping
-- pending/approved leave for the same employee, instead of relying only on
-- the Python-side check in leave_service.py. Requires the btree_gist extension.
-- Uncomment if you want DB-level overlap protection (belt & suspenders):
--
-- CREATE EXTENSION IF NOT EXISTS btree_gist;
-- ALTER TABLE leave_requests ADD COLUMN date_range daterange
--     GENERATED ALWAYS AS (daterange(start_date, end_date, '[]')) STORED;
-- ALTER TABLE leave_requests ADD CONSTRAINT no_overlapping_active_leave
--     EXCLUDE USING gist (
--         employee_id WITH =,
--         date_range WITH &&
--     ) WHERE (status IN ('pending', 'approved'));

-- Seed data matching the original in-memory demo org
INSERT INTO employees (id, name, role, manager_id) VALUES
    ('E001', 'Asha Verma',  'manager',  NULL),
    ('E002', 'Rahul Singh', 'employee', 'E001'),
    ('E003', 'Priya Nair',  'employee', 'E001')
ON CONFLICT (id) DO NOTHING;

COMMIT;
