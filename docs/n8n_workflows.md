# n8n Workflows

## Idea Intake

Trigger from a form, Slack command, CRM note, or manual entry. Normalize the input and call `POST /ideas/ingest` with `title`, `description`, `source`, `audience`, and optional metadata.

## Draft and Evaluation

After idea creation, call `POST /drafts/generate`. For each returned draft, call `POST /drafts/{id}/evaluate`.

Branch on the evaluation response:

- `can_auto_schedule=true`: call `POST /drafts/{id}/schedule`.
- `requires_approval=true`: create a human approval task.
- duplicate/high-risk/rejected: do not schedule automatically.

## Human Approval

Use n8n's approval step or a Slack/Linear task. On approval, call `POST /drafts/{id}/approve`, then call `POST /drafts/{id}/schedule`. On rejection, call `POST /drafts/{id}/reject`.

## X ID Reconciliation

Run after scheduled posts are expected to be live. Prefer manual mappings when Postiz or an operator provides the X post ID. Otherwise call `POST /posts/reconcile-x-ids` with a `lookback_days` window to match owned X posts by normalized text.

## Metrics Collection

Run on a timer, for example every 6 or 24 hours:

1. Call `POST /posts/reconcile-x-ids`.
2. Call `POST /metrics/collect`.
3. Call `GET /metrics/summary` for dashboards or notifications.

## Feedback and Weekly Report

Run `POST /feedback/run` weekly or after enough posts have metrics. Fetch `GET /feedback/playbook` before the next draft generation batch. Send `GET /reports/weekly` to the team.
