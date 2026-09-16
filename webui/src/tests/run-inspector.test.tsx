import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

import { RunInspectorSettings } from "@/components/settings/RunInspectorSettings";

afterEach(() => vi.unstubAllGlobals());

it("renders a run, context sources, and timeline", async () => {
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const body = url.includes("/api/webui/runs/run-1")
      ? {
          summary: {
            run_id: "run-1", status: "succeeded", model: "test-model",
            session_key: "cli:test", started_at: null, duration_ms: 42,
            total_tokens: 120, cost_status: "estimated", estimated_cost_usd: 0.001234,
            model_calls: 1, provider_calls: 1, tool_calls: 1,
          },
          manifest: {},
          report: { context: { max_source_tokens: { bootstrap: 80, user_input: 40 } } },
          events: [{ sequence: 1, event_type: "tool_call_started", payload: { tool_name: "read_file" } }],
          event_count: 1,
          events_truncated: false,
        }
      : {
          runs: [{
            run_id: "run-1", status: "succeeded", model: "test-model",
            session_key: "cli:test", started_at: null, duration_ms: 42,
            total_tokens: 120, cost_status: "estimated", estimated_cost_usd: 0.001234,
            model_calls: 1, provider_calls: 1, tool_calls: 1,
          }],
        };
    return { ok: true, json: async () => body } as Response;
  }));

  render(<RunInspectorSettings token="tok" />);

  expect(await screen.findByText("run-1")).toBeInTheDocument();
  expect(await screen.findByText("bootstrap")).toBeInTheDocument();
  expect(screen.getByText("$0.001234")).toBeInTheDocument();
  expect(screen.getByText("tool_call_started")).toBeInTheDocument();
  expect(screen.getByText("read_file")).toBeInTheDocument();
});
