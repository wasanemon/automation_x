# Growth Agent

Growth Agent は、X向けの投稿運用を安全に半自動化するためのMVPサービスです。

このリポジトリが担当する中心ループは次の通りです。

```text
idea
-> draft
-> evaluate
-> safety / approval 判定
-> schedule候補化、またはPostiz経由の予約投稿
-> X実投稿後のx_post_id reconcile
-> public metrics取得
-> summary / history保存
-> 次のcycleへ
```

重要な前提として、このサービスはXへ直接投稿しません。Xへの投稿・予約投稿はPostiz経由だけで行い、X APIはowned post lookupとpublic metrics取得のread-only用途だけに使います。

## 現在のデプロイ状況

現在の公開Growth Agent API:

```text
https://automation-x-kwzx.onrender.com
```

2026-05-06時点で確認済みの状態:

- 公開 `GET /health` は `{"status":"ok","database":"ok"}` を返します。
- 認証付き `GET /automation/status` はローカル外からも実行できます。
- n8n Cloud から公開HTTPSのGrowth Agent APIへ到達できます。
- n8n Cloud経由でdry-run automation cycleを実行済みです。
- 安全な初期運用状態は `AUTO_POSTING_ENABLED=false`, `SCHEDULING_DRY_RUN=true`, `AUTOMATION_KILL_SWITCH=false` です。
- 確認済みのdry-run挙動では、ローカルDBにdry-run schedule recordは作られますが、`live_scheduled_count=0` でPostiz live schedulingは呼ばれません。

本番的なlive schedulingへ進む前に、チャット、スクリーンショット、メモなどのsecret管理外に貼り付けた可能性があるDB/API credentialは必ずローテーションし、Renderとn8nの設定を更新してください。

## システム構成

Growth Agent はFastAPIで作られた小さな制御プレーンです。投稿の可否判断、状態保存、重複防止、頻度制限、Postiz schedulingの呼び出し、X投稿ID紐づけ、metrics収集を担当します。

```text
n8n Cloud
  -> HTTPS Growth Agent API on Render
      -> PostgreSQL on Render
      -> Postiz Public API for scheduling to the test X account
      -> X API read-only owned lookup and public metrics
```

各コンポーネントの役割:

- **n8n Cloud**: cron実行、手動dry-run/live workflow、将来のapproval通知や運用通知。
- **Growth Agent API**: idea ingestion、draft生成、evaluate、安全判定、重複チェック、schedule判定、run履歴、reconcile、metrics collect、summary。
- **PostgreSQL**: ideas、drafts、posts、metrics snapshots、feedback/playbook、automation run historyの永続保存。
- **Postiz**: Xへの唯一の投稿・予約投稿経路。
- **X API**: read-onlyのowned post lookupとpublic metrics取得。

## 安全境界

このMVPでは、以下を実装しません。

- 自動返信
- 自動メンション
- 自動いいね
- 自動フォロー
- 自動リポスト
- DM自動化
- キーワード反応型の営業リプライ
- X APIからの直接投稿
- private / non-public metrics取得

live schedulingが許可されるのは、すべてのgateを通過した場合だけです。

- `AUTO_POSTING_ENABLED=true`
- `SCHEDULING_DRY_RUN=false`
- `AUTOMATION_KILL_SWITCH=false`
- evaluator scoreが `AUTO_SCHEDULE_SCORE_THRESHOLD` 以上
- `risk_level=low`
- `requires_approval=false`
- duplicate / near-duplicateではない
- schedule済みdraftではない
- 投稿頻度制限内
- Postiz credentialが設定済み

URLを含むdraft/postは `has_url=true` として扱います。`OWNED_DOMAINS` が空の場合、URL付きdraftはhuman approvalが必要です。外部URL、短縮URL、価格・法務表現、強い主張、duplicate / near-duplicate、高riskのdraftは自動scheduleされません。

## Render + n8n Cloudでの本運用想定

Render Web Serviceの設定例:

- Repository: `wasanemon/automation_x`
- Branch: `codex/x-public-metrics-reconcile`
- Dockerfile path: `./Dockerfile`
- Docker build context directory: 空欄、または `.`
- container起動時に `alembic upgrade head` を実行します。
- appはRenderの `PORT` env varを尊重します。

Renderに設定する基本env var:

```text
APP_ENV=production
TESTING=false
DATABASE_URL=postgresql+psycopg://...
GROWTH_AGENT_API_KEY=<secret>
SAFE_PUBLIC_READS=false

SCHEDULING_DRY_RUN=true
AUTO_POSTING_ENABLED=false
AUTOMATION_KILL_SWITCH=false

MAX_AUTO_SCHEDULE_PER_CYCLE=1
MAX_AUTO_SCHEDULE_PER_DAY=3
MIN_HOURS_BETWEEN_AUTO_POSTS=4
DEFAULT_SCHEDULE_DELAY_MINUTES=30

REQUEST_TIMEOUT_SECONDS=10
MAX_EXTERNAL_RETRIES=2
DUPLICATE_SIMILARITY_THRESHOLD=0.88
AUTO_SCHEDULE_SCORE_THRESHOLD=80
```

Postiz/Xのlive testへ進む場合だけ追加するenv var:

```text
POSTIZ_BASE_URL=<secret/service URL>
POSTIZ_API_KEY=<secret>
POSTIZ_X_INTEGRATION_ID=<secret>
TEST_X_ACCOUNT_HANDLE=<test account handle>

X_API_BASE_URL=https://api.x.com
X_BEARER_TOKEN=<secret>
X_USER_ID=<owned test account user id>
X_RECONCILE_LOOKBACK_HOURS=48
X_RECONCILE_TEXT_SIMILARITY_THRESHOLD=0.82
```

n8n Cloud側の設定:

- n8n variable: `GROWTH_AGENT_BASE_URL=https://automation-x-kwzx.onrender.com`
- Header Auth credential名の例: `Growth Agent Header Auth`
- Header Auth credentialの中身:
  - `Name`: `X-API-Key`
  - `Value`: Renderに設定した `GROWTH_AGENT_API_KEY`
- workflow JSONは `n8n/` 配下の3つをimportします。
- import後、各HTTP Request nodeにHeader Auth credentialを選択します。

credentialやAPI keyをworkflow JSONに直書きしないでください。workflow JSONにはsecretを含めません。

推奨rollout:

1. Renderをdry-run modeのままにします。
2. n8n Cloudで `Growth Agent - Dry Run Smoke Test` を手動実行します。
3. `dry_run_scheduled_count` が増え、`live_scheduled_count=0` であることを確認します。
4. Postiz integrationがテスト用Xアカウントを指していることを確認します。
5. その後、テスト用Xアカウントでのみlive test modeに切り替えます。

```text
AUTO_POSTING_ENABLED=true
SCHEDULING_DRY_RUN=false
AUTOMATION_KILL_SWITCH=false
```

6. live cycleを1回だけ手動実行し、`live_scheduled_count=1` を確認します。
7. 確認後はsafe modeへ戻すか、頻度制限を保守的にしたまま運用します。

## ローカルクイックスタート

Postiz系envは必要に応じてあとから設定できます。まずはdry-runでローカルDBだけを使って動かします。

```bash
cp .env.example .env
python3 scripts/check_config.py
docker compose up --build
```

別ターミナルでAPI keyを読み込みます。値は表示しないでください。

```bash
export GROWTH_AGENT_API_KEY="$(grep '^GROWTH_AGENT_API_KEY=' .env | cut -d= -f2-)"
```

health checkは認証なしで確認できます。

```bash
curl http://localhost:8000/health
```

ローカルでvenv実行する場合:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python3 scripts/check_config.py
alembic upgrade head
uvicorn growth_agent.main:app --reload
```

永続DBへ接続している環境ではmigrationを適用します。

```bash
alembic upgrade head
```

## 手動APIでdry-run投稿を作る

`SCHEDULING_DRY_RUN=true` のままならPostizは呼ばれず、ローカルDBに `dry_run=true` のpost recordだけが作られます。

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

## Postiz + テスト用Xアカウントで予約投稿する

`.env` またはRender Environmentに以下を設定します。値はログ、README、テスト、標準出力に出さないでください。

- `POSTIZ_BASE_URL`
- `POSTIZ_API_KEY`
- `POSTIZ_X_INTEGRATION_ID`
- `TEST_X_ACCOUNT_HANDLE`

`POSTIZ_BASE_URL` は完全なPostiz Public API base URLです。アプリ側で `/api/public/v1` や `/public/v1` は追加しません。

```bash
python3 scripts/check_config.py
```

Postiz test scheduling configがreadyになったら、テスト用Xアカウントだけで `SCHEDULING_DRY_RUN=false` に変更してアプリを再起動します。

```bash
docker compose up --build
```

scheduleレスポンスで `dry_run=false` かつ `postiz_post_id` が入っていれば、Postiz経由の予約投稿作成まで進んでいます。

## X投稿IDreconcileとpublic metrics

Postiz経由の投稿がX上で公開された後、Growth Agentのpost recordに実投稿IDを紐づけてからmetricsを取得します。

`.env` またはRender Environmentには以下が必要です。

- `X_BEARER_TOKEN`
- `X_USER_ID`

`X_BEARER_TOKEN` はログ、README、curl例、標準出力に表示しないでください。

自動reconcileは、`X_USER_ID` の最近のowned postsをread-onlyで取得し、本文類似度と投稿時刻の近さで照合します。URLはX上で `t.co` 化されることがあるため、比較時はURLや記号差分に強い正規化を行います。

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

このMVPで取得するのはBearer Tokenで読めるpublic metricsのみです。

- `impression_count`
- `like_count`
- `retweet_count`
- `reply_count`
- `quote_count`
- `bookmark_count`

URL clicks、profile clicks、engagements、follows、`organic_metrics`、`promoted_metrics`、`non_public_metrics` は対象外です。将来、適切なuser context認証を設計してから扱います。

## 自動運転MVP

`POST /automation/run-cycle` は、手動curlの連続を1回分のcycleとして実行します。

```text
idea -> draft -> evaluate -> approval判定 -> schedule候補またはschedule -> reconcile -> metrics collect -> automation_runs保存
```

前提:

- テスト用Xアカウントだけで使います。
- X APIはread-onlyです。
- Xへの投稿はPostiz経由だけです。
- 自動返信、メンション、いいね、フォロー、リポスト、DM、keyword outreachは実装していません。
- 初期設定ではlive schedulingは走りません。

dry-run cycle:

```bash
curl http://localhost:8000/automation/status \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"

curl -X POST http://localhost:8000/automation/run-cycle \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

`GET /automation/status` はsecret値を返さず、現在のguardrailだけを返します。

- `auto_posting_enabled`
- `scheduling_dry_run`
- `kill_switch_active`
- `today_auto_scheduled_count`
- `max_auto_schedule_per_day`
- `max_auto_schedule_per_cycle`
- `min_hours_between_auto_posts`
- `warnings`

`POST /automation/run-cycle` はdry-run schedulingとlive schedulingを分けて返します。

- `auto_schedule_candidates_count`
- `dry_run_scheduled_count`
- `live_scheduled_count`
- `auto_scheduled_count`
- `approval_required_count`
- `duplicate_skipped_count`
- `frequency_limited_count`
- `metrics_skipped_count`
- `errors`

`auto_scheduled_count` は後方互換のため残しており、`dry_run_scheduled_count + live_scheduled_count` です。

CLIからcronやn8nのExecute Command nodeで呼ぶ場合:

```bash
export GROWTH_AGENT_BASE_URL=http://localhost:8000
python -m growth_agent.scripts.run_cycle
```

live scheduling cycleを許可する条件:

- `AUTO_POSTING_ENABLED=true`
- `SCHEDULING_DRY_RUN=false`
- `AUTOMATION_KILL_SWITCH=false`
- Postizの `POSTIZ_BASE_URL`, `POSTIZ_API_KEY`, `POSTIZ_X_INTEGRATION_ID` が設定済み
- evaluator scoreが `AUTO_SCHEDULE_SCORE_THRESHOLD` 以上
- `risk_level=low`
- duplicate / near-duplicateではない
- `requires_approval=false`
- schedule済みdraftではない
- 投稿頻度制限内

`AUTO_POSTING_ENABLED=false` の場合、automationはschedule候補としてlocal post recordを作りますが、Postiz live schedulingは呼びません。`SCHEDULING_DRY_RUN=true` の場合もPostizは呼びません。Postizが呼ばれるのは `AUTO_POSTING_ENABLED=true` かつ `SCHEDULING_DRY_RUN=false` かつ kill switch off の時だけです。

kill switch:

```bash
AUTOMATION_KILL_SWITCH=true
```

この状態では `POST /automation/run-cycle` はscheduleを実行せず、Postizも呼びません。draft生成、evaluate、reconcile、metrics collectは安全な範囲で進められます。レスポンスには `kill_switch_active=true` が入ります。

投稿頻度制限:

- `MAX_AUTO_SCHEDULE_PER_CYCLE=1`
- `MAX_AUTO_SCHEDULE_PER_DAY=3`
- `MIN_HOURS_BETWEEN_AUTO_POSTS=4`
- `DEFAULT_SCHEDULE_DELAY_MINUTES=30`

cron例:

```cron
*/30 * * * * cd /path/to/automation_x && GROWTH_AGENT_BASE_URL=http://localhost:8000 .venv/bin/python -m growth_agent.scripts.run_cycle
```

## n8n workflows

import可能なworkflow JSONは [n8n](n8n) にあります。

- `growth_agent_n8n_dry_run_smoke_test.json`
- `growth_agent_n8n_live_cycle.json`
- `growth_agent_n8n_metrics_catchup.json`

n8n Cloudで使う場合は、local `localhost` ではなく公開HTTPSのGrowth Agent URLを使います。

```text
n8n Cloud -> https://<your-growth-agent-domain>
```

workflow JSONは n8n variable `GROWTH_AGENT_BASE_URL` を参照します。API認証はHTTP Request nodeのHeader Auth credentialで設定します。

Header Auth credential:

- `Name`: `X-API-Key`
- `Value`: `GROWTH_AGENT_API_KEY` の値

import後は、各HTTP Request nodeでこのcredentialを選択してください。secretをworkflow JSONに直接書かないでください。

推奨workflow:

- Dry-run smoke test: Manual Trigger -> `GET /automation/status` -> dry-run gate -> `POST /automation/run-cycle` -> summary JSON。
- Live scheduled cycle: Schedule Trigger -> `GET /automation/status` -> live safety gate -> `POST /automation/run-cycle` -> `needs_attention` 付きsummary JSON。
- Metrics catch-up: Schedule Trigger -> `GET /automation/status` -> kill switch確認 -> `POST /posts/reconcile-x-ids` -> `POST /metrics/collect` -> `GET /metrics/summary`。

詳しいimport手順、n8n variable、credential設定、dry-run/live/metrics catch-upの運用手順は [docs/n8n_workflows.md](docs/n8n_workflows.md) を参照してください。

## 環境変数

| Variable | 目的 | 必要な場面 |
| --- | --- | --- |
| `DATABASE_URL` | SQLAlchemy database URL。Composeでは同梱PostgreSQL serviceに上書きされます。 | 全環境 |
| `APP_ENV` | 実行環境ラベル。 | 任意 |
| `TESTING` | `true` の時だけテスト用にAPI authを緩和します。 | テスト |
| `GROWTH_AGENT_API_KEY` | protected endpoint用API key。未設定なら `scripts/check_config.py` が生成します。 | health以外のAPI |
| `SCHEDULING_DRY_RUN` | `true` の時はlocal post recordだけを作り、Postizを呼びません。 | scheduling |
| `AUTO_POSTING_ENABLED` | automationがPostiz live schedulingを呼ぶための追加gate。 | automation live scheduling |
| `AUTOMATION_KILL_SWITCH` | `true` の時はautomation schedulingを止めます。 | automation |
| `MAX_AUTO_SCHEDULE_PER_CYCLE` | 1 cycleあたりの自動schedule上限。default `1`。 | automation |
| `MAX_AUTO_SCHEDULE_PER_DAY` | 1日あたりの自動schedule上限。default `3`。 | automation |
| `MIN_HOURS_BETWEEN_AUTO_POSTS` | 自動投稿間隔。default `4`。 | automation |
| `DEFAULT_SCHEDULE_DELAY_MINUTES` | 次の自動schedule予定時刻までのdefault delay。default `30`。 | automation |
| `GROWTH_AGENT_BASE_URL` | `python -m growth_agent.scripts.run_cycle` が使うAPI base URL。 | CLI |
| `POSTIZ_BASE_URL` | 完全なPostiz Public API base URL。 | live Postiz test |
| `POSTIZ_API_KEY` | Postiz API key。出力やdocsに出さないでください。 | live Postiz test |
| `POSTIZ_X_INTEGRATION_ID` | テスト用XアカウントのPostiz integration ID。 | live Postiz test |
| `TEST_X_ACCOUNT_HANDLE` | テスト用アカウントのhuman-readable guardrail。 | live Postiz test |
| `OWNED_DOMAINS` | owned domainをカンマ区切りで指定します。 | 任意 |
| `SAFE_PUBLIC_READS` | 明示的に `true` の時だけnon-health GETを公開読み取り可にします。 | 任意 |
| `AUTO_APPLY_TENTATIVE_RULES` | 将来のtentative rule automation用。default off。 | 任意 |
| `X_API_BASE_URL` | X API base URL。 | metrics |
| `X_BEARER_TOKEN` | owned lookupとmetrics用のread-only X token。 | metrics |
| `X_USER_ID` | owned X user ID。 | metrics lookup |
| `X_RECONCILE_LOOKBACK_HOURS` | X ID reconcileのowned-post lookup window。 | 任意 |
| `X_RECONCILE_TEXT_SIMILARITY_THRESHOLD` | automatic reconcileの本文類似度しきい値。 | 任意 |
| `REQUEST_TIMEOUT_SECONDS` | 外部HTTP timeout。 | 外部API |
| `MAX_EXTERNAL_RETRIES` | bounded retry回数。 | 外部API |
| `DUPLICATE_SIMILARITY_THRESHOLD` | schedule前のnear-duplicate判定しきい値。 | scheduling |
| `AUTO_SCHEDULE_SCORE_THRESHOLD` | low-risk draftをauto scheduleする最低score。 | scheduling |

X credentialsはapp起動、dry-run、Postiz schedulingには必須ではありません。未設定の場合、metrics collectionは安全にskipされます。

## その他のAPI例

reject:

```bash
curl -X POST http://localhost:8000/drafts/1/reject \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"marketing","reason":"Too speculative."}'
```

feedback実行とplaybook確認:

```bash
curl -X POST http://localhost:8000/feedback/run \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"

curl http://localhost:8000/feedback/playbook \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

weekly report:

```bash
curl http://localhost:8000/reports/weekly \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

## 品質チェック

```bash
python -m pytest -q
python -m ruff check .
docker compose config --quiet
```

テストではPostiz/X clientをmockするため、実際の外部APIは呼びません。Compose validationはplain `docker compose config` ではenv値が表示される可能性があるため、`--quiet` を使います。

## トラブルシュート

- `401 Invalid or missing API key`: `X-API-Key: $GROWTH_AGENT_API_KEY` を送っているか確認してください。`GROWTH_AGENT_API_KEY` がない場合は `python3 scripts/check_config.py` で生成できます。
- `Postiz test scheduling config: not ready`: Postizの4つのenv varを設定してから `python3 scripts/check_config.py` を再実行してください。
- scheduleレスポンスが `dry_run=true`: `SCHEDULING_DRY_RUN=true` です。テスト用Xアカウントでlive schedulingする時だけ `false` にします。
- metricsが `collected=0`: `X_BEARER_TOKEN` と `X_USER_ID` を確認してください。未設定でもapp起動やdry-runは壊れません。
- URL付きdraftがapproval requiredになる: `OWNED_DOMAINS` を設定してください。ただし外部URLや短縮URLは引き続きhuman approvalが必要です。
- n8n Cloudから `localhost` に到達できない: n8n CloudはローカルPC上の `localhost` を見られません。Renderなどの公開HTTPS URLを `GROWTH_AGENT_BASE_URL` に設定してください。
