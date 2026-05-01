# AGENTS.md

## Project Rules

- Do not put secrets in code, tests, fixtures, docs examples, or logs.
- Use environment variables for all credentials and service URLs.
- Never implement automated replies, mentions, likes, follows, retweets, DMs, or keyword-triggered outreach.
- X API usage in this MVP is read-only and limited to owned post lookup and metrics collection.
- Mock Postiz, X API, and any other external HTTP calls in tests.
- Keep retries bounded and timeouts explicit.
- Before scheduling, enforce duplicate or near-duplicate prevention.
- URL-bearing drafts and posts must set `has_url=true`.
- High-risk drafts must require human approval before scheduling.
- Run `pytest` and `ruff check .` before handing off changes when the environment allows it.
