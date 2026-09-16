from ruiclaw.llm_usage.context import capture_llm_call, capture_llm_calls, source_from_request
from ruiclaw.llm_usage.models import LLMCallRecord


def test_automation_metadata_overrides_user_session_source() -> None:
    assert source_from_request(
        "websocket:ordinary-session",
        channel="websocket",
        metadata={"_cron_trigger": {"job_id": "job"}},
    ) == "cron"
    assert source_from_request(
        "websocket:ordinary-session",
        channel="websocket",
        metadata={"_local_trigger": {"trigger_id": "trigger"}},
    ) == "cron"


def test_api_and_system_channels_have_explicit_sources() -> None:
    assert source_from_request("shared-session", channel="api", metadata={}) == "api"
    assert source_from_request("shared-session", channel="system", metadata={}) == "system"


def test_physical_call_capture_is_scoped() -> None:
    call = LLMCallRecord(
        started_at_ms=1,
        duration_ms=2,
        provider="provider",
        model="model",
        source="user",
        stream=False,
        finish_reason="stop",
    )

    capture_llm_call(call)
    with capture_llm_calls() as captured:
        capture_llm_call(call)
    capture_llm_call(call)

    assert captured == [call]
