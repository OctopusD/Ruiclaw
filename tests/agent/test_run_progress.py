from pathlib import Path
from types import SimpleNamespace

from ruiclaw.agent.run_progress import RunProgressTracker


def test_tracker_records_completed_failed_and_pending_actions() -> None:
    tracker = RunProgressTracker("run-1", "session-1")
    tracker.set_pending([
        {"id": "call-1", "function": {"name": "read_file"}},
        {"id": "call-2", "function": {"name": "pytest"}},
    ])
    assert [item["tool"] for item in tracker.snapshot.pending_tool_calls] == [
        "read_file",
        "pytest",
    ]

    tracker.observe_results(
        [
            SimpleNamespace(name="read_file", arguments={"path": "src/app.py"}),
            SimpleNamespace(name="pytest", arguments={"command": "pytest"}),
        ],
        [
            {"name": "read_file", "status": "ok"},
            {"name": "pytest", "status": "error", "detail": "import error"},
        ],
    )

    assert tracker.snapshot.pending_tool_calls == []
    assert tracker.snapshot.completed_actions == [
        {"tool": "read_file", "target": "src/app.py"}
    ]
    assert tracker.snapshot.failed_actions[0]["error_summary"] == "import error"
    assert "pytest" in tracker.snapshot.resume_hint
    assert "Current Run Progress" in tracker.snapshot.render()


def test_tracker_deduplicates_actions_and_bounds_artifacts() -> None:
    tracker = RunProgressTracker("run-1")
    tracker.observe_results(
        [SimpleNamespace(name="list_dir", arguments={})],
        [{"name": "list_dir", "status": "ok"}],
    )
    tracker.observe_results(
        [SimpleNamespace(name="list_dir", arguments={})],
        [{"name": "list_dir", "status": "ok"}],
    )
    assert len(tracker.snapshot.completed_actions) == 1

    for index in range(30):
        tracker.add_artifact(f"file-{index}.py")
    assert len(tracker.snapshot.artifacts) == 24
    assert tracker.snapshot.artifacts[0] == "file-6.py"


def test_tracker_persists_atomically_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / ".ruiclaw" / "runs" / "run-1" / "progress.json"
    tracker = RunProgressTracker("run-1", persist_path=path)
    tracker.add_artifact("src/app.py")
    assert path.is_file()
    assert not list(path.parent.glob(".*.progress.json.*"))

    restored = RunProgressTracker("run-1", persist_path=path)
    assert restored.snapshot.artifacts == ["src/app.py"]

    other = RunProgressTracker("run-2", persist_path=path)
    assert other.snapshot.artifacts == []
