import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { getRun, listRuns } from "@/lib/api";
import type { RunDetail, RunEvent, RunSummary } from "@/lib/types";

function number(value: number | null | undefined): string {
  return typeof value === "number" ? value.toLocaleString() : "-";
}

function duration(value: number | null | undefined): string {
  if (typeof value !== "number") return "-";
  return value >= 1_000 ? `${(value / 1_000).toFixed(2)} s` : `${value} ms`;
}

function cost(status: RunSummary["cost_status"], value: number | null | undefined): string {
  return status === "estimated" && typeof value === "number"
    ? `$${value.toFixed(6)}`
    : status === "partial" ? "partial" : "cost unknown";
}

function eventTarget(event: RunEvent): string {
  const payload = event.payload ?? {};
  const value = payload.tool_name ?? payload.model ?? payload.status ?? payload.finish_reason;
  return typeof value === "string" ? value : "";
}

export function RunInspectorSettings({ token }: { token: string }) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const rows = await listRuns(token);
      setRuns(rows);
      setSelectedId((current) => (
        current && rows.some((row) => row.run_id === current)
          ? current
          : rows[0]?.run_id ?? null
      ));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Failed to load runs");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => { void refresh(); }, [refresh]);
  useEffect(() => {
    if (!token || !selectedId) {
      setDetail(null);
      return;
    }
    let active = true;
    void getRun(token, selectedId).then((value) => {
      if (active) setDetail(value);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : "Failed to load run");
    });
    return () => { active = false; };
  }, [selectedId, token]);

  const sources = useMemo(() => Object.entries(
    detail?.report.context?.max_source_tokens ?? {},
  ).sort((left, right) => right[1] - left[1]), [detail]);
  const largestSource = Math.max(1, ...sources.map(([, tokens]) => tokens));

  return (
    <section className="settings-stack">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-xl font-medium text-foreground">RuiClaw Run Inspector</h2>
          <p className="mt-1 text-sm text-muted-foreground">
            Inspect agent execution, context composition, and tool activity.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => void refresh()} disabled={loading}>
          <RefreshCw className={`mr-2 h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {error ? <div role="alert" className="rounded-panel border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div> : null}

      <div className="grid min-h-[32rem] gap-4 lg:grid-cols-[minmax(15rem,0.8fr)_minmax(0,2fr)]">
        <div className="overflow-hidden rounded-panel border border-border bg-settings-surface">
          <div className="border-b border-border px-4 py-3 text-sm font-medium">Recent runs</div>
          <div className="max-h-[38rem] overflow-y-auto p-2">
            {!loading && runs.length === 0 ? (
              <p className="p-4 text-sm text-muted-foreground">No runs recorded yet.</p>
            ) : runs.map((run) => (
              <button
                key={run.run_id}
                type="button"
                onClick={() => setSelectedId(run.run_id)}
                className={`mb-1 w-full rounded-control p-3 text-left transition-colors ${selectedId === run.run_id ? "bg-accent text-foreground" : "hover:bg-accent/50"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs">{run.run_id}</span>
                  <span className="rounded-full bg-muted px-2 py-0.5 text-[11px]">{run.status}</span>
                </div>
                <div className="mt-2 flex justify-between text-xs text-muted-foreground">
                  <span>{run.model ?? "unknown model"}</span>
                  <span>{number(run.total_tokens)} tokens</span>
                </div>
              </button>
            ))}
          </div>
        </div>

        <div className="space-y-4">
          {!detail ? (
            <div className="flex h-48 items-center justify-center rounded-panel border border-border bg-settings-surface text-sm text-muted-foreground">
              {loading ? "Loading runs…" : "Select a run to inspect."}
            </div>
          ) : (
            <>
              <div className="grid gap-3 sm:grid-cols-6">
                {[
                  ["Status", detail.summary.status],
                  ["Duration", duration(detail.summary.duration_ms)],
                  ["Tokens", number(detail.summary.total_tokens)],
                  ["Est. cost", cost(
                    detail.summary.cost_status,
                    detail.summary.estimated_cost_usd,
                  )],
                  ["Provider calls", number(
                    detail.summary.provider_calls || detail.summary.model_calls,
                  )],
                  ["Tool calls", number(detail.summary.tool_calls)],
                ].map(([label, value]) => (
                  <div key={label} className="rounded-panel border border-border bg-settings-surface p-4">
                    <div className="text-xs text-muted-foreground">{label}</div>
                    <div className="mt-1 truncate text-lg font-medium">{value}</div>
                  </div>
                ))}
              </div>

              <div className="rounded-panel border border-border bg-settings-surface p-4">
                <h3 className="text-sm font-medium">Peak context by source</h3>
                <div className="mt-4 space-y-3">
                  {sources.length === 0 ? <p className="text-sm text-muted-foreground">No context metrics.</p> : sources.map(([source, tokens]) => (
                    <div key={source}>
                      <div className="mb-1 flex justify-between text-xs"><span>{source}</span><span>{number(tokens)}</span></div>
                      <div className="h-2 overflow-hidden rounded-full bg-muted">
                        <div className="h-full rounded-full bg-primary" style={{ width: `${Math.max(2, (tokens / largestSource) * 100)}%` }} />
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              <div className="overflow-hidden rounded-panel border border-border bg-settings-surface">
                <div className="border-b border-border px-4 py-3 text-sm font-medium">Timeline</div>
                <div className="max-h-72 overflow-y-auto divide-y divide-border">
                  {detail.events.map((event, index) => (
                    <div key={`${event.sequence ?? index}-${event.event_type ?? "event"}`} className="grid grid-cols-[3rem_minmax(0,1fr)_auto] gap-3 px-4 py-2.5 text-xs">
                      <span className="text-muted-foreground">#{event.sequence ?? index + 1}</span>
                      <span className="truncate font-mono">{event.event_type ?? "event"}</span>
                      <span className="max-w-40 truncate text-muted-foreground">{eventTarget(event)}</span>
                    </div>
                  ))}
                </div>
                {detail.events_truncated ? <p className="border-t border-border px-4 py-2 text-xs text-muted-foreground">Showing the latest {detail.events.length} of {detail.event_count} events.</p> : null}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
