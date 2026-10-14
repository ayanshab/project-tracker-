"""
Shared PostgreSQL helper. Copy this file into every service folder.

Handles a pooled connection (reused across Lambda invocations), simple query
helpers that return dicts, and idempotent schema creation.
"""

import logging
import os

from psycopg import connect
from psycopg.rows import dict_row

logger = logging.getLogger()

PG_CONFIG = (
    f"host={os.getenv('POSTGRES_HOST', 'localhost')} "
    f"port={os.getenv('POSTGRES_PORT', '5432')} "
    f"user={os.getenv('POSTGRES_USER') or 'test'} "
    f"password={os.getenv('POSTGRES_PASS') or 'test'} "
    f"dbname={os.getenv('POSTGRES_NAME') or 'test'} "
    f"connect_timeout=15"
)

_CONN = None
_SCHEMA_READY = False


def get_conn():
    """Return the pooled connection, opening a new one if needed."""
    global _CONN
    if _CONN is None or _CONN.closed:
        _CONN = connect(PG_CONFIG, row_factory=dict_row, autocommit=True)
    return _CONN


def _reset():
    global _CONN
    try:
        if _CONN is not None:
            _CONN.close()
    finally:
        _CONN = None


def query(sql, params=None):
    """Run a SELECT and return a list of dict rows."""
    try:
        with get_conn().cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    except Exception:
        _reset()
        raise


def query_one(sql, params=None):
    """Run a SELECT and return the first row as a dict, or None."""
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql, params=None):
    """Run an INSERT/UPDATE/DELETE. Returns the affected row count.
    If the statement has RETURNING, returns the returned row instead."""
    try:
        with get_conn().cursor() as cur:
            cur.execute(sql, params)
            if cur.description:
                return cur.fetchone()
            return cur.rowcount
    except Exception:
        _reset()
        raise


SCHEMA = """
CREATE SEQUENCE IF NOT EXISTS employee_number_seq START WITH 10000000;

CREATE TABLE IF NOT EXISTS users (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    employee_number  BIGINT NOT NULL UNIQUE DEFAULT nextval('employee_number_seq'),
    username         TEXT NOT NULL UNIQUE,
    password_hash    TEXT NOT NULL,
    name             TEXT NOT NULL,
    email            TEXT NOT NULL UNIQUE,
    job_title        TEXT,
    role             TEXT NOT NULL CHECK (role IN ('admin', 'manager', 'employee')),
    manager_id       UUID REFERENCES users(id),
    pay_rate         NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (pay_rate >= 0),
    is_archived      BOOLEAN NOT NULL DEFAULT FALSE,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Added after the first release: upgrade existing databases in place.
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT FALSE;
CREATE INDEX IF NOT EXISTS users_manager_idx ON users(manager_id);

CREATE TABLE IF NOT EXISTS projects (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name             TEXT NOT NULL,
    description      TEXT,
    manager_id       UUID NOT NULL REFERENCES users(id),
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    budget_hours     NUMERIC(10, 2) NOT NULL DEFAULT 0 CHECK (budget_hours >= 0),
    planned_budget   NUMERIC(14, 2) NOT NULL DEFAULT 0 CHECK (planned_budget >= 0),
    status           TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'completed')),
    is_archived      BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (end_date >= start_date)
);
CREATE INDEX IF NOT EXISTS projects_manager_idx ON projects(manager_id);

CREATE TABLE IF NOT EXISTS project_members (
    project_id       UUID NOT NULL REFERENCES projects(id),
    user_id          UUID NOT NULL REFERENCES users(id),
    hours_per_week   NUMERIC(5, 2) NOT NULL DEFAULT 40 CHECK (hours_per_week > 0 AND hours_per_week <= 40),
    assigned_from    DATE NOT NULL DEFAULT CURRENT_DATE,
    assigned_to      DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, user_id),
    CHECK (assigned_to IS NULL OR assigned_to >= assigned_from)
);
CREATE INDEX IF NOT EXISTS project_members_user_idx ON project_members(user_id);

CREATE TABLE IF NOT EXISTS milestones (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id),
    position         INTEGER NOT NULL CHECK (position > 0),
    name             TEXT NOT NULL,
    description      TEXT,
    start_date       DATE NOT NULL,
    end_date         DATE NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'completed')),
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (project_id, position),
    CHECK (end_date >= start_date)
);

CREATE TABLE IF NOT EXISTS milestone_members (
    milestone_id     UUID NOT NULL REFERENCES milestones(id),
    user_id          UUID NOT NULL REFERENCES users(id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (milestone_id, user_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    milestone_id     UUID NOT NULL REFERENCES milestones(id),
    title            TEXT NOT NULL,
    description      TEXT,
    assigned_to      UUID REFERENCES users(id),
    completed        BOOLEAN NOT NULL DEFAULT FALSE,
    completed_at     TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS tasks_milestone_idx ON tasks(milestone_id);

CREATE TABLE IF NOT EXISTS time_entries (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES users(id),
    project_id       UUID NOT NULL REFERENCES projects(id),
    milestone_id     UUID REFERENCES milestones(id),
    work_date        DATE NOT NULL DEFAULT CURRENT_DATE,
    hours            NUMERIC(5, 2) NOT NULL CHECK (hours > 0 AND hours <= 24),
    note             TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS time_entries_project_idx ON time_entries(project_id);
CREATE INDEX IF NOT EXISTS time_entries_user_date_idx ON time_entries(user_id, work_date);

CREATE TABLE IF NOT EXISTS audit_log (
    id               BIGSERIAL PRIMARY KEY,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_id         UUID,
    actor_name       TEXT,
    actor_role       TEXT,
    action           TEXT NOT NULL,
    entity_type      TEXT NOT NULL,
    entity_id        TEXT,
    entity_name      TEXT,
    details          JSONB NOT NULL DEFAULT '{}'::jsonb,
    source_ip        TEXT,
    user_agent       TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_time_idx ON audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_actor_idx ON audit_log(actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS audit_log_entity_idx ON audit_log(entity_type, entity_id);

-- The audit log is append-only: block edits and deletes at the database level.
CREATE OR REPLACE FUNCTION audit_log_block_changes() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname = 'audit_log_append_only') THEN
        CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_block_changes();
    END IF;
END $$;

-- Maker-checker: sensitive changes wait here until a different admin approves them.
CREATE TABLE IF NOT EXISTS change_requests (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kind             TEXT NOT NULL,
    entity_type      TEXT NOT NULL CHECK (entity_type IN ('project', 'user')),
    entity_id        UUID NOT NULL,
    entity_name      TEXT,
    payload          JSONB NOT NULL,
    previous         JSONB NOT NULL DEFAULT '{}'::jsonb,
    reason           TEXT,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected', 'cancelled')),
    requested_by     UUID NOT NULL REFERENCES users(id),
    requested_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_by       UUID REFERENCES users(id),
    decided_at       TIMESTAMPTZ,
    decision_note    TEXT,
    CHECK (decided_by IS NULL OR decided_by <> requested_by)
);
CREATE INDEX IF NOT EXISTS change_requests_status_idx ON change_requests(status, requested_at DESC);
CREATE INDEX IF NOT EXISTS change_requests_entity_idx ON change_requests(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS project_costs (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id       UUID NOT NULL REFERENCES projects(id),
    category         TEXT NOT NULL,
    description      TEXT,
    amount           NUMERIC(14, 2) NOT NULL CHECK (amount >= 0),
    incurred_on      DATE NOT NULL DEFAULT CURRENT_DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def ensure_schema():
    """Create all tables if they do not exist. Runs once per Lambda container."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return
    try:
        with get_conn().cursor() as cur:
            # Several Lambdas can cold-start at once; serialize schema setup so they don't collide.
            cur.execute("SELECT pg_advisory_lock(727272)")
            try:
                cur.execute(SCHEMA)
            finally:
                cur.execute("SELECT pg_advisory_unlock(727272)")
        _SCHEMA_READY = True
        logger.info("Schema verified")
    except Exception:
        _reset()
        raise