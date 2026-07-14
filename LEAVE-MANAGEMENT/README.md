# Employee Leave Management MCP Server

A beginner-level MCP (Model Context Protocol) server that lets an AI
assistant (Claude Desktop, or any MCP client) manage employee leave
requests: create, approve/reject, cancel, check balance, and view history.

## Architecture

```
AI Assistant (Claude Desktop / any MCP client)
        │  MCP protocol (JSON-RPC over stdio)
        ▼
 server.py            <- MCP tool definitions (5 tools), input/output
                          shaping, catches LeaveError -> structured dict
        │  calls
        ▼
 leave_service.py      <- Business rules: validation, overlap detection,
                          balance math, authorization, state transitions
        │  reads/writes
        ▼
 database.py            <- In-memory store (LeaveDB): employees + requests
        │  uses
        ▼
 models.py               <- Dataclasses: Employee, LeaveRequest, enums
```

Each layer only knows about the layer below it, so you can:
- swap `database.py` for a real SQLite/Postgres-backed store without
  touching `server.py` or `leave_service.py`
- unit-test `leave_service.py` directly, with no MCP/transport involved
  (see `tests/test_leave_service.py`)

## Data model

- **Employee**: `id`, `name`, `role` (`employee` | `manager`), `manager_id`,
  `leave_balance` (per leave type: sick / casual / earned)
- **LeaveRequest**: `id`, `employee_id`, `leave_type`, `start_date`,
  `end_date`, `reason`, `status` (`pending` | `approved` | `rejected` | `cancelled`),
  `approved_by`, `decision_note`

Seed data (in `database.py`) creates a small org:
| ID   | Name        | Role     | Manager |
|------|-------------|----------|---------|
| E001 | Asha Verma  | manager  | —       |
| E002 | Rahul Singh | employee | E001    |
| E003 | Priya Nair  | employee | E001    |

## Tools

| Tool | Description |
|---|---|
| `create_leave(employee_id, leave_type, start_date, end_date, reason)` | Files a new leave request. Validates leave type, date range, checks balance, and rejects overlapping active requests. |
| `approve_leave(request_id, manager_id, decision, note)` | Manager-only. `manager_id` must be the requester's direct manager. `decision` is `"approved"` or `"rejected"`. Deducts balance only on approval. |
| `cancel_leave(request_id, employee_id)` | Cancels a pending or approved request (only by its owner). Refunds balance if it had been approved. |
| `leave_balance(employee_id)` | Returns remaining days per leave type. |
| `leave_history(employee_id, status)` | Lists an employee's requests, optionally filtered by status. |

Every tool returns a JSON-serializable dict:
```jsonc
// success
{ "success": true, "request": { ... } }
// failure
{ "success": false, "error_code": "OVERLAPPING_LEAVE", "error": "human readable message" }
```

Error codes: `EMPLOYEE_NOT_FOUND`, `INVALID_LEAVE_TYPE`, `INVALID_DATE_RANGE`,
`INSUFFICIENT_BALANCE`, `OVERLAPPING_LEAVE`, `LEAVE_REQUEST_NOT_FOUND`,
`NOT_AUTHORIZED`, `INVALID_STATE`.

## Setup

```bash
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## Run the tests

```bash
python -m pytest tests/ -v
```

## Run the server standalone (stdio)

```bash
python server.py
```

It will idle waiting for an MCP client to connect over stdio — that's expected.

## Connect it to Claude Desktop

Add this to your `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`,
Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "leave-management": {
      "command": "python",
      "args": ["/absolute/path/to/leave_management_mcp/server.py"]
    }
  }
}
```

Restart Claude Desktop. You should see the 5 tools available (hammer icon).
Try prompts like:
- "Create a casual leave request for E002 from 2026-08-10 to 2026-08-12"
- "As manager E001, approve leave request LR0001"
- "What's E002's leave balance?"
- "Show E002's leave history"
- "Cancel leave request LR0001 for E002"

## Notes / beginner-friendly design choices

- **Storage is in-memory** and resets each time the server restarts — this
  keeps the example dependency-free. Swap `LeaveDB` for a SQLite-backed
  class to persist data; the service/tool layers don't need to change.
- **Overlap detection** uses standard interval-overlap math
  (`existing.start <= new.end and new.start <= existing.end`) against a
  requester's own *pending or approved* requests only (cancelled/rejected
  requests don't block new ones).
- **Balance is deducted on approval, not on request creation**, and
  refunded automatically if an approved request is later cancelled.
- **Authorization for approval** checks both role (`manager`) and that the
  manager is the specific requester's direct manager — not just "any manager".
