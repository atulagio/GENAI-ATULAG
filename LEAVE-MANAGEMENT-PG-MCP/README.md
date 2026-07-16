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
 database.py             <- Postgres-backed store (psycopg2 + pool)
 database_memory.py       <- In-memory store, same interface — used by tests
        │  uses
        ▼
 models.py               <- Dataclasses: Employee, LeaveRequest, enums
```

`leave_service.py` and `server.py` never import a specific backend directly
by name — `server.py` does `from database import LeaveDB`, and both
`database.py` (Postgres) and `database_memory.py` (in-memory) expose the
exact same `LeaveDB` interface:
`get_employee`, `create_leave_request`, `get_leave_request`,
`requests_for_employee`, `active_requests_for_employee`,
`update_leave_balance`, `update_request`. That's what lets production run
on Postgres while unit tests run instantly against the in-memory version
with zero setup.

## Data model

- **Employee**: `id`, `name`, `role` (`employee` | `manager`), `manager_id`,
  `leave_balance` (JSONB in Postgres — per leave type: sick / casual / earned)
- **LeaveRequest**: `id`, `employee_id`, `leave_type`, `start_date`,
  `end_date`, `reason`, `status` (`pending` | `approved` | `rejected` | `cancelled`),
  `approved_by`, `decision_note`

Seed data (`schema.sql`, and mirrored in `database_memory.py`) creates a
small org:
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

### 1. Start Postgres

Easiest option — Docker:
```bash
docker compose up -d
```
This starts Postgres on `localhost:5432` and automatically runs `schema.sql`
on first boot (via the compose file's init-script mount).

No Docker? Point `schema.sql` at any Postgres 13+ instance you already have:
```bash
createuser leave_user -P          # set password to leave_pass, or use your own
createdb leave_management -O leave_user
psql "postgresql://leave_user:leave_pass@localhost:5432/leave_management" -f schema.sql
```

### 2. Configure the connection string

```bash
export DATABASE_URL="postgresql://leave_user:leave_pass@localhost:5432/leave_management"
```
(or create a `.env` file / set it in your `claude_desktop_config.json` — see below).
If unset, `database.py` defaults to that same local connection string.

## Run the tests

Unit tests run against the **in-memory** backend (`database_memory.py`) —
fast, no Postgres required:
```bash
python -m pytest tests/ -v
```

## Run the server standalone (stdio)

```bash
export DATABASE_URL="postgresql://leave_user:leave_pass@localhost:5432/leave_management"
python server.py
```

It will idle waiting for an MCP client to connect over stdio — that's expected.
If it exits immediately with a connection error, double check Postgres is
running and `schema.sql` has been applied.

## Connect it to Claude Desktop

Add this to your `claude_desktop_config.json`
(macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`,
Windows: `%APPDATA%\Claude\claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "leave-management": {
      "command": "python",
      "args": ["/absolute/path/to/leave_management_mcp/server.py"],
      "env": {
        "DATABASE_URL": "postgresql://leave_user:leave_pass@localhost:5432/leave_management"
      }
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

## Notes / design choices

- **Storage is Postgres** (`database.py`), persisted across restarts. Unit
  tests use `database_memory.py` instead — same interface, zero setup, fast.
  If you ever want to add a third backend (SQLite, etc.), implement the
  same seven methods and nothing in `server.py` or `leave_service.py` changes.
- **Balance updates are atomic in Postgres** (`update_leave_balance` does a
  single `UPDATE ... jsonb_set` inside the database) so two concurrent
  approvals for the same employee can't race and silently drop one.
- **Overlap detection still runs in Python** (`leave_service.py`), checking
  against the requester's own *pending or approved* requests. `schema.sql`
  includes a commented-out `EXCLUDE USING gist` constraint if you want
  Postgres itself to reject overlaps too, as defense in depth against
  concurrent requests racing past the Python check.
- **Overlap detection** uses standard interval-overlap math
  (`existing.start <= new.end and new.start <= existing.end`) against a
  requester's own *pending or approved* requests only (cancelled/rejected
  requests don't block new ones).
- **Balance is deducted on approval, not on request creation**, and
  refunded automatically if an approved request is later cancelled.
- **Authorization for approval** checks both role (`manager`) and that the
  manager is the specific requester's direct manager — not just "any manager".

## CI/CD (GitHub Actions)

This is a **monorepo** — the project lives in `LEAVE-MANAGEMENT-PG-MCP/`,
not at the git repo root, and `.github/workflows/` sits at the repo root
(GitHub Actions only discovers workflows there). Both workflows account
for this: every `run:` step executes inside the project subfolder via
`defaults.run.working-directory`, and steps that don't respect that
(`docker/build-push-action`) get an explicit path instead.

- **`ci.yml`** — runs on every push/PR to `main` that touches
  `LEAVE-MANAGEMENT-PG-MCP/**`. Spins up a real Postgres service
  container, applies `schema.sql`, runs the unit test suite (in-memory
  backend) plus a Postgres connectivity smoke test, lints with `ruff`,
  and validates the Dockerfile builds.
- **`cd.yml`** — on push to `main`: builds the image, pushes it to GitHub
  Container Registry (`ghcr.io/<owner>/<repo>`), authenticates to AWS,
  points `kubectl` at your EKS cluster, syncs the `leave-mcp-secrets`
  Kubernetes Secret from `DATABASE_URL` (your Neon connection string),
  and rolls out the new image.

### GitHub setup

Settings → Secrets and variables → Actions → **Secrets** tab:

| Name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | From your IAM user |
| `AWS_SECRET_ACCESS_KEY` | From your IAM user |
| `AWS_REGION` | e.g. `ap-south-1` |
| `EKS_CLUSTER_NAME` | Your cluster's name |
| `DATABASE_URL` | Your Neon connection string — see below |

(`AWS_REGION` / `EKS_CLUSTER_NAME` aren't sensitive and would conventionally
go in the **Variables** tab instead, but since you've already added all
four to **Secrets**, `cd.yml` reads all four as `secrets.*` to match —
functionally identical either way, just has to be consistent.)

Also: Settings → Actions → General → Workflow permissions → **Read and write
permissions** (needed so the workflow can push to GHCR using the built-in
`GITHUB_TOKEN` — no separate secret needed for that part).

### Neon database

Get your connection string from the Neon dashboard (Project → Connection
Details) — it looks like:
```
postgresql://<user>:<password>@ep-xxxx-xxxx.<region>.aws.neon.tech/leave_management?sslmode=require
```
Use that exact string as the `DATABASE_URL` secret. Then apply the schema
once — easiest from your own machine, no cluster needed for this step:
```bash
psql "postgresql://<user>:<password>@ep-xxxx.<region>.aws.neon.tech/leave_management?sslmode=require" -f LEAVE-MANAGEMENT-PG-MCP/schema.sql
```
Since Neon is managed and external to the cluster, `k8s/postgres.yaml`
(in-cluster Postgres) is **not** applied by `cd.yml` or `kustomization.yaml`
— it's kept in the repo only as a fallback if you ever self-host Postgres
instead.

### Two things that trip people up on EKS specifically

1. **The IAM user needs cluster RBAC access, separately from AWS IAM
   permissions.** Being able to call the AWS API isn't the same as being
   allowed to run `kubectl` commands against the cluster. Whoever created
   the EKS cluster needs to grant your IAM user access — either via an
   **EKS access entry** (`aws eks create-access-entry` /
   `aws eks associate-access-policy`, the modern approach) or by editing
   the `aws-auth` ConfigMap (older clusters). Without this, every
   `kubectl` command in the workflow fails with `Unauthorized`, even
   though the `aws eks update-kubeconfig` step itself succeeds.
2. **EKS nodes can't pull from a private GHCR package** without
   credentials. Either make the package public (repo → Packages →
   package settings → Change visibility), or create a pull secret and
   uncomment `imagePullSecrets` in `k8s/deployment.yaml` — instructions
   are in a comment right above it.

## Kubernetes deployment

MCP's default `stdio` transport only works when a client spawns the server
as a local subprocess — it has no network component, so it can't run in a
pod. This project's `server.py` also supports `streamable-http`
(controlled by the `MCP_TRANSPORT` env var), which listens on a real TCP
port and is what the Docker image and K8s manifests use. Point any MCP
client that supports remote/HTTP servers at `http://<service>/mcp` (or
your Ingress host) instead of a local command.

Manifests live in `k8s/` (all commands below assume you've `cd`'d into
`LEAVE-MANAGEMENT-PG-MCP/` first):

| File | Purpose |
|---|---|
| `namespace.yaml` | Creates the `leave-management` namespace |
| `configmap.yaml` | Non-secret config (transport, host, port) |
| `secret.example.yaml` | Template for the `DATABASE_URL` (Neon) secret — see instructions inside, don't commit real values |
| `postgres.yaml` | In-cluster Postgres fallback — **not used** while on Neon, kept for reference only |
| `migrate-job.yaml` | Optional: applies `schema.sql` as a cluster Job. Usually simpler to just run `psql` from your own machine instead (see above) |
| `deployment.yaml` | The MCP server itself — 2 replicas, health probes, resource limits, waits for the DB via initContainer |
| `service.yaml` | ClusterIP Service in front of the deployment |
| `ingress.yaml` | Optional — external HTTPS access via an ingress controller |
| `kustomization.yaml` | Ties the above together for `kubectl apply -k k8s/` (excludes `postgres.yaml` and `migrate-job.yaml`) |

### Deploy steps (manual — `cd.yml` automates all of this on every push to `main`)

```bash
cd LEAVE-MANAGEMENT-PG-MCP

# 1. Namespace + secret
kubectl apply -f k8s/namespace.yaml
kubectl create secret generic leave-mcp-secrets \
  --namespace leave-management \
  --from-literal=DATABASE_URL='postgresql://user:password@ep-xxxx.region.aws.neon.tech/leave_management?sslmode=require'

# 2. Schema (once, against Neon directly — no cluster involvement needed)
psql "$DATABASE_URL" -f schema.sql

# 3. App
kubectl apply -k k8s/
kubectl set image deployment/leave-mcp-server \
  leave-mcp-server=ghcr.io/<owner>/<repo>:latest -n leave-management
kubectl rollout status deployment/leave-mcp-server -n leave-management
```

### Verify it's running

```bash
kubectl get pods -n leave-management
kubectl port-forward svc/leave-mcp-server 8000:80 -n leave-management
# then, from another terminal, point an MCP HTTP client at http://localhost:8000/mcp
```

### Notes on the Kubernetes setup

- **Two replicas by default.** Since all state lives in Postgres (not in
  the app process), the deployment scales horizontally with no extra work.
- **Probes use `tcpSocket`**, not an HTTP health path, since `streamable-http`
  doesn't expose a plain health endpoint out of the box — a successful TCP
  connect to port 8000 is a reasonable proxy for "the server is up."
- **`readOnlyRootFilesystem: true`** is set in `deployment.yaml` as a
  security hardening default. If you later add code that writes to local
  disk (logs, temp files), remove that line or add a mounted `emptyDir`.
- **In-cluster Postgres vs. managed DB:** you're on Neon now, so
  `postgres.yaml` is unused — kept only as a reference if you ever want to
  self-host Postgres in-cluster instead. Switching back just means
  applying that file and updating the `DATABASE_URL` secret to point at
  the in-cluster service instead of Neon.