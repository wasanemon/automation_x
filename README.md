# Growth Agent

A production-minded MVP service for a semi-autonomous X marketing Growth Agent.

The service owns the growth loop:

idea collection -> draft generation -> evaluation and safety gate -> scheduling or human approval -> post tracking -> metrics collection -> feedback/playbook update -> next draft generation.

Postiz is the publishing/scheduling layer. n8n is the workflow and human-approval layer. This repo is the Growth Agent service.

## Setup

Prerequisites:

- Python 3.12+
- Docker and Docker Compose
- PostgreSQL, or the included Compose database

Local install:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn growth_agent.main:app --reload
```

Docker Compose:

```bash
cp .env.example .env
docker compose up --build
```

The app listens on `http://localhost:8000`.

## Environment Variables

| Variable | Purpose |
| --- | --- |
| `DATABASE_URL` | SQLAlchemy database URL. Compose uses PostgreSQL. |
| `POSTIZ_BASE_URL` | Postiz public API base URL, for example `https://postiz.example.com/public/v1`. |
| `POSTIZ_API_KEY` | Postiz API key. Keep it out of code and logs. |
| `POSTIZ_X_INTEGRATION_ID` | Postiz integration ID for the owned X account. |
| `X_API_BASE_URL` | X API base URL. Defaults to `https://api.x.com`. |
| `X_BEARER_TOKEN` | Read-only token for owned post lookup and metrics collection. |
| `X_USER_ID` | Owned X user ID used for timeline lookup. |
| `HTTP_TIMEOUT_SECONDS` | External HTTP timeout. |
| `HTTP_MAX_RETRIES` | Bounded retry count for transient external failures. |
| `DUPLICATE_SIMILARITY_THRESHOLD` | Near-duplicate threshold before scheduling. |
| `AUTO_SCHEDULE_SCORE_THRESHOLD` | Minimum score for auto-scheduling low-risk drafts. |

## Quality Checks

```bash
pytest
ruff check .
```

Tests override Postiz and X clients, so no real external API calls are made.

## Curl Examples

Health:

```bash
curl http://localhost:8000/health
```

Ingest an idea:

```bash
curl -X POST http://localhost:8000/ideas/ingest \
  -H 'Content-Type: application/json' \
  -d '{"title":"Launch lesson","description":"Turn onboarding notes into one useful post.","source":"n8n","audience":"founders"}'
```

List ideas:

```bash
curl http://localhost:8000/ideas
```

Generate drafts:

```bash
curl -X POST http://localhost:8000/drafts/generate \
  -H 'Content-Type: application/json' \
  -d '{"idea_id":1,"count":3}'
```

Evaluate a draft:

```bash
curl -X POST http://localhost:8000/drafts/1/evaluate
```

Approve or reject:

```bash
curl -X POST http://localhost:8000/drafts/1/approve \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"marketing","note":"Safe for this week."}'

curl -X POST http://localhost:8000/drafts/1/reject \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"marketing","reason":"Too speculative."}'
```

Schedule:

```bash
curl -X POST http://localhost:8000/drafts/1/schedule \
  -H 'Content-Type: application/json' \
  -d '{"scheduled_for":"2026-05-02T09:00:00Z"}'
```

List posts:

```bash
curl http://localhost:8000/posts
```

Reconcile X IDs manually:

```bash
curl -X POST http://localhost:8000/posts/reconcile-x-ids \
  -H 'Content-Type: application/json' \
  -d '{"mappings":[{"post_id":1,"x_post_id":"1234567890"}]}'
```

Reconcile by owned X lookup:

```bash
curl -X POST http://localhost:8000/posts/reconcile-x-ids \
  -H 'Content-Type: application/json' \
  -d '{"lookback_days":7}'
```

Collect metrics and summarize:

```bash
curl -X POST http://localhost:8000/metrics/collect \
  -H 'Content-Type: application/json' \
  -d '{}'

curl http://localhost:8000/metrics/summary
```

Run feedback and view playbook:

```bash
curl -X POST http://localhost:8000/feedback/run
curl http://localhost:8000/feedback/playbook
```

Weekly report:

```bash
curl http://localhost:8000/reports/weekly
```

## n8n Workflow Outline

1. Idea intake workflow receives form, Slack, CRM, or research inputs and calls `POST /ideas/ingest`.
2. Draft workflow calls `POST /drafts/generate` and evaluates each draft with `POST /drafts/{id}/evaluate`.
3. Safety branch auto-schedules low-risk drafts that pass thresholds, or sends approval-required drafts to a human approval task.
4. Approval workflow calls `POST /drafts/{id}/approve` or `POST /drafts/{id}/reject`, then schedules approved drafts.
5. Metrics workflow runs on a timer, reconciles X IDs, and calls `POST /metrics/collect`.
6. Learning workflow calls `POST /feedback/run`, reads `GET /feedback/playbook`, and uses the playbook in the next generation cycle.
7. Weekly workflow sends `GET /reports/weekly` to the team.

## Safety Boundaries

This MVP never automates replies, mentions, likes, follows, retweets, DMs, or keyword-triggered outreach. X API usage is read-only and limited to owned post lookup and metrics collection.
