# Safety Policy

## Prohibited Automation

The MVP must not automate:

- replies
- mentions
- likes
- follows
- retweets or reposts
- DMs
- keyword-triggered sales replies or outreach

The service only creates owned-account draft posts, schedules approved owned-account posts through Postiz, and reads owned post data from X for reconciliation and metrics.

## Test Account First

Validation is expected to happen with a test X account connected through Postiz. Before switching to any production X account, confirm:

- `SCHEDULING_DRY_RUN=false` is intentional.
- `POSTIZ_X_INTEGRATION_ID` points to the intended account.
- `TEST_X_ACCOUNT_HANDLE` has been replaced or explicitly acknowledged.
- approval policy is understood by the operator.
- duplicate and near-duplicate checks are passing.
- no workflow sends replies, mentions, likes, follows, DMs, or keyword-triggered outreach.

## Dry-Run vs Live Scheduling

`SCHEDULING_DRY_RUN=true` is the default. In dry-run, scheduling creates a local post record with `dry_run=true` and does not call Postiz.

`SCHEDULING_DRY_RUN=false` enables live scheduling through Postiz. Live scheduling uses the configured Postiz Public API base URL, API key, and X integration ID. Postiz calls use explicit timeouts and bounded retries.

## URL Handling

Any draft or post containing `http://`, `https://`, or `www.` is tagged with `has_url=true`.

`OWNED_DOMAINS` is a comma-separated allowlist such as `example.com,docs.example.com`.

- If `OWNED_DOMAINS` is empty, URL-bearing drafts require human approval.
- Owned-domain URLs can be auto-scheduled only when the draft is low risk, high scoring, and not a duplicate.
- External URLs require human approval.
- Shortened URLs require human approval.
- Competitor or unknown URLs are treated as external URL risk unless explicitly owned.
- Pricing, legal, compliance, refund, terms, or strong claim language requires human approval.

## Duplicate Prevention

Before scheduling, the evaluator compares normalized draft text against existing drafts and scheduled or published posts. Text is lowercased, URLs and punctuation are removed, and near-duplicate similarity is measured.

Drafts at or above `DUPLICATE_SIMILARITY_THRESHOLD` are blocked from scheduling. Duplicate or near-duplicate drafts are blocked even if someone attempts to approve them.

The same draft cannot be scheduled twice. A failed live Postiz scheduling attempt leaves a local schedule record, so retries do not easily create duplicate remote posts.

## Risk and Approval Thresholds

Drafts start with a score of 95 and lose points for URLs, high-risk language, claim/urgency language, pricing/legal language, excessive length, very short text, all caps, or duplicate content.

Auto-scheduling is allowed only when all are true:

- `risk_level=low`
- `score >= AUTO_SCHEDULE_SCORE_THRESHOLD`, default `80`
- `requires_approval=false`
- no duplicate or near-duplicate match

Human approval is required when any are true:

- `risk_level=medium` or `risk_level=high`
- score is below the auto-scheduling threshold
- `OWNED_DOMAINS` is empty and the draft contains a URL
- the draft contains an external URL, short URL, pricing/legal language, or hard-to-verify claim
- the draft is high-risk by policy

## X API Safety

X API usage is read-only and limited to owned post lookup and metrics collection. X credentials are not required for startup, dry-run, or Postiz scheduling.

This MVP collects only public metrics available with Bearer Token access: impressions, likes, replies, reposts, quotes, and bookmarks. It does not collect URL clicks, profile clicks, organic metrics, promoted metrics, private metrics, or non-public metrics. Those require a future user-context authentication design.

X post ID reconciliation uses normalized text similarity plus scheduled/created time proximity. It must not trigger replies, mentions, likes, follows, DMs, reposts, or keyword-based outreach.

## External API Safety

Postiz and X clients use explicit timeouts and bounded retries. Tests mock every external client and must not call live external services.
