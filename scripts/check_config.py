from __future__ import annotations

import os
import secrets
import shutil
from pathlib import Path

ENV_PATH = Path(".env")
EXAMPLE_PATH = Path(".env.example")

DEFAULTS = {
    "TESTING": "false",
    "SCHEDULING_DRY_RUN": "true",
    "REQUEST_TIMEOUT_SECONDS": "10",
    "MAX_EXTERNAL_RETRIES": "2",
    "OWNED_DOMAINS": "",
    "SAFE_PUBLIC_READS": "false",
    "AUTO_APPLY_TENTATIVE_RULES": "false",
    "X_RECONCILE_LOOKBACK_HOURS": "48",
    "X_RECONCILE_TEXT_SIMILARITY_THRESHOLD": "0.82",
}

DRY_RUN_REQUIRED = ("DATABASE_URL", "GROWTH_AGENT_API_KEY", "SCHEDULING_DRY_RUN")
POSTIZ_REQUIRED = (
    "POSTIZ_BASE_URL",
    "POSTIZ_API_KEY",
    "POSTIZ_X_INTEGRATION_ID",
    "TEST_X_ACCOUNT_HANDLE",
)
X_METRICS_REQUIRED = ("X_BEARER_TOKEN", "X_USER_ID")

DISPLAY_KEYS = (
    "DATABASE_URL",
    "TESTING",
    "GROWTH_AGENT_API_KEY",
    "SCHEDULING_DRY_RUN",
    "POSTIZ_BASE_URL",
    "POSTIZ_API_KEY",
    "POSTIZ_X_INTEGRATION_ID",
    "TEST_X_ACCOUNT_HANDLE",
    "OWNED_DOMAINS",
    "SAFE_PUBLIC_READS",
    "AUTO_APPLY_TENTATIVE_RULES",
    "X_API_BASE_URL",
    "X_BEARER_TOKEN",
    "X_USER_ID",
    "X_RECONCILE_LOOKBACK_HOURS",
    "X_RECONCILE_TEXT_SIMILARITY_THRESHOLD",
    "REQUEST_TIMEOUT_SECONDS",
    "MAX_EXTERNAL_RETRIES",
)

SECRET_KEYS = {"GROWTH_AGENT_API_KEY", "POSTIZ_API_KEY", "X_BEARER_TOKEN"}
VALUE_OK_TO_PRINT = {
    "TESTING",
    "SCHEDULING_DRY_RUN",
    "SAFE_PUBLIC_READS",
    "AUTO_APPLY_TENTATIVE_RULES",
    "X_USER_ID",
    "X_RECONCILE_LOOKBACK_HOURS",
    "X_RECONCILE_TEXT_SIMILARITY_THRESHOLD",
    "REQUEST_TIMEOUT_SECONDS",
    "MAX_EXTERNAL_RETRIES",
}


def main() -> int:
    env_created, completed = ensure_env_file()
    env_values = read_env_values(ENV_PATH)
    effective = {
        key: os.environ.get(key, env_values.get(key, ""))
        for key in DISPLAY_KEYS
        + DRY_RUN_REQUIRED
        + POSTIZ_REQUIRED
        + X_METRICS_REQUIRED
    }

    dry_run_missing = missing(effective, DRY_RUN_REQUIRED)
    postiz_missing = missing(effective, POSTIZ_REQUIRED)
    x_missing = missing(effective, X_METRICS_REQUIRED)
    dry_run_enabled = parse_bool(effective.get("SCHEDULING_DRY_RUN"), default=True)

    print("Growth Agent configuration check")
    print(f".env: {'created from .env.example' if env_created else 'found'}")

    if completed:
        print("\nAuto-completed:")
        for key, value in completed.items():
            print(f"- {key}={display_value(key, value)}")
    else:
        print("\nAuto-completed: none")

    print("\nReadiness:")
    print(f"- dry-run flow: {ready_label(not dry_run_missing and dry_run_enabled)}")
    print(f"- Postiz test scheduling config: {ready_label(not postiz_missing)}")
    print(f"- X metrics ready: {ready_label(not x_missing)}")

    print("\nSettings:")
    for key in DISPLAY_KEYS:
        print(f"- {key}: {display_value(key, effective.get(key, ''))}")

    missing_items = {
        "dry-run": dry_run_missing,
        "Postiz test scheduling": postiz_missing,
        "X metrics": x_missing,
    }
    print("\nMissing:")
    any_missing = False
    for label, keys in missing_items.items():
        if keys:
            any_missing = True
            print(f"- {label}: {', '.join(keys)}")
    if not any_missing:
        print("- none")

    warnings = build_warnings(
        dry_run_enabled=dry_run_enabled,
        postiz_missing=postiz_missing,
        x_missing=x_missing,
        owned_domains=effective.get("OWNED_DOMAINS", ""),
    )
    print("\nNotes:")
    for warning in warnings:
        print(f"- {warning}")

    return 0


def ensure_env_file() -> tuple[bool, dict[str, str]]:
    env_created = False
    if not ENV_PATH.exists():
        if EXAMPLE_PATH.exists():
            shutil.copyfile(EXAMPLE_PATH, ENV_PATH)
        else:
            ENV_PATH.write_text("", encoding="utf-8")
        env_created = True

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    values, line_for_key = parse_env_lines(lines)
    completed: dict[str, str] = {}

    if not is_set(values.get("GROWTH_AGENT_API_KEY")):
        completed["GROWTH_AGENT_API_KEY"] = f"ga_{secrets.token_urlsafe(32)}"
    for key, value in DEFAULTS.items():
        if key not in values or (key != "OWNED_DOMAINS" and not is_set(values.get(key))):
            completed[key] = value

    if completed:
        if lines and lines[-1].strip():
            lines.append("\n")
        for key, value in completed.items():
            line = f"{key}={value}\n"
            if key in line_for_key:
                lines[line_for_key[key]] = line
            else:
                lines.append(line)
        ENV_PATH.write_text("".join(lines), encoding="utf-8")

    return env_created, completed


def read_env_values(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values, _ = parse_env_lines(path.read_text(encoding="utf-8").splitlines(keepends=True))
    return values


def parse_env_lines(lines: list[str]) -> tuple[dict[str, str], dict[str, int]]:
    values: dict[str, str] = {}
    line_for_key: dict[str, int] = {}
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or any(char.isspace() for char in key):
            continue
        values[key] = unquote(raw_value.strip())
        line_for_key[key] = index
    return values, line_for_key


def unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def missing(values: dict[str, str], keys: tuple[str, ...]) -> list[str]:
    return [key for key in keys if not is_set(values.get(key))]


def is_set(value: str | None) -> bool:
    return value is not None and value.strip() != ""


def parse_bool(value: str | None, default: bool) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def ready_label(is_ready: bool) -> str:
    return "ready" if is_ready else "not ready"


def display_value(key: str, value: str | None) -> str:
    if not is_set(value):
        return "unset"
    assert value is not None
    if key in SECRET_KEYS:
        return "set: ****"
    if key in VALUE_OK_TO_PRINT:
        return value
    return "set"


def build_warnings(
    *,
    dry_run_enabled: bool,
    postiz_missing: list[str],
    x_missing: list[str],
    owned_domains: str,
) -> list[str]:
    warnings: list[str] = []
    if dry_run_enabled:
        warnings.append("SCHEDULING_DRY_RUN=true, so scheduling creates only local post records.")
    else:
        warnings.append("SCHEDULING_DRY_RUN=false, so approved schedules call Postiz.")
    if postiz_missing:
        warnings.append("Postiz live test scheduling needs the missing Postiz variables above.")
    if x_missing:
        warnings.append(
            "X metrics credentials are incomplete; metrics collection will skip safely."
        )
    if not owned_domains.strip():
        warnings.append("OWNED_DOMAINS is empty; URL-bearing drafts require human approval.")
    return warnings


if __name__ == "__main__":
    raise SystemExit(main())
