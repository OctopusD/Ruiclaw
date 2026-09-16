from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ruiclaw.agent.hook import AgentHookContext, AgentRunHookContext, ModelRequestMetrics
from ruiclaw.llm_usage.models import LLMCallRecord
from ruiclaw.observability.report import rebuild_report
from ruiclaw.observability.run_ledger import RunLedger, RunLedgerHook
from ruiclaw.providers.base import LLMResponse, LLMUsage, ToolCallRequest


@pytest.mark.asyncio
async def test_run_ledger_writes_ordered_events_and_rebuildable_report(tmp_path) -> None:
    ledger = RunLedger(
        runs_dir=tmp_path / ".ruiclaw" / "runs",
        run_id="run-1",
        session_key="cli:direct",
        turn_id="turn-1",
    )
    await ledger.start()
    physical_calls: list[LLMCallRecord] = []
    hook = RunLedgerHook(
        ledger,
        provider="openai",
        model="gpt-5.3-codex",
        physical_calls=physical_calls,
    )
    context = AgentHookContext(
        iteration=0,
        messages=[],
        model_request=ModelRequestMetrics(
            message_count=2,
            system_tokens=6,
            user_tokens=4,
            assistant_tokens=0,
            tool_result_tokens=0,
            other_tokens=0,
            tool_definition_tokens=2,
            total_tokens=12,
            token_source="test",
            context_window_tokens=100,
            input_budget_tokens=80,
            utilization_ratio=0.12,
            compacted=False,
            runtime_context_sources=("identity",),
            source_tokens=(("bootstrap", 6), ("user_input", 4), ("tool_definitions", 2)),
            memory_candidate_count=3,
            memory_selected_count=1,
            memory_stale_rejected_count=1,
            memory_selected=(("mem-1", 8, ("path_match",), "agent/loop.py"),),
        ),
    )

    await hook.before_model_call(context)
    context.response = LLMResponse(content="", finish_reason="tool_calls")
    context.usage = LLMUsage.reported(input_tokens=10, output_tokens=2)
    physical_calls.append(LLMCallRecord(
        started_at_ms=1,
        duration_ms=3,
        provider="openai",
        model="gpt-5.3-codex",
        source="user",
        stream=False,
        finish_reason="tool_calls",
        usage=context.usage,
    ))
    context.tool_calls = [ToolCallRequest(id="call-1", name="read_file", arguments={})]
    await hook.after_model_call(context)
    await hook.before_execute_tools(context)
    context.tool_events = [{"name": "read_file", "status": "ok", "detail": "secret"}]
    context.tool_governance = {
        "call-1": {
            "effect_type": "read_only",
            "queue_ms": "2",
            "duration_ms": "7",
            "timeout_s": "120",
            "batch_id": "tool-batch-1",
        }
    }
    await hook.after_iteration(context)
    await ledger.finish("succeeded", stop_reason="completed")

    manifest = json.loads(ledger.manifest_path.read_text(encoding="utf-8"))
    events = [
        json.loads(line)
        for line in ledger.events_path.read_text(encoding="utf-8").splitlines()
    ]
    report = json.loads(ledger.report_path.read_text(encoding="utf-8"))

    assert manifest["status"] == "succeeded"
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["event_type"] for event in events] == [
        "run_started",
        "model_context_built",
        "model_call_started",
        "provider_call_finished",
        "model_call_finished",
        "tool_call_started",
        "tool_call_finished",
        "run_finished",
    ]
    assert "secret" not in ledger.events_path.read_text(encoding="utf-8")
    assert report["model_calls"] == 1
    assert report["provider_calls"] == 1
    assert report["tool_calls"] == 1
    assert report["usage"]["total_tokens"] == 12
    assert report["usage"]["reported_tokens"] == 12
    assert report["usage"]["cache_read_tokens"] is None
    assert report["cost"]["status"] == "estimated"
    assert report["cost"]["estimated_cost_usd"] == 0.0000455
    assert report["cost"]["pricing_snapshot_id"] == "openai-standard-2026-09-13-v1"
    assert report["cost"]["basis"] == "provider_calls"
    assert report["tool_governance"] == {
        "effect_counts": {"read_only": 1},
        "batch_count": 1,
        "timed_out_calls": 0,
        "cancelled_calls": 0,
        "max_queue_ms": 2,
        "max_execution_ms": 7,
    }
    assert report["context"] == {
        "requests": 1,
        "max_total_tokens": 12,
        "max_utilization_ratio": 0.12,
        "compacted_requests": 0,
        "max_source_tokens": {
            "bootstrap": 6,
            "tool_definitions": 2,
            "user_input": 4,
        },
        "memory_retrieval": {
            "requests_with_hits": 1,
            "max_candidates": 3,
            "max_selected": 1,
            "max_stale_rejected": 1,
        },
    }
    assert rebuild_report(ledger.run_dir) == report


@pytest.mark.asyncio
async def test_reopened_ledger_continues_sequence_without_second_start(tmp_path) -> None:
    first = RunLedger(tmp_path, "run-1", "cli:direct", "turn-1")
    await first.start()
    await first.append("context_built")

    continuation = RunLedger(tmp_path, "run-1", "cli:direct", "turn-2")
    await continuation.start()
    await continuation.finish("succeeded")

    events = [
        json.loads(line)
        for line in continuation.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event_type"] for event in events].count("run_started") == 1
    assert [event["sequence"] for event in events] == [1, 2, 3]


@pytest.mark.asyncio
async def test_run_ledger_closes_inflight_tool_as_cancelled(tmp_path) -> None:
    ledger = RunLedger(tmp_path, "run-1", "cli:direct", "turn-1")
    await ledger.start()
    hook = RunLedgerHook(ledger, model="test-model")
    context = AgentHookContext(
        iteration=0,
        messages=[],
        tool_calls=[ToolCallRequest(id="call-1", name="exec", arguments={})],
    )

    await hook.before_execute_tools(context)
    await hook.before_execute_tool(
        context,
        context.tool_calls[0],
        SimpleNamespace(effect_type="workspace_write"),
        {},
    )
    await hook.on_finally(AgentRunHookContext(messages=[]))

    events = [
        json.loads(line)
        for line in ledger.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert events[-1]["event_type"] == "tool_call_finished"
    assert events[-1]["payload"]["status"] == "cancelled"
    assert events[-1]["payload"]["effect_type"] == "workspace_write"
