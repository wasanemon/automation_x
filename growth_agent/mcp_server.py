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
) -> dict[str, Any]:
    """Import ChatGPT/Codex-generated draft candidates; Growth Agent evaluates them later."""
    return _safe_call(
        lambda client: client.import_generated_drafts(
            idea_id=idea_id,
            drafts=drafts,
            source=source,
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
