# Safety Policy

## Prohibited Automation

The MVP must not automate replies, mentions, likes, follows, retweets, DMs, or keyword-triggered outreach. It only schedules owned-account posts through Postiz and reads owned post data from X.

## URL Handling

Any draft or post containing `http://`, `https://`, or `www.` is tagged with `has_url=true`. URL-bearing drafts require human approval because links can change safety, compliance, and attribution risk.

## Duplicate Prevention

Before scheduling, the evaluator compares normalized draft text against existing drafts and scheduled or published posts. Text is lowercased, URLs and punctuation are removed, and near-duplicate similarity is measured. Drafts at or above `DUPLICATE_SIMILARITY_THRESHOLD` are blocked from scheduling.

## Risk and Approval Thresholds

Drafts start with a score of 95 and lose points for URLs, high-risk language, claim/urgency language, excessive length, very short text, all caps, or duplicate content.

Auto-scheduling is allowed only when all are true:

- `risk_level=low`
- `score >= AUTO_SCHEDULE_SCORE_THRESHOLD`, default `80`
- `requires_approval=false`
- no duplicate or near-duplicate match

Human approval is required when any are true:

- `risk_level=medium` or `risk_level=high`
- score is below the auto-scheduling threshold
- the draft contains a URL
- the draft contains absolute, urgent, or hard-to-verify claims
- the draft is high-risk by policy

Duplicate or near-duplicate drafts are blocked from scheduling even if someone attempts to approve them.

## External API Safety

Postiz and X clients use explicit timeouts and bounded retries. Tests mock every external client and must not call live external services.
