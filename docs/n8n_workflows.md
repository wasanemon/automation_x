# n8n Workflows

All non-health API calls should include `X-API-Key: $GROWTH_AGENT_API_KEY`. n8n should store that value as a credential or environment variable, not inside workflow JSON.

## Idea Intake

Trigger from a form, Slack command, CRM note, or manual entry. Normalize the input and call `POST /ideas/ingest` with `title`, `description`, `source`, `audience`, and optional metadata.

Do not trigger keyword-based sales replies, mentions, DMs, follows, likes, or reposts.

## Draft and Evaluation

After idea creation, call `POST /drafts/generate`. For each returned draft, call `POST /drafts/{id}/evaluate`.

Branch on the evaluation response:

- `can_auto_schedule=true`: call `POST /drafts/{id}/schedule`.
- `requires_approval=true`: create a human approval task.
- duplicate, near-duplicate, high-risk, or rejected: do not schedule automatically.

## Human Approval

Use n8n's approval step or a Slack/Linear task. On approval, call `POST /drafts/{id}/approve`, then call `POST /drafts/{id}/schedule`. On rejection, call `POST /drafts/{id}/reject`.

URL-bearing drafts need extra care:

- If `OWNED_DOMAINS` is empty, require approval.
- Owned-domain URLs can be auto-scheduled only when the evaluator returns low risk.
- External URLs, short URLs, pricing/legal language, and strong claims require approval.

## Dry-Run Smoke Test

Keep `SCHEDULING_DRY_RUN=true` for the first workflow smoke test. Scheduling will create local post records with `dry_run=true` and will not call Postiz.

Recommended smoke-test path:

1. `POST /ideas/ingest`
2. `POST /drafts/generate`
3. `POST /drafts/{id}/evaluate`
4. `POST /drafts/{id}/approve` when needed
5. `POST /drafts/{id}/schedule`
6. `GET /posts`

## Postiz Test Scheduling

After dry-run succeeds, verify the test X account connection through Postiz:

- `POSTIZ_BASE_URL`
- `POSTIZ_API_KEY`
- `POSTIZ_X_INTEGRATION_ID`
- `TEST_X_ACCOUNT_HANDLE`

Then set `SCHEDULING_DRY_RUN=false` and rerun the same workflow against the test X account. The schedule response should have `dry_run=false` and a `postiz_post_id`.

## X ID Reconciliation

Run after scheduled posts are expected to be live. Prefer manual mappings when Postiz or an operator provides the X post ID.

Otherwise call `POST /posts/reconcile-x-ids` to match owned X posts by normalized text and scheduled/created time proximity. This requires `X_BEARER_TOKEN` and `X_USER_ID`. If those are missing, reconciliation skips safely and Postiz scheduling still works.

## Metrics Collection

Run on a timer, for example every 6 or 24 hours:

1. Call `POST /posts/reconcile-x-ids`.
2. Call `POST /metrics/collect`.
3. Call `GET /metrics/summary` for dashboards or notifications.

Metrics collection skips safely when X credentials are missing. The MVP collects public metrics only: impressions, likes, replies, reposts, quotes, and bookmarks. URL clicks, profile clicks, organic metrics, promoted metrics, and non-public/private metrics are future user-context auth work.

## Automation Cycle MVP

For the first automatic operating loop, prefer the single cycle endpoint over chaining many manual HTTP nodes:

1. Cron node every 30-60 minutes.
2. HTTP Request: `GET /automation/status`.
3. IF node: stop when `kill_switch_active=true`.
4. IF node: optionally stop or notify when `system_warnings` contains a blocking warning.
5. HTTP Request: `POST /automation/run-cycle`.
6. IF node: when `approval_required_count > 0`, notify the human approval channel.
7. HTTP Request: `GET /metrics/summary` for the latest dashboard snapshot.
8. Weekly Cron node: `GET /reports/weekly` and send the report to the team.

Recommended HTTP settings:

- Send `X-API-Key` from an n8n credential or environment variable.
- Do not store API keys, Postiz keys, or X bearer tokens in workflow JSON.
- Keep `SCHEDULING_DRY_RUN=true` and `AUTO_POSTING_ENABLED=false` for the first dry-run.
- Enable live scheduling only for the test X account with `AUTO_POSTING_ENABLED=true`, `SCHEDULING_DRY_RUN=false`, and `AUTOMATION_KILL_SWITCH=false`.

The status response gives n8n the guardrails it needs before calling the cycle:

- `auto_posting_enabled`
- `scheduling_dry_run`
- `kill_switch_active`
- `today_auto_scheduled_count`
- `next_post_available_at`
- `approval_waiting_draft_count`
- `unreconciled_post_count`
- `metrics_missing_post_count`
- `last_automation_run`
- `system_warnings`

The cycle response is designed for branching:

- `auto_scheduled_count > 0`: post or dry-run schedule records were created.
- `approval_required_count > 0`: notify a human reviewer.
- `errors` is not empty: notify ops and keep automation conservative.
- `next_recommended_action`: include in notification text.

## Feedback and Weekly Report

Run `POST /feedback/run` weekly or after enough posts have metrics. Fetch `GET /feedback/playbook` before the next draft generation batch. Send `GET /reports/weekly` to the team.
