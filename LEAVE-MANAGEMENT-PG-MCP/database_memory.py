"""
In-memory "database" for the Leave Management MCP.

This is now the *test/offline-dev* backend. Production uses the
Postgres-backed `LeaveDB` in database.py — both expose the exact same
interface (see leave_service.py), so this class is a drop-in replacement
wherever you want fast, isolated tests with no external dependency.
"""

import itertools
from datetime import date
from typing import Dict, List, Optional

from models import Employee, LeaveRequest, LeaveStatus, Role, LeaveType


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
    def _next_request_id(self) -> str:
        return f"LR{next(self._request_id_counter):04d}"

    def create_leave_request(
        self, employee_id: str, leave_type: LeaveType,
        start_date: date, end_date: date, reason: str,
    ) -> LeaveRequest:
        request = LeaveRequest(
            id=self._next_request_id(),
            employee_id=employee_id,
            leave_type=leave_type,
            start_date=start_date,
            end_date=end_date,
            reason=reason,
        )
        self.leave_requests[request.id] = request
        return request

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

    # ---------- Mutations used by leave_service.py ----------
    def update_leave_balance(self, employee_id: str, leave_type: str, delta: int) -> None:
        """Apply a +/- delta to one leave type's remaining balance."""
        employee = self.employees[employee_id]
        employee.leave_balance[leave_type] = employee.leave_balance.get(leave_type, 0) + delta

    def update_request(self, request: LeaveRequest) -> None:
        """Persist status/approved_by/decision_note changes.

        The in-memory dict already holds a reference to this exact object,
        so there's nothing to do here — but the method exists so the
        service layer's code is identical for both backends.
        """
        self.leave_requests[request.id] = request
