# Growth Agent

A production-minded MVP service for a semi-autonomous X marketing Growth Agent.

The service owns this loop:

idea collection -> draft generation -> evaluation and safety gate -> scheduling or human approval -> post tracking -> metrics collection -> feedback/playbook update -> next draft generation.

Postiz is the publishing/scheduling layer. n8n is the workflow and human-approval layer. This repo is the Growth Agent service.

## Quick Start: まずdry-runで動かす

Postiz系のenvはユーザー入力済みである前提です。Postiz以外のMVP用envは `scripts/check_config.py` が安全に補完できます。

```bash
cp .env.example .env
python3 scripts/check_config.py
docker compose up --build
```

別ターミナルでAPIキーを読み込みます。値は表示しないでください。

```bash
export GROWTH_AGENT_API_KEY="$(grep '^GROWTH_AGENT_API_KEY=' .env | cut -d= -f2-)"
```

Health checkは認証なしで確認できます。

```bash
curl http://localhost:8000/health
```

dry-runでは `SCHEDULING_DRY_RUN=true` のままにします。Postizは呼ばれず、ローカルDBに `dry_run=true` のpost recordが作成されます。

```bash
curl -X POST http://localhost:8000/ideas/ingest \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"title":"Launch lesson","description":"Turn onboarding notes into one useful post.","source":"manual","audience":"founders"}'

curl -X POST http://localhost:8000/drafts/generate \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"idea_id":1,"count":1}'

curl -X POST http://localhost:8000/drafts/1/evaluate \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"

curl -X POST http://localhost:8000/drafts/1/approve \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"test","note":"Dry-run approval."}'

curl -X POST http://localhost:8000/drafts/1/schedule \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"scheduled_for":"2026-05-02T09:00:00Z"}'

curl http://localhost:8000/posts \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

## Quick Start: Postiz + テスト用Xアカウントで予約投稿する

`.env` に以下が入っていることを確認します。値はログやREADMEに書かないでください。

- `POSTIZ_BASE_URL`
- `POSTIZ_API_KEY`
- `POSTIZ_X_INTEGRATION_ID`
- `TEST_X_ACCOUNT_HANDLE`

`POSTIZ_BASE_URL` は完全なPublic API base URLです。例: `https://api.postiz.com/public/v1`。アプリはこのbase URLに `/posts` だけを追加します。

```bash
python3 scripts/check_config.py
```

`Postiz test scheduling config: ready` になったら、`.env` の `SCHEDULING_DRY_RUN=false` に変更してアプリを再起動します。

```bash
docker compose up --build
```

その後、dry-runと同じ `idea -> draft -> evaluate -> approve -> schedule -> posts確認` を実行します。scheduleレスポンスで `dry_run=false` かつ `postiz_post_id` が入っていれば、Postiz経由の予約作成まで進んでいます。

## X投稿ID紐づけとpublic metrics

Postiz経由の投稿がX上で公開された後、Growth Agentのpost recordに実投稿IDを紐づけてからmetricsを取得します。`.env` には `X_BEARER_TOKEN` と `X_USER_ID` が設定済みである前提です。`X_BEARER_TOKEN` はログ、README、curl例、標準出力に表示しないでください。

自動紐づけは、`X_USER_ID` の最近のowned postsをread-onlyで取得し、本文類似度と投稿時刻の近さで照合します。URLはX上で `t.co` 化されることがあるため、比較時はURLや記号差分に強い正規化を行います。

```bash
curl -X POST http://localhost:8000/posts/reconcile-x-ids \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{}'
```

X投稿IDが分かっている場合はmanual mappingを使えます。既存の `x_post_id` を上書きする場合だけ `force=true` を指定します。

```bash
curl -X POST http://localhost:8000/posts/reconcile-x-ids \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"mappings":[{"post_id":1,"x_post_id":"1234567890123456789"}]}'
```

public metricsを保存します。`post_id` を指定すると、そのpostだけを取得します。

```bash
curl -X POST http://localhost:8000/metrics/collect \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{}'

curl -X POST http://localhost:8000/metrics/collect \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"post_id":1}'
```

保存済みsnapshotのsummaryを確認します。

```bash
curl http://localhost:8000/metrics/summary \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

今回取得するのはBearer Tokenで読めるpublic metricsのみです。`impression_count`, `like_count`, `retweet_count`, `reply_count`, `quote_count`, `bookmark_count` を保存します。URL clicks、profile clicks、engagements、follows、`organic_metrics`、`promoted_metrics`、`non_public_metrics` は今回は対象外です。これらは将来、適切なuser context認証を追加してから扱います。

## Environment Variables

| Variable | Purpose | Required when |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy database URL. Compose overrides this to the included PostgreSQL service. | all modes |
| `APP_ENV` | Runtime label. | optional |
| `TESTING` | Allows tests to bypass API auth only when `true`. | tests |
| `GROWTH_AGENT_API_KEY` | API key for protected endpoints. Generated when missing. | all non-health API calls |
| `SCHEDULING_DRY_RUN` | When `true`, creates local post records and does not call Postiz. | scheduling |
| `POSTIZ_BASE_URL` | Full Postiz Public API base URL. | live Postiz test |
| `POSTIZ_API_KEY` | Postiz API key. Mask in all output. | live Postiz test |
| `POSTIZ_X_INTEGRATION_ID` | Postiz integration ID for the test X account. | live Postiz test |
| `TEST_X_ACCOUNT_HANDLE` | Human-readable test account guardrail. | live Postiz test |
| `OWNED_DOMAINS` | Comma-separated owned domains for lower-risk URL posts. | optional |
| `SAFE_PUBLIC_READS` | Allows non-health GET endpoints without auth only when explicitly `true`. | optional |
| `AUTO_APPLY_TENTATIVE_RULES` | Reserved for future tentative-rule automation. Default off. | optional |
| `X_API_BASE_URL` | X API base URL. | metrics |
| `X_BEARER_TOKEN` | Read-only X token for owned lookup and metrics. | metrics |
| `X_USER_ID` | Owned X user ID. | metrics lookup |
| `X_RECONCILE_LOOKBACK_HOURS` | Default owned-post lookup window for X ID reconciliation. | optional |
| `X_RECONCILE_TEXT_SIMILARITY_THRESHOLD` | Minimum normalized text similarity for automatic X ID reconciliation. | optional |
| `REQUEST_TIMEOUT_SECONDS` | External HTTP timeout. | external calls |
| `MAX_EXTERNAL_RETRIES` | Bounded retry count. | external calls |
| `DUPLICATE_SIMILARITY_THRESHOLD` | Near-duplicate threshold before scheduling. | scheduling |
| `AUTO_SCHEDULE_SCORE_THRESHOLD` | Minimum score for auto-scheduling low-risk drafts. | scheduling |

X credentials are not required for app startup, dry-run, or Postiz scheduling. If they are missing, metrics collection skips safely.

## Setup

Prerequisites:

- Python 3.12+
- Docker and Docker Compose
- PostgreSQL, or the included Compose database

Local install:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python3 scripts/check_config.py
alembic upgrade head
uvicorn growth_agent.main:app --reload
```

Docker Compose:

```bash
cp .env.example .env
python3 scripts/check_config.py
docker compose up --build
```

The app listens on `http://localhost:8000`.

## Additional API Examples

Reject:

```bash
curl -X POST http://localhost:8000/drafts/1/reject \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"marketing","reason":"Too speculative."}'
```

Reconcile X IDs automatically:

```bash
curl -X POST http://localhost:8000/posts/reconcile-x-ids \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{}'
```

Reconcile X IDs manually:

```bash
curl -X POST http://localhost:8000/posts/reconcile-x-ids \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"mappings":[{"post_id":1,"x_post_id":"1234567890123456789"}]}'
```

Collect metrics and summarize:

```bash
curl -X POST http://localhost:8000/metrics/collect \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{}'

curl http://localhost:8000/metrics/summary \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

Run feedback and view playbook:

```bash
curl -X POST http://localhost:8000/feedback/run \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"

curl http://localhost:8000/feedback/playbook \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

Weekly report:

```bash
curl http://localhost:8000/reports/weekly \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

## Safety Boundaries

This MVP never automates replies, mentions, likes, follows, retweets, DMs, or keyword-triggered outreach. X API usage is read-only and limited to owned post lookup and metrics collection.

URL-bearing drafts and posts are marked `has_url=true`. If `OWNED_DOMAINS` is empty, URL-bearing drafts require human approval. External URLs, short links, pricing/legal language, strong claims, duplicate drafts, and near-duplicates require human approval or are blocked from scheduling.

## Quality Checks

```bash
pytest
ruff check .
docker compose config --quiet
```

Tests override Postiz and X clients, so no real external API calls are made.
Use `--quiet` for Compose validation because plain `docker compose config` can render environment values.

## Troubleshooting

- `401 Invalid or missing API key`: send `X-API-Key: $GROWTH_AGENT_API_KEY`, or confirm `GROWTH_AGENT_API_KEY` exists with `python3 scripts/check_config.py`.
- `Postiz test scheduling config: not ready`: set the four Postiz env vars in `.env`, then rerun `python3 scripts/check_config.py`.
- Schedule response has `dry_run=true`: `SCHEDULING_DRY_RUN=true`; set it to `false` only for test-account live scheduling.
- Metrics returns `collected=0`: set `X_BEARER_TOKEN` and `X_USER_ID`, or leave metrics skipped during the Postiz scheduling smoke test.
- URL drafts require approval: set `OWNED_DOMAINS` for owned URLs; external or shortened URLs still require human approval.
