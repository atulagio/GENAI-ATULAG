"""
Data models for the Employee Leave Management MCP server.

Kept as plain dataclasses (no ORM) so the whole project stays
dependency-light and easy to follow for a beginner-level MCP.
"""

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional


class Role(str, Enum):
    EMPLOYEE = "employee"
    MANAGER = "manager"


class LeaveType(str, Enum):
    SICK = "sick"
    CASUAL = "casual"
    EARNED = "earned"


class LeaveStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


@dataclass
class Employee:
    id: str
    name: str
    role: Role
    manager_id: Optional[str]  # None for top-level managers/HR
    # Annual entitlement per leave type
    leave_balance: dict = field(default_factory=lambda: {
        LeaveType.SICK.value: 12,
        LeaveType.CASUAL.value: 12,
        LeaveType.EARNED.value: 15,
    })


@dataclass
class LeaveRequest:
    id: str
    employee_id: str
    leave_type: LeaveType
    start_date: date
    end_date: date
    reason: str
    status: LeaveStatus = LeaveStatus.PENDING
    approved_by: Optional[str] = None
    decision_note: Optional[str] = None

    @property
    def days(self) -> int:
        return (self.end_date - self.start_date).days + 1

    def overlaps(self, other_start: date, other_end: date) -> bool:
        """Standard interval overlap check: A.start <= B.end and B.start <= A.end"""
        return self.start_date <= other_end and other_start <= self.end_date
