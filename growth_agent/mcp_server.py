from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from growth_agent.mcp_tools import GrowthAgentMCPClient, GrowthAgentMCPError

mcp = FastMCP("growth-agent")


@mcp.tool()
def get_automation_status() -> dict[str, Any]:
    """Return Growth Agent automation guardrail status without exposing secrets."""
    return _safe_call(lambda client: client.get_automation_status())


@mcp.tool()
def get_metrics_summary() -> dict[str, Any]:
    """Return the latest public metrics summary stored by Growth Agent."""
    return _safe_call(lambda client: client.get_metrics_summary())


@mcp.tool()
def get_playbook() -> dict[str, Any]:
    """Return active deterministic playbook rules used as generation context."""
    return _safe_call(lambda client: {"rules": client.get_playbook()})


@mcp.tool()
def get_memory_context(limit: int = 10) -> dict[str, Any]:
    """Return the compact operational memory context Codex should use for generation."""
    return _safe_call(lambda client: client.get_memory_context(limit=limit))


@mcp.tool()
def list_hypotheses(limit: int = 50) -> dict[str, Any]:
    """Return recent stored hypotheses."""
    return _safe_call(lambda client: {"hypotheses": client.list_hypotheses(limit=limit)})


@mcp.tool()
def list_draft_import_runs(limit: int = 50) -> dict[str, Any]:
    """Return recent draft import runs created by Codex/MCP or other import sources."""
    return _safe_call(
        lambda client: {"draft_import_runs": client.list_draft_import_runs(limit=limit)}
    )


@mcp.tool()
def list_decision_logs(
    limit: int = 50,
    draft_id: int | None = None,
    automation_run_id: int | None = None,
) -> dict[str, Any]:
    """Return recent deterministic decision logs for audits and next-cycle analysis."""
    return _safe_call(
        lambda client: {
            "decision_logs": client.list_decision_logs(
                limit=limit,
                draft_id=draft_id,
                automation_run_id=automation_run_id,
            )
        }
    )


@mcp.tool()
def create_idea(
    title: str,
    description: str,
    source: str = "chatgpt_mcp",
    audience: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an idea that ChatGPT/Codex can use as the source for generated drafts."""
    return _safe_call(
        lambda client: client.create_idea(
            title=title,
            description=description,
            source=source,
            audience=audience,
            metadata=metadata,
        )
    )


@mcp.tool()
def import_generated_drafts(
    idea_id: int,
    drafts: list[dict[str, Any]],
    source: str = "chatgpt_mcp",
    prompt_version: str = "mcp-v1",
    context_snapshot: dict[str, Any] | None = None,
    hypotheses: list[dict[str, Any]] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Import generated drafts and store the context/hypotheses that produced them."""
    return _safe_call(
        lambda client: client.import_generated_drafts(
            idea_id=idea_id,
            drafts=drafts,
            source=source,
            prompt_version=prompt_version,
            context_snapshot=context_snapshot,
            hypotheses=hypotheses,
            metadata=metadata,
        )
    )


@mcp.tool()
def evaluate_draft(draft_id: int) -> dict[str, Any]:
    """Run Growth Agent's deterministic safety evaluator for one draft."""
    return _safe_call(lambda client: client.evaluate_draft(draft_id=draft_id))


@mcp.tool()
def run_dry_cycle() -> dict[str, Any]:
    """Run one automation cycle only when Growth Agent is configured for dry-run scheduling."""
    return _safe_call(lambda client: client.run_dry_cycle())


@mcp.tool()
def explain_last_run_context() -> dict[str, Any]:
    """Return status, last automation run, metrics, and playbook context for explanation."""
    return _safe_call(lambda client: client.explain_last_run_context())


def _safe_call(call):
    client = GrowthAgentMCPClient()
    try:
        return call(client)
    except GrowthAgentMCPError as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    mcp.run()
