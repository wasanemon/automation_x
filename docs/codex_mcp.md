# Codex / ChatGPT MCP方式

このドキュメントは、Growth Agentで高度な仮説分析とdraft生成を行う時に、OpenAI APIではなくCodex / ChatGPT側のモデル実行を使うための運用メモです。

## 方針

Growth Agent serverはLLMを直接呼びません。Codex / ChatGPTが会話セッション内でmemory context、metrics summary、playbook、automation statusを読み、仮説とdraft候補を作ります。その候補をMCP tool経由でGrowth Agentへimportし、Growth Agentの既存安全機構でevaluate、duplicate check、approval判定、frequency limit、dry-run/live gate、kill switchを通します。

Codexの1つのウインドウ履歴を公式な記憶にはしません。公式な運用履歴はGrowth Agent DBの `hypotheses`、`draft_import_runs`、`decision_logs`、`automation_runs`、`metric_snapshots` に保存し、Codexは次回その履歴をMCPで読んで考えます。

```text
Codex / ChatGPT
  -> MCP tool: get_memory_context / get_automation_status
  -> Codex / ChatGPTが仮説分析とdraft候補を生成
  -> MCP tool: create_idea / import_generated_drafts / evaluate_draft / run_dry_cycle
  -> Growth Agent API
  -> PostgreSQL: hypotheses / draft_import_runs / decision_logs
  -> Postiz経由の予約投稿だけ
  -> X API read-only reconcile / public metrics
```

## OpenAI API方式との違い

- `OPENAI_API_KEY` は使いません。
- Growth Agent server内でResponses APIやChat Completions APIは呼びません。
- LLM生成はCodex / ChatGPTの会話セッション側で行います。
- Growth Agentはimportされたdraftを保存し、安全判定と運用制御を担当します。

## 起動

ローカルのGrowth Agentを使う場合:

```bash
export GROWTH_AGENT_BASE_URL=http://localhost:8000
export GROWTH_AGENT_API_KEY=<Growth Agent API key>
python -m growth_agent.mcp_server
```

Render上のGrowth Agentを使う場合:

```bash
export GROWTH_AGENT_BASE_URL=https://automation-x-kwzx.onrender.com
export GROWTH_AGENT_API_KEY=<Renderに設定したGrowth Agent API key>
python -m growth_agent.mcp_server
```

`GROWTH_AGENT_API_KEY` はMCP serverの環境変数またはcredentialとして設定し、チャット、README、workflow JSON、ログに書かないでください。

## 提供tool

- `get_automation_status`: auto posting、dry-run、kill switch、警告、last runを確認します。
- `get_metrics_summary`: 保存済みpublic metrics summaryを取得します。
- `get_playbook`: deterministic playbook ruleを取得します。
- `get_memory_context`: 次cycleで使うcompactな運用記憶を取得します。
- `list_hypotheses`: 保存済み仮説を取得します。
- `list_draft_import_runs`: draft import履歴を取得します。
- `list_decision_logs`: evaluate、schedule、reconcile、metricsなどの判断履歴を取得します。
- `create_idea`: 生成対象のideaを作成します。
- `import_generated_drafts`: ChatGPT/Codexが生成したdraft候補、仮説、context snapshotをGrowth Agentに保存します。
- `evaluate_draft`: 1 draftをdeterministic evaluatorに通します。
- `run_dry_cycle`: `SCHEDULING_DRY_RUN=true` かつ kill switch off の時だけautomation cycleを実行します。
- `explain_last_run_context`: statusとmemory contextをまとめて取得します。

v1では、MCP toolからlive schedulingを直接開始するtoolは用意していません。live schedulingはn8nまたはGrowth Agent API側で、既存gateを満たした時だけ実行します。

## draft import schema

`import_generated_drafts` に渡すdraft候補は、以下の形です。

```json
{
  "idea_id": 1,
  "source": "chatgpt_mcp",
  "prompt_version": "mcp-v1",
  "context_snapshot": {
    "metrics_summary": {
      "posts": 0
    },
    "playbook": ["具体的ユースケースを優先"]
  },
  "hypotheses": [
    {
      "statement": "具体的な運用チェックリスト型の投稿は保存数を伸ばしやすい。",
      "target_metric": "bookmarks",
      "confidence": 0.72,
      "evidence": ["過去metricsが少ないため仮説扱い"]
    }
  ],
  "drafts": [
    {
      "content": "投稿本文",
      "hypothesis_index": 0,
      "target_metric": "bookmarks",
      "confidence": 0.82,
      "risk_notes": ["外部URLなし", "強い断定なし"],
      "requires_human_review_by_model": false
    }
  ],
  "metadata": {
    "operator": "codex"
  }
}
```

`requires_human_review_by_model=true` のdraft、または `confidence < 0.7` のdraftは、評価後もhuman approvalが必要になります。LLMが安全と判断しても、最終判断はGrowth Agentのevaluatorが行います。

import後は以下が保存されます。

- `hypotheses`: 仮説本文、target metric、confidence、evidence。
- `draft_import_runs`: Codexが参照したcontext、生成した仮説、出力draft、importされたdraft ID。
- `decision_logs`: `draft_import=imported`。
- `drafts.hypothesis_id` / `drafts.draft_import_run_id`: draftの由来リンク。

automation cycleを回すと、`decision_logs` に `evaluate`、`approval`、`schedule`、`reconcile`、`metrics` の判断が追記されます。

## 安全制約

Codex / ChatGPTにdraft生成を依頼する時は、必ず以下を固定条件にしてください。

- 自動返信を作らない。
- 自動メンションを作らない。
- 自動いいね、フォロー、リポスト、DMを作らない。
- キーワード反応型営業リプライを作らない。
- X APIから投稿しない。
- 投稿・予約投稿はPostiz経由だけ。
- 外部URL、短縮URL、価格、法務、強い主張はhuman approvalへ回す。
- secret値をdraft、metadata、risk note、ログに含めない。

## dry-run確認

1. `get_automation_status` を実行します。
2. `scheduling_dry_run=true`、`kill_switch_active=false` を確認します。
3. `get_memory_context` を取得します。
4. Codex / ChatGPTで仮説とdraft候補を作ります。
5. `create_idea` でideaを作ります。
6. `import_generated_drafts` でcontext、hypotheses、draft候補を保存します。
7. `run_dry_cycle` を実行します。
8. `dry_run_scheduled_count` が増え、`live_scheduled_count=0` であることを確認します。
9. `list_decision_logs` または `get_memory_context` で判断履歴を確認します。

## live testへ進む条件

live schedulingを有効にするのは、テスト用Xアカウントで次が揃った時だけです。

```text
AUTO_POSTING_ENABLED=true
SCHEDULING_DRY_RUN=false
AUTOMATION_KILL_SWITCH=false
POSTIZ_BASE_URL=<configured>
POSTIZ_API_KEY=<configured>
POSTIZ_X_INTEGRATION_ID=<configured>
```

加えて、draft側でも以下が必要です。

- evaluator scoreが `AUTO_SCHEDULE_SCORE_THRESHOLD` 以上
- `risk_level=low`
- `requires_approval=false`
- duplicate / near-duplicateではない
- schedule済みではない
- 投稿頻度制限内

## やらないこと

- Growth Agent server内のOpenAI API呼び出し
- private metrics取得
- URL clickやprofile click取得
- 自動返信、自動メンション、自動いいね、自動フォロー、自動リポスト、DM
- X APIからの投稿
