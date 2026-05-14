from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_runtime_environment_reads_are_centralized_in_config() -> None:
    offenders: list[str] = []
    for path in (PROJECT_ROOT / "app").rglob("*.py"):
        if path.name == "config.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "os.getenv" in text or "os.environ" in text:
            offenders.append(str(path.relative_to(PROJECT_ROOT)))

    assert offenders == []


def test_background_schedulers_expose_authenticated_status_endpoints() -> None:
    endpoints = {
        '"/notifications/scheduler"',
        '"/rotation/auto/scheduler"',
        '"/api/credit-control/scheduler"',
    }
    main_source = (PROJECT_ROOT / "app" / "main.py").read_text(encoding="utf-8")

    missing = sorted(endpoint for endpoint in endpoints if endpoint not in main_source)

    assert missing == []
