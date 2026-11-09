from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_workflow_has_durable_state_and_berlin_schedule():
    # BaseLoader preserves the YAML key 'on' rather than interpreting it as a boolean.
    data = yaml.load(
        (ROOT / ".github/workflows/daily_monitor.yml").read_text(), Loader=yaml.BaseLoader
    )
    schedule = data["on"]["schedule"][0]
    assert schedule == {"cron": "7 16 * * *", "timezone": "Europe/Berlin"}
    assert ZoneInfo(schedule["timezone"]).key == "Europe/Berlin"
    assert data["concurrency"]["cancel-in-progress"] == "false"
    assert data["permissions"]["contents"] == "write"
    job = data["jobs"]["monitor"]
    assert job["env"]["STATE_BACKEND"] == "github"
    assert job["env"]["GEMINI_API_KEY"] == "${{ secrets.GEMINI_API_KEY }}"
    assert "GEMINI_MODEL" in job["env"]
    assert "repository.private" in job["if"]
    assert "main.py" in job["steps"][-1]["run"]
