# Architecture

The Growth Agent service coordinates the marketing learning loop while delegating publishing to Postiz and workflow/human approval to n8n.

```mermaid
flowchart LR
    Sources[Idea Sources] --> N8N[n8n Workflows]
    N8N -->|POST /ideas/ingest| Agent[Growth Agent FastAPI]
    Agent --> DB[(PostgreSQL)]
    Agent --> Drafts[Draft Generator]
    Drafts --> Agent
    Agent --> Evaluator[Evaluator and Safety Gate]
    Evaluator -->|low risk, score >= 80, not duplicate| Scheduler[Schedule Decision]
    Evaluator -->|requires approval| N8NApproval[n8n Human Approval]
    N8NApproval -->|approve/reject| Agent
    Scheduler -->|dry-run local post| DB
    Scheduler -->|POST /posts when dry-run=false| Postiz[Postiz Public API]
    Postiz --> X[X Test Account]
    Agent -->|owned lookup and metrics only| XAPI[X API]
    XAPI --> Agent
    Agent --> Metrics[Metrics Collector]
    Metrics --> DB
    Agent --> Feedback[Feedback Engine]
    Feedback --> Playbook[Playbook Rules]
    Playbook --> Drafts
    Agent --> Reports[Weekly Report]
    Reports --> N8N
```

## Service Boundaries

- Growth Agent stores ideas, drafts, posts, metrics, experiments, playbook rules, and feedback runs.
- Postiz handles publishing and scheduling to the configured test X account.
- n8n handles orchestration, human approvals, timers, and notifications.
- X API is read-only in this MVP and used only for owned post reconciliation and public metrics collection.
- `GET /health` is public. Other endpoints require `GROWTH_AGENT_API_KEY` unless `TESTING=true`; non-health GET endpoints can be made public only with `SAFE_PUBLIC_READS=true`.

## Configuration Flow

`growth_agent.config.Settings` loads from environment variables and `.env`. `scripts/check_config.py` can create `.env` from `.env.example`, generate `GROWTH_AGENT_API_KEY`, and fill safe non-secret defaults.

Postiz variables are treated as user-provided:

- `POSTIZ_BASE_URL`
- `POSTIZ_API_KEY`
- `POSTIZ_X_INTEGRATION_ID`
- `TEST_X_ACCOUNT_HANDLE`

`POSTIZ_BASE_URL` is the complete Public API base URL, for example `https://api.postiz.com/public/v1`. The app appends only `/posts`.

## Data Flow

1. n8n or an operator ingests ideas through `POST /ideas/ingest`.
2. Growth Agent generates deterministic MVP drafts from the idea plus active playbook rules.
3. The evaluator scores risk, tags URL-bearing drafts, and checks duplicate or near-duplicate content.
4. Safe low-risk drafts can be scheduled; medium/high-risk drafts require human approval.
5. In dry-run, scheduling creates a local post record with `dry_run=true`.
6. In live test mode, scheduling creates a local in-progress record before calling Postiz, then stores the Postiz post ID.
7. Scheduled posts can be reconciled with owned X IDs manually, or automatically by comparing normalized text similarity plus scheduled/created time proximity against recent owned X posts.
8. Metrics snapshots store public metrics from X only: impressions, likes, replies, reposts, quotes, and bookmarks. Missing X credentials cause metrics collection to skip safely and do not affect startup, dry-run, or Postiz scheduling.
9. Feedback updates playbook rule weights and informs later draft generation.

Private metrics, URL clicks, profile clicks, organic metrics, promoted metrics, and non-public metrics are future extensions that require an appropriate user-context authentication boundary.
