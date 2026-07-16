"""
Postgres-backed "database" for the Leave Management MCP.

Same public interface as database_memory.LeaveDB (get_employee,
create_leave_request, get_leave_request, requests_for_employee,
active_requests_for_employee, update_leave_balance, update_request) so
leave_service.py and server.py don't need to know which backend is in use.

Configure via the DATABASE_URL environment variable, e.g.:
    postgresql://leave_user:leave_pass@localhost:5432/leave_management

Schema lives in schema.sql — run it once against a fresh database:
    psql "$DATABASE_URL" -f schema.sql
"""

import os
from contextlib import contextmanager
from datetime import date
from typing import List, Optional

import psycopg2
import psycopg2.extras
from psycopg2.pool import SimpleConnectionPool

from models import Employee, LeaveRequest, LeaveStatus, LeaveType, Role

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://leave_user:leave_pass@localhost:5432/leave_management",
)

_pool: Optional[SimpleConnectionPool] = None


def _get_pool() -> SimpleConnectionPool:
    global _pool
    if _pool is None:
        _pool = SimpleConnectionPool(minconn=1, maxconn=10, dsn=DATABASE_URL)
    return _pool


@contextmanager
def _connection():
    """Borrow a pooled connection; commit on success, rollback on error."""
    pool = _get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def _row_to_employee(row: dict) -> Employee:
    return Employee(
        id=row["id"],
        name=row["name"],
        role=Role(row["role"]),
        manager_id=row["manager_id"],
        leave_balance=row["leave_balance"],  # JSONB comes back as a dict already
    )


def _row_to_request(row: dict) -> LeaveRequest:
    return LeaveRequest(
        id=row["id"],
        employee_id=row["employee_id"],
        leave_type=LeaveType(row["leave_type"]),
        start_date=row["start_date"],
        end_date=row["end_date"],
        reason=row["reason"] or "",
        status=LeaveStatus(row["status"]),
        approved_by=row["approved_by"],
        decision_note=row["decision_note"],
    )


class LeaveDB:
    def __init__(self):
        # Nothing to seed here — schema.sql seeds the demo org once.
        # Fail fast with a clear message if Postgres isn't reachable,
        # rather than surfacing a cryptic error on the first tool call.
        try:
            with _connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
        except psycopg2.OperationalError as e:
            raise RuntimeError(
                f"Could not connect to Postgres using DATABASE_URL="
                f"'{DATABASE_URL}'. Is Postgres running and has "
                f"schema.sql been applied? Original error: {e}"
            ) from e

    # ---------- Employee lookups ----------
    def get_employee(self, employee_id: str) -> Optional[Employee]:
        with _connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, name, role, manager_id, leave_balance "
                    "FROM employees WHERE id = %s",
                    (employee_id,),
                )
                row = cur.fetchone()
                return _row_to_employee(row) if row else None

    # ---------- Leave request CRUD ----------
    def create_leave_request(
        self, employee_id: str, leave_type: LeaveType,
        start_date: date, end_date: date, reason: str,
    ) -> LeaveRequest:
        with _connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    INSERT INTO leave_requests
                        (employee_id, leave_type, start_date, end_date, reason)
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, employee_id, leave_type, start_date, end_date,
                              reason, status, approved_by, decision_note
                    """,
                    (employee_id, leave_type.value, start_date, end_date, reason),
                )
                row = cur.fetchone()
                return _row_to_request(row)

    def get_leave_request(self, request_id: str) -> Optional[LeaveRequest]:
        with _connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, employee_id, leave_type, start_date, end_date, "
                    "reason, status, approved_by, decision_note "
                    "FROM leave_requests WHERE id = %s",
                    (request_id,),
                )
                row = cur.fetchone()
                return _row_to_request(row) if row else None

    def requests_for_employee(self, employee_id: str) -> List[LeaveRequest]:
        with _connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, employee_id, leave_type, start_date, end_date, "
                    "reason, status, approved_by, decision_note "
                    "FROM leave_requests WHERE employee_id = %s ORDER BY start_date",
                    (employee_id,),
                )
                return [_row_to_request(r) for r in cur.fetchall()]

    def active_requests_for_employee(self, employee_id: str) -> List[LeaveRequest]:
        """Requests that still occupy calendar days (pending or approved)."""
        with _connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, employee_id, leave_type, start_date, end_date, "
                    "reason, status, approved_by, decision_note "
                    "FROM leave_requests "
                    "WHERE employee_id = %s AND status IN ('pending', 'approved')",
                    (employee_id,),
                )
                return [_row_to_request(r) for r in cur.fetchall()]

    # ---------- Mutations used by leave_service.py ----------
    def update_leave_balance(self, employee_id: str, leave_type: str, delta: int) -> None:
        """Apply a +/- delta to one leave type's remaining balance.

        Done as a single atomic UPDATE (read-modify-write inside Postgres)
        rather than fetch-then-write from Python, so two concurrent
        approvals for the same employee can't race and clobber each other.
        """
        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE employees
                    SET leave_balance = jsonb_set(
                        leave_balance,
                        ARRAY[%s],
                        to_jsonb(COALESCE((leave_balance->>%s)::int, 0) + %s)
                    )
                    WHERE id = %s
                    """,
                    (leave_type, leave_type, delta, employee_id),
                )

    def update_request(self, request: LeaveRequest) -> None:
        """Persist status/approved_by/decision_note changes."""
        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE leave_requests
                    SET status = %s, approved_by = %s, decision_note = %s
                    WHERE id = %s
                    """,
                    (request.status.value, request.approved_by,
                     request.decision_note, request.id),
                )
