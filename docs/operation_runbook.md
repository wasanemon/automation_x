# Growth Agent 運用 Runbook

このファイルは、Growth Agentを日常運用するための最短手順です。詳細設計は `README.md`、MCP詳細は `docs/codex_mcp.md`、n8n詳細は `docs/n8n_workflows.md` を参照してください。

## 基本方針

- Codex / ChatGPTは「思考と生成」を担当します。
- Growth Agent DBは「公式な記憶」を担当します。
- X APIはread-onlyです。
- Xへの予約投稿・投稿はPostiz経由だけです。
- 自動返信、メンション、いいね、フォロー、リポスト、DM、キーワード営業リプライは実装・運用しません。
- secretはREADME、workflow JSON、ログ、チャットに書きません。

## 通常の安全状態

テストや確認が終わったら、Renderではこの状態に戻します。

```text
AUTO_POSTING_ENABLED=false
SCHEDULING_DRY_RUN=true
AUTOMATION_KILL_SWITCH=false
MAX_AUTO_SCHEDULE_PER_DAY=3
```

完全にscheduleを止めたい場合:

```text
AUTOMATION_KILL_SWITCH=true
```

## デプロイ後に必ず行うこと

PRをmergeしてRenderへdeployした後、DB migrationを反映します。Renderの起動コマンドで `alembic upgrade head` が走る構成なら再デプロイで反映されます。

ローカルDocker Compose DBへ反映する場合:

```bash
cd /Users/hitodekai/Desktop/automation_x
docker compose exec -T app alembic upgrade head
```

Render Shellが使える場合:

```bash
alembic upgrade head
```

## 状態確認

API keyは環境変数に入れてから使います。画面に表示しない入力方法:

```bash
read -s GROWTH_AGENT_API_KEY
export GROWTH_AGENT_API_KEY
```

Renderの状態確認:

```bash
curl https://automation-x-kwzx.onrender.com/automation/status \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

見るポイント:

- `auto_posting_enabled`
- `scheduling_dry_run`
- `kill_switch_active`
- `today_auto_scheduled_count`
- `max_auto_schedule_per_day`
- `next_post_available_at`
- `warnings`

## Codex MCPを使う

Codex MCP登録済みか確認:

```bash
/Users/hitodekai/.cursor/extensions/openai.chatgpt-26.429.30905-darwin-arm64/bin/macos-aarch64/codex mcp list
```

未登録の場合:

```bash
read -s GA_KEY

/Users/hitodekai/.cursor/extensions/openai.chatgpt-26.429.30905-darwin-arm64/bin/macos-aarch64/codex mcp add growth-agent \
  --env GROWTH_AGENT_BASE_URL=https://automation-x-kwzx.onrender.com \
  --env GROWTH_AGENT_API_KEY="$GA_KEY" \
  --env GROWTH_AGENT_MCP_TIMEOUT_SECONDS=10 \
  -- /Users/hitodekai/Desktop/automation_x/.venv/bin/python -m growth_agent.mcp_server
```

Codexに依頼する文例:

```text
growth-agent MCPを使って get_memory_context と get_automation_status を確認して。
その内容をもとに仮説を1つ作り、ideaを作成し、240文字以内のdraftを1つimportして。
URLなし、価格/法務/強いclaimなし、requires_human_review_by_model=false、confidence=0.85以上にして。
SCHEDULING_DRY_RUN=true の場合だけ run_dry_cycle を実行して。
live schedulingは絶対に実行しないで。
```

## dry-run運用

Render env:

```text
AUTO_POSTING_ENABLED=false
SCHEDULING_DRY_RUN=true
AUTOMATION_KILL_SWITCH=false
```

実行方法:

```bash
curl -X POST https://automation-x-kwzx.onrender.com/automation/run-cycle \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

成功の目安:

```text
dry_run=true
dry_run_scheduled_count=1
live_scheduled_count=0
errors=[]
```

dry-run post確認:

```bash
curl https://automation-x-kwzx.onrender.com/posts \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

## live test運用

live testはテスト用Xアカウントでだけ行います。事前にPostizのcalendarで対象アカウントが正しいことを確認します。

Render env:

```text
AUTO_POSTING_ENABLED=true
SCHEDULING_DRY_RUN=false
AUTOMATION_KILL_SWITCH=false
MAX_AUTO_SCHEDULE_PER_CYCLE=1
MAX_AUTO_SCHEDULE_PER_DAY=3
MIN_HOURS_BETWEEN_AUTO_POSTS=4
```

Postiz/X envが設定済みであること:

```text
POSTIZ_BASE_URL
POSTIZ_API_KEY
POSTIZ_X_INTEGRATION_ID
TEST_X_ACCOUNT_HANDLE
X_BEARER_TOKEN
X_USER_ID
```

1回だけcycleを実行:

```bash
curl -X POST https://automation-x-kwzx.onrender.com/automation/run-cycle \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

成功の目安:

```text
dry_run=false
live_scheduled_count=1
dry_run_scheduled_count=0
errors=[]
```

Postiz calendarで予約が入ったことを確認します。確認後、安全状態へ戻します。

```text
AUTO_POSTING_ENABLED=false
SCHEDULING_DRY_RUN=true
MAX_AUTO_SCHEDULE_PER_DAY=3
```

## 実投稿後のreconcileとmetrics

PostizからXに投稿された後、x_post_idを自動紐づけします。

```bash
curl -X POST https://automation-x-kwzx.onrender.com/posts/reconcile-x-ids \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"lookback_days": 7, "mappings": []}'
```

metrics取得:

```bash
curl -X POST https://automation-x-kwzx.onrender.com/metrics/collect \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"post_ids": null}'
```

summary確認:

```bash
curl https://automation-x-kwzx.onrender.com/metrics/summary \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

## 運用記憶を見る

Codexが次のcycleで参照するcontext:

```bash
curl https://automation-x-kwzx.onrender.com/memory/context \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

仮説:

```bash
curl https://automation-x-kwzx.onrender.com/hypotheses \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

draft import履歴:

```bash
curl https://automation-x-kwzx.onrender.com/draft-import-runs \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

判断ログ:

```bash
curl https://automation-x-kwzx.onrender.com/decision-logs \
  -H "X-API-Key: $GROWTH_AGENT_API_KEY"
```

## n8n運用

n8n Cloudでは、workflow JSONにsecretを書かず、Header Auth credentialを使います。

Credential:

```text
Name: X-API-Key
Value: RenderのGROWTH_AGENT_API_KEY
```

n8n variable:

```text
GROWTH_AGENT_BASE_URL=https://automation-x-kwzx.onrender.com
```

使うworkflow:

- dry-run確認: `n8n/growth_agent_n8n_dry_run_smoke_test.json`
- live cycle: `n8n/growth_agent_n8n_live_cycle.json`
- reconcile/metrics追跡: `n8n/growth_agent_n8n_metrics_catchup.json`

## トラブル時

すぐ止める:

```text
AUTOMATION_KILL_SWITCH=true
```

live schedulingだけ止める:

```text
AUTO_POSTING_ENABLED=false
SCHEDULING_DRY_RUN=true
```

API keyエラー:

- ターミナルの `$GROWTH_AGENT_API_KEY` が未設定の可能性があります。
- Renderの値、n8n credential、Codex MCPのenvが同じか確認します。
- API keyをチャットに貼った場合はRenderでローテーションします。

scheduleされない:

- `approval_required_count > 0`: `/decision-logs` とdraftの `evaluation_notes` を確認します。
- `frequency_limited_count > 0`: `today_auto_scheduled_count` と `next_post_available_at` を確認します。
- `live_scheduled_count=0`: `AUTO_POSTING_ENABLED`, `SCHEDULING_DRY_RUN`, `AUTOMATION_KILL_SWITCH` を確認します。

Postizに出ない:

- `POSTIZ_BASE_URL`
- `POSTIZ_API_KEY`
- `POSTIZ_X_INTEGRATION_ID`
- `postiz_post_id_present`

X IDやmetricsが取れない:

- `X_BEARER_TOKEN`
- `X_USER_ID`
- 投稿時刻後にreconcileを実行したか
- 対象投稿がowned postとして取得可能か
