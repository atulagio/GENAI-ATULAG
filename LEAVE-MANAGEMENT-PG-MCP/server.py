"""
Employee Leave Management MCP Server
=====================================

Exposes 5 tools to any MCP-compatible AI assistant (Claude Desktop, etc.):

    - create_leave      : file a new leave request
    - approve_leave      : manager-only approve/reject
    - cancel_leave        : cancel a pending/approved request
    - leave_balance       : check remaining leave days
    - leave_history        : list past/pending requests

Transport is controlled by the MCP_TRANSPORT env var:
    - "stdio"           (default) - for Claude Desktop / local MCP clients
                          that spawn this process directly.
    - "streamable-http" - for running in a container / Kubernetes, where
                          a client connects over the network instead of
                          spawning a subprocess. Listens on MCP_HOST:MCP_PORT.

Run with:
    python server.py                                  (stdio, local)
    MCP_TRANSPORT=streamable-http python server.py     (network, container/K8s)
"""

import os
from typing import Optional

from mcp.server.fastmcp import FastMCP

from database import LeaveDB
from leave_service import LeaveService, LeaveError
from models import LeaveRequest

MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8000"))

mcp = FastMCP("leave-management", host=MCP_HOST, port=MCP_PORT)

db = LeaveDB()
service = LeaveService(db)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def _request_to_dict(r: LeaveRequest) -> dict:
    return {
        "request_id": r.id,
        "employee_id": r.employee_id,
        "leave_type": r.leave_type.value,
        "start_date": r.start_date.isoformat(),
        "end_date": r.end_date.isoformat(),
        "days": r.days,
        "status": r.status.value,
        "reason": r.reason,
        "approved_by": r.approved_by,
        "decision_note": r.decision_note,
    }


def _error(e: LeaveError) -> dict:
    """Every tool returns this same shape on failure so the assistant
    (and the human reading its output) gets a consistent, structured error
    instead of a stack trace."""
    return {"success": False, "error_code": e.code, "error": str(e)}


# ---------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------
@mcp.tool()
def create_leave(
    employee_id: str,
    leave_type: str,
    start_date: str,
    end_date: str,
    reason: str = "",
) -> dict:
    """Create a new leave request for an employee.

    Args:
        employee_id: ID of the employee requesting leave, e.g. "E002".
        leave_type: One of "sick", "casual", "earned".
        start_date: First day of leave, format YYYY-MM-DD.
        end_date: Last day of leave (inclusive), format YYYY-MM-DD.
        reason: Optional free-text reason for the leave.

    Returns a dict with success flag and either the created request or an error.
    """
    try:
        request = service.create_leave(employee_id, leave_type, start_date, end_date, reason)
        return {"success": True, "request": _request_to_dict(request)}
    except LeaveError as e:
        return _error(e)


@mcp.tool()
def approve_leave(request_id: str, manager_id: str, decision: str, note: str = "") -> dict:
    """Approve or reject a pending leave request. Manager-only action.

    Args:
        request_id: ID of the leave request, e.g. "LR0001".
        manager_id: ID of the manager making the decision. Must be the
            direct manager of the requesting employee.
        decision: "approved" or "rejected".
        note: Optional note explaining the decision.

    Returns a dict with success flag and either the updated request or an error.
    """
    try:
        request = service.approve_leave(request_id, manager_id, decision, note)
        return {"success": True, "request": _request_to_dict(request)}
    except LeaveError as e:
        return _error(e)


@mcp.tool()
def cancel_leave(request_id: str, employee_id: str) -> dict:
    """Cancel a pending or already-approved leave request.

    Args:
        request_id: ID of the leave request to cancel, e.g. "LR0001".
        employee_id: ID of the employee who owns the request (for authorization).

    Returns a dict with success flag and either the updated request or an error.
    """
    try:
        request = service.cancel_leave(request_id, employee_id)
        return {"success": True, "request": _request_to_dict(request)}
    except LeaveError as e:
        return _error(e)


@mcp.tool()
def leave_balance(employee_id: str) -> dict:
    """Check an employee's remaining leave balance by leave type.

    Args:
        employee_id: ID of the employee, e.g. "E002".

    Returns a dict with success flag and a balance breakdown, or an error.
    """
    try:
        balance = service.leave_balance(employee_id)
        return {"success": True, "employee_id": employee_id, "balance": balance}
    except LeaveError as e:
        return _error(e)


@mcp.tool()
def leave_history(employee_id: str, status: Optional[str] = None) -> dict:
    """Show an employee's leave request history, optionally filtered by status.

    Args:
        employee_id: ID of the employee, e.g. "E002".
        status: Optional filter - one of "pending", "approved", "rejected", "cancelled".

    Returns a dict with success flag and a list of leave requests, or an error.
    """
    try:
        requests = service.leave_history(employee_id, status)
        return {
            "success": True,
            "employee_id": employee_id,
            "count": len(requests),
            "requests": [_request_to_dict(r) for r in requests],
        }
    except LeaveError as e:
        return _error(e)


if __name__ == "__main__":
    mcp.run(transport=MCP_TRANSPORT)
