from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx


class GrowthAgentMCPError(RuntimeError):
    pass


@dataclass(frozen=True)
class GrowthAgentMCPConfig:
    base_url: str
    api_key: str
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> GrowthAgentMCPConfig:
        values = os.environ if env is None else env
        timeout_raw = values.get("GROWTH_AGENT_MCP_TIMEOUT_SECONDS", "10")
        try:
            timeout_seconds = float(timeout_raw)
        except ValueError:
            timeout_seconds = 10.0
        return cls(
            base_url=values.get("GROWTH_AGENT_BASE_URL", "http://localhost:8000").rstrip("/"),
            api_key=values.get("GROWTH_AGENT_API_KEY", ""),
            timeout_seconds=max(1.0, min(timeout_seconds, 60.0)),
        )


class GrowthAgentMCPClient:
    def __init__(
        self,
        config: GrowthAgentMCPConfig | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config or GrowthAgentMCPConfig.from_env()
        self.transport = transport

    def get_automation_status(self) -> dict[str, Any]:
        return self._request("GET", "/automation/status")

    def get_metrics_summary(self) -> dict[str, Any]:
        return self._request("GET", "/metrics/summary")

    def get_playbook(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/feedback/playbook")
        return data if isinstance(data, list) else []

    def get_memory_context(self, *, limit: int = 10) -> dict[str, Any]:
        return self._request("GET", f"/memory/context?limit={limit}")

    def list_hypotheses(self, *, limit: int = 50) -> list[dict[str, Any]]:
        data = self._request("GET", f"/hypotheses?limit={limit}")
        return data if isinstance(data, list) else []

    def list_draft_import_runs(self, *, limit: int = 50) -> list[dict[str, Any]]:
        data = self._request("GET", f"/draft-import-runs?limit={limit}")
        return data if isinstance(data, list) else []

    def list_decision_logs(
        self,
        *,
        limit: int = 50,
        draft_id: int | None = None,
        automation_run_id: int | None = None,
    ) -> list[dict[str, Any]]:
        params = [f"limit={limit}"]
        if draft_id is not None:
            params.append(f"draft_id={draft_id}")
        if automation_run_id is not None:
            params.append(f"automation_run_id={automation_run_id}")
        data = self._request("GET", f"/decision-logs?{'&'.join(params)}")
        return data if isinstance(data, list) else []

    def create_idea(
        self,
        *,
        title: str,
        description: str,
        source: str = "chatgpt_mcp",
        audience: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "title": title,
            "description": description,
            "source": source,
            "audience": audience,
            "metadata": metadata or {},
        }
        return self._request("POST", "/ideas/ingest", json=payload)

    def import_generated_drafts(
        self,
        *,
        idea_id: int,
        drafts: list[dict[str, Any]],
        source: str = "chatgpt_mcp",
        prompt_version: str = "mcp-v1",
        context_snapshot: dict[str, Any] | None = None,
        hypotheses: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "idea_id": idea_id,
            "drafts": drafts,
            "source": source,
            "prompt_version": prompt_version,
            "context_snapshot": context_snapshot or {},
            "hypotheses": hypotheses or [],
            "metadata": metadata or {},
        }
        return self._request("POST", "/drafts/import", json=payload)

    def evaluate_draft(self, *, draft_id: int) -> dict[str, Any]:
        return self._request("POST", f"/drafts/{draft_id}/evaluate")

    def run_dry_cycle(self) -> dict[str, Any]:
        status = self.get_automation_status()
        if status.get("kill_switch_active") is True:
            return {
                "ok": False,
                "blocked": True,
                "reason": "AUTOMATION_KILL_SWITCH=true; dry-run cycle was not started.",
                "status": status,
            }
        if status.get("scheduling_dry_run") is not True:
            return {
                "ok": False,
                "blocked": True,
                "reason": (
                    "SCHEDULING_DRY_RUN=false; this MCP tool only starts dry-run cycles."
                ),
                "status": status,
            }
        return {"ok": True, "cycle": self._request("POST", "/automation/run-cycle")}

    def explain_last_run_context(self) -> dict[str, Any]:
        return {
            "automation_status": self.get_automation_status(),
            "memory_context": self.get_memory_context(),
        }

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any] | list[Any]:
        if not self.config.api_key:
            raise GrowthAgentMCPError("GROWTH_AGENT_API_KEY is not configured for MCP server.")
        url = f"{self.config.base_url}{path}"
        headers = {"X-API-Key": self.config.api_key}
        try:
            with httpx.Client(
                timeout=self.config.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = client.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise GrowthAgentMCPError(_redact(str(exc), self.config.api_key)) from exc

        if response.status_code >= 400:
            message = _safe_response_excerpt(response, self.config.api_key)
            raise GrowthAgentMCPError(
                f"Growth Agent {method} {path} failed with status "
                f"{response.status_code}: {message}"
            )

        data = response.json()
        if isinstance(data, dict | list):
            return data
        return {"data": data}


def _safe_response_excerpt(response: httpx.Response, api_key: str) -> str:
    text = response.text.strip() or "<empty response body>"
    return _redact(text[:500], api_key)


def _redact(text: str, *secrets: str) -> str:
    safe = text
    for secret in secrets:
        if secret:
            safe = safe.replace(secret, "****")
    return safe
