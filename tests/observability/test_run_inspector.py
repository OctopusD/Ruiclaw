from __future__ import annotations

import json

from ruiclaw.observability.run_inspector import MAX_DETAIL_EVENTS, RunInspector


def test_run_inspector_lists_and_reads_run(tmp_path) -> None:
    run_dir = tmp_path / ".ruiclaw" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(json.dumps({
        "run_id": "run-1",
        "status": "succeeded",
        "session_key": "cli:direct",
        "started_at": "2026-09-13T01:00:00+00:00",
    }), encoding="utf-8")
    events = [
        {"event_type": "run_started", "payload": {}},
        {"event_type": "model_call_started", "payload": {"model": "test-model"}},
        {"event_type": "run_finished", "payload": {"status": "succeeded"}},
    ]
    (run_dir / "events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events),
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(json.dumps({
        "duration_ms": 25,
        "model_calls": 1,
        "provider_calls": 1,
        "tool_calls": 0,
        "usage": {"total_tokens": 12},
        "cost": {"status": "estimated", "estimated_cost_usd": 0.0012},
    }), encoding="utf-8")

    inspector = RunInspector(tmp_path)
    rows = inspector.list_runs()
    detail = inspector.get_run("run-1")

    assert rows[0]["run_id"] == "run-1"
    assert rows[0]["model"] == "test-model"
    assert rows[0]["cost_status"] == "estimated"
    assert rows[0]["estimated_cost_usd"] == 0.0012
    assert rows[0]["provider_calls"] == 1
    assert detail is not None
    assert detail["summary"] == rows[0]
    assert detail["event_count"] == 3
    assert detail["events_truncated"] is False


def test_run_inspector_rejects_traversal_and_symlinks(tmp_path) -> None:
    runs_dir = tmp_path / ".ruiclaw" / "runs"
    runs_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "manifest.json").write_text("{}", encoding="utf-8")
    (runs_dir / "linked").symlink_to(outside, target_is_directory=True)

    inspector = RunInspector(tmp_path)

    assert inspector.get_run("../outside") is None
    assert inspector.get_run("linked") is None
    assert inspector.list_runs() == []


def test_run_inspector_bounds_detail_events(tmp_path) -> None:
    run_dir = tmp_path / ".ruiclaw" / "runs" / "many"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "many", "status": "running", "session_key": "cli:test"}),
        encoding="utf-8",
    )
    lines = [json.dumps({"event_type": "tick", "sequence": index}) for index in range(510)]
    (run_dir / "events.jsonl").write_text("\n".join(lines), encoding="utf-8")

    detail = RunInspector(tmp_path).get_run("many")

    assert detail is not None
    assert len(detail["events"]) == MAX_DETAIL_EVENTS
    assert detail["events"][0]["sequence"] == 10
    assert detail["event_count"] == 510
    assert detail["events_truncated"] is True
