"""
In-memory "database" for the Leave Management MCP.

For a beginner-level project this avoids the extra ceremony of a real
database while keeping a clean interface (LeaveDB) that you could later
swap for SQLite / Postgres without touching the service or server layers.
"""

import itertools
from datetime import date
from typing import Dict, List, Optional

from models import Employee, LeaveRequest, Role, LeaveType


class LeaveDB:
    def __init__(self):
        self.employees: Dict[str, Employee] = {}
        self.leave_requests: Dict[str, LeaveRequest] = {}
        self._request_id_counter = itertools.count(1)
        self._seed()

    def _seed(self):
        """Seed a small org chart so the tools are usable immediately."""
        self.employees["E001"] = Employee(
            id="E001", name="Asha Verma", role=Role.MANAGER, manager_id=None
        )
        self.employees["E002"] = Employee(
            id="E002", name="Rahul Singh", role=Role.EMPLOYEE, manager_id="E001"
        )
        self.employees["E003"] = Employee(
            id="E003", name="Priya Nair", role=Role.EMPLOYEE, manager_id="E001"
        )

    # ---------- Employee lookups ----------
    def get_employee(self, employee_id: str) -> Optional[Employee]:
        return self.employees.get(employee_id)

    # ---------- Leave request CRUD ----------
    def next_request_id(self) -> str:
        return f"LR{next(self._request_id_counter):04d}"

    def add_leave_request(self, request: LeaveRequest) -> None:
        self.leave_requests[request.id] = request

    def get_leave_request(self, request_id: str) -> Optional[LeaveRequest]:
        return self.leave_requests.get(request_id)

    def requests_for_employee(self, employee_id: str) -> List[LeaveRequest]:
        return [r for r in self.leave_requests.values() if r.employee_id == employee_id]

    def active_requests_for_employee(self, employee_id: str) -> List[LeaveRequest]:
        """Requests that still occupy calendar days (pending or approved)."""
        return [
            r for r in self.requests_for_employee(employee_id)
            if r.status.value in ("pending", "approved")
        ]
