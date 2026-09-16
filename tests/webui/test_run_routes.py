from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from websockets.datastructures import Headers
from websockets.http11 import Request as WsRequest

from ruiclaw.webui.ws_http import GatewayHTTPHandler


def _handler(workspace: Path) -> GatewayHTTPHandler:
    handler = object.__new__(GatewayHTTPHandler)
    handler.skills_workspace_path = workspace
    handler.check_api_token = lambda _request: True  # type: ignore[method-assign]
    return handler


def _payload(response: Any) -> dict[str, Any]:
    return json.loads(bytes(response.body).decode())


def test_run_routes_list_and_read_detail(tmp_path) -> None:
    run_dir = tmp_path / ".ruiclaw" / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "manifest.json").write_text(
        json.dumps({"run_id": "run-1", "status": "succeeded", "session_key": "cli:test"}),
        encoding="utf-8",
    )
    (run_dir / "report.json").write_text(
        json.dumps({"model_calls": 0, "tool_calls": 0, "usage": {"total_tokens": 0}}),
        encoding="utf-8",
    )
    (run_dir / "events.jsonl").write_text("", encoding="utf-8")
    handler = _handler(tmp_path)

    listed = handler._handle_webui_runs(WsRequest("/api/webui/runs?limit=10", Headers()))
    detail = handler._handle_webui_run_detail(
        WsRequest("/api/webui/runs/run-1", Headers()),
        "run-1",
    )

    assert _payload(listed)["runs"][0]["run_id"] == "run-1"
    assert _payload(detail)["summary"]["status"] == "succeeded"


def test_run_detail_rejects_path_traversal(tmp_path) -> None:
    response = _handler(tmp_path)._handle_webui_run_detail(
        WsRequest("/api/webui/runs/..%2Fsecret", Headers()),
        "..%2Fsecret",
    )

    assert response.status_code == 404
