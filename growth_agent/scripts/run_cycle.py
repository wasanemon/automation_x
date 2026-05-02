from __future__ import annotations

import json
import os
import sys
from typing import Any

import httpx


def main() -> int:
    base_url = os.environ.get("GROWTH_AGENT_BASE_URL", "http://localhost:8000").rstrip("/")
    api_key = os.environ.get("GROWTH_AGENT_API_KEY", "")
    timeout = float(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))

    if not api_key:
        print("GROWTH_AGENT_API_KEY is not configured.", file=sys.stderr)
        return 2

    try:
        response = httpx.post(
            f"{base_url}/automation/run-cycle",
            headers={"X-API-Key": api_key},
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        print(f"automation cycle request failed: {exc}", file=sys.stderr)
        return 1

    body = _response_body(response)
    print(json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if response.status_code < 400 else 1


def _response_body(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError:
        return {"status_code": response.status_code, "body": response.text[:500]}
    if isinstance(data, dict):
        return data
    return {"status_code": response.status_code, "body": data}


if __name__ == "__main__":
    raise SystemExit(main())
