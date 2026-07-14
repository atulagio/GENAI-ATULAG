import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, timedelta
import pytest

from database import LeaveDB
from leave_service import (
    LeaveService, EmployeeNotFound, InvalidLeaveType, InvalidDateRange,
    InsufficientBalance, OverlappingLeave, NotAuthorized, InvalidState,
    LeaveRequestNotFound,
)

TODAY = date.today()


def d(offset):
    return (TODAY + timedelta(days=offset)).isoformat()


@pytest.fixture
def svc():
    return LeaveService(LeaveDB())


def test_create_leave_success(svc):
    req = svc.create_leave("E002", "casual", d(5), d(6), "trip")
    assert req.status.value == "pending"
    assert req.days == 2


def test_create_leave_unknown_employee(svc):
    with pytest.raises(EmployeeNotFound):
        svc.create_leave("E999", "casual", d(5), d(6), "x")


def test_create_leave_bad_type(svc):
    with pytest.raises(InvalidLeaveType):
        svc.create_leave("E002", "vacation", d(5), d(6), "x")


def test_create_leave_bad_range(svc):
    with pytest.raises(InvalidDateRange):
        svc.create_leave("E002", "casual", d(6), d(5), "x")


def test_create_leave_past_date(svc):
    with pytest.raises(InvalidDateRange):
        svc.create_leave("E002", "casual", d(-3), d(-1), "x")


def test_create_leave_insufficient_balance(svc):
    with pytest.raises(InsufficientBalance):
        svc.create_leave("E002", "casual", d(5), d(20), "long trip")  # 16 days > 12 balance


def test_overlap_detection(svc):
    svc.create_leave("E002", "casual", d(5), d(10), "first")
    with pytest.raises(OverlappingLeave):
        svc.create_leave("E002", "sick", d(8), d(12), "second overlaps")


def test_no_overlap_adjacent_dates(svc):
    svc.create_leave("E002", "casual", d(5), d(10), "first")
    # Starts the day after the first ends -> should be fine
    req2 = svc.create_leave("E002", "casual", d(11), d(12), "second")
    assert req2.id != ""


def test_approve_by_non_manager_rejected(svc):
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    with pytest.raises(NotAuthorized):
        svc.approve_leave(req.id, "E003", "approved")  # E003 is an employee, not manager


def test_approve_by_wrong_manager(svc):
    from models import Employee, Role
    # A second, unrelated manager who is NOT E002's manager
    svc.db.employees["E010"] = Employee(id="E010", name="Other Manager", role=Role.MANAGER, manager_id=None)
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    with pytest.raises(NotAuthorized):
        svc.approve_leave(req.id, "E010", "approved")


def test_approve_unknown_manager(svc):
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    with pytest.raises(EmployeeNotFound):
        svc.approve_leave(req.id, "E999", "approved")


def test_approve_success_deducts_balance(svc):
    before = svc.leave_balance("E002")["casual"]
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")  # 2 days
    svc.approve_leave(req.id, "E001", "approved")
    after = svc.leave_balance("E002")["casual"]
    assert after == before - 2


def test_approve_twice_fails(svc):
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    svc.approve_leave(req.id, "E001", "approved")
    with pytest.raises(InvalidState):
        svc.approve_leave(req.id, "E001", "approved")


def test_reject_does_not_deduct_balance(svc):
    before = svc.leave_balance("E002")["casual"]
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    svc.approve_leave(req.id, "E001", "rejected")
    after = svc.leave_balance("E002")["casual"]
    assert after == before


def test_cancel_pending(svc):
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    cancelled = svc.cancel_leave(req.id, "E002")
    assert cancelled.status.value == "cancelled"


def test_cancel_approved_refunds_balance(svc):
    before = svc.leave_balance("E002")["casual"]
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    svc.approve_leave(req.id, "E001", "approved")
    svc.cancel_leave(req.id, "E002")
    after = svc.leave_balance("E002")["casual"]
    assert after == before  # deducted then refunded


def test_cancel_by_wrong_employee(svc):
    req = svc.create_leave("E002", "casual", d(5), d(6), "x")
    with pytest.raises(NotAuthorized):
        svc.cancel_leave(req.id, "E003")


def test_cancel_unknown_request(svc):
    with pytest.raises(LeaveRequestNotFound):
        svc.cancel_leave("LR9999", "E002")


def test_leave_history_filter(svc):
    r1 = svc.create_leave("E002", "casual", d(5), d(6), "a")
    svc.create_leave("E002", "sick", d(20), d(21), "b")
    svc.approve_leave(r1.id, "E001", "approved")

    all_history = svc.leave_history("E002")
    assert len(all_history) == 2

    approved_only = svc.leave_history("E002", status="approved")
    assert len(approved_only) == 1
    assert approved_only[0].id == r1.id


def test_leave_balance_unknown_employee(svc):
    with pytest.raises(EmployeeNotFound):
        svc.leave_balance("E999")
