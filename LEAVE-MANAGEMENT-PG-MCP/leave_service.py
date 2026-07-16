"""
Business logic layer for the Leave Management MCP.

This is the layer that actually enforces the rules from the requirements:
  - creating leave requests
  - balance checks
  - manager-only approval
  - overlap detection
  - cancellation rules
  - leave history

Kept independent of MCP so it can be unit-tested on its own (see tests/).
"""

from datetime import date, datetime
from typing import List, Optional

from database import LeaveDB
from models import Employee, LeaveRequest, LeaveStatus, LeaveType, Role


class LeaveError(Exception):
    """Base class for all predictable/business errors.

    Every LeaveError carries a short machine-friendly `code` in addition
    to the human-readable message, so the MCP layer can return structured
    errors instead of raw stack traces.
    """
    code = "LEAVE_ERROR"


class EmployeeNotFound(LeaveError):
    code = "EMPLOYEE_NOT_FOUND"


class InvalidLeaveType(LeaveError):
    code = "INVALID_LEAVE_TYPE"


class InvalidDateRange(LeaveError):
    code = "INVALID_DATE_RANGE"


class InsufficientBalance(LeaveError):
    code = "INSUFFICIENT_BALANCE"


class OverlappingLeave(LeaveError):
    code = "OVERLAPPING_LEAVE"


class LeaveRequestNotFound(LeaveError):
    code = "LEAVE_REQUEST_NOT_FOUND"


class NotAuthorized(LeaveError):
    code = "NOT_AUTHORIZED"


class InvalidState(LeaveError):
    code = "INVALID_STATE"


def _parse_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        raise InvalidDateRange(f"{field_name} must be in YYYY-MM-DD format, got '{value}'")


class LeaveService:
    def __init__(self, db: LeaveDB):
        self.db = db

    # ---------------------------------------------------------------
    def _require_employee(self, employee_id: str) -> Employee:
        emp = self.db.get_employee(employee_id)
        if emp is None:
            raise EmployeeNotFound(f"No employee found with id '{employee_id}'")
        return emp

    # ---------------------------------------------------------------
    def create_leave(
        self,
        employee_id: str,
        leave_type: str,
        start_date: str,
        end_date: str,
        reason: str = "",
    ) -> LeaveRequest:
        employee = self._require_employee(employee_id)

        if leave_type not in [lt.value for lt in LeaveType]:
            raise InvalidLeaveType(
                f"'{leave_type}' is not a valid leave type. "
                f"Choose one of: {[lt.value for lt in LeaveType]}"
            )

        start = _parse_date(start_date, "start_date")
        end = _parse_date(end_date, "end_date")

        if end < start:
            raise InvalidDateRange("end_date cannot be before start_date")

        if start < date.today():
            raise InvalidDateRange("start_date cannot be in the past")

        requested_days = (end - start).days + 1

        # --- Overlap detection ---
        for existing in self.db.active_requests_for_employee(employee_id):
            if existing.overlaps(start, end):
                raise OverlappingLeave(
                    f"Requested dates overlap with existing {existing.status.value} "
                    f"leave request {existing.id} ({existing.start_date} to {existing.end_date})"
                )

        # --- Balance check ---
        available = employee.leave_balance.get(leave_type, 0)
        if requested_days > available:
            raise InsufficientBalance(
                f"Requested {requested_days} day(s) of {leave_type} leave, "
                f"but only {available} day(s) remain"
            )

        return self.db.create_leave_request(
            employee_id=employee_id,
            leave_type=LeaveType(leave_type),
            start_date=start,
            end_date=end,
            reason=reason,
        )

    # ---------------------------------------------------------------
    def approve_leave(self, request_id: str, manager_id: str, decision: str,
                       note: str = "") -> LeaveRequest:
        manager = self._require_employee(manager_id)
        if manager.role != Role.MANAGER:
            raise NotAuthorized(
                f"Employee '{manager_id}' is not a manager and cannot approve/reject leave"
            )

        request = self.db.get_leave_request(request_id)
        if request is None:
            raise LeaveRequestNotFound(f"No leave request found with id '{request_id}'")

        employee = self._require_employee(request.employee_id)
        if employee.manager_id != manager_id:
            raise NotAuthorized(
                f"'{manager_id}' is not the manager of employee '{request.employee_id}' "
                f"and cannot act on this request"
            )

        if request.status != LeaveStatus.PENDING:
            raise InvalidState(
                f"Leave request {request_id} is already '{request.status.value}' "
                f"and cannot be re-decided"
            )

        decision_norm = decision.strip().lower()
        if decision_norm not in ("approved", "rejected"):
            raise InvalidState("decision must be either 'approved' or 'rejected'")

        if decision_norm == "approved":
            # Deduct balance only on approval
            self.db.update_leave_balance(employee.id, request.leave_type.value, -request.days)
            request.status = LeaveStatus.APPROVED
        else:
            request.status = LeaveStatus.REJECTED

        request.approved_by = manager_id
        request.decision_note = note
        self.db.update_request(request)
        return request

    # ---------------------------------------------------------------
    def cancel_leave(self, request_id: str, employee_id: str) -> LeaveRequest:
        request = self.db.get_leave_request(request_id)
        if request is None:
            raise LeaveRequestNotFound(f"No leave request found with id '{request_id}'")

        if request.employee_id != employee_id:
            raise NotAuthorized(
                f"Employee '{employee_id}' cannot cancel a leave request "
                f"belonging to '{request.employee_id}'"
            )

        if request.status in (LeaveStatus.CANCELLED, LeaveStatus.REJECTED):
            raise InvalidState(
                f"Leave request {request_id} is already '{request.status.value}' "
                f"and cannot be cancelled"
            )

        was_approved = request.status == LeaveStatus.APPROVED
        request.status = LeaveStatus.CANCELLED

        if was_approved:
            # Refund the balance since the days are no longer being taken
            self.db.update_leave_balance(employee_id, request.leave_type.value, request.days)

        self.db.update_request(request)
        return request

    # ---------------------------------------------------------------
    def leave_balance(self, employee_id: str) -> dict:
        employee = self._require_employee(employee_id)
        return dict(employee.leave_balance)

    # ---------------------------------------------------------------
    def leave_history(self, employee_id: str, status: Optional[str] = None) -> List[LeaveRequest]:
        self._require_employee(employee_id)  # validates the id
        requests = self.db.requests_for_employee(employee_id)

        if status:
            status_norm = status.strip().lower()
            if status_norm not in [s.value for s in LeaveStatus]:
                raise InvalidState(
                    f"'{status}' is not a valid status filter. "
                    f"Choose one of: {[s.value for s in LeaveStatus]}"
                )
            requests = [r for r in requests if r.status.value == status_norm]

        return sorted(requests, key=lambda r: r.start_date)
