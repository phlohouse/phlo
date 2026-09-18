/**
 * Run detail: orders_daily r7e42b with Evidence, Logs, Traces, Artifacts and
 * Configuration tabs translated from the Paper Run artboards.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { Bell, ChevronRight, Play, Search } from "lucide-react";
import * as React from "react";
import { StatusPill } from "../../components/mc";

const TABS = ["Evidence", "Logs · 128", "Traces", "Artifacts · 6", "Configuration"] as const;

const STAGES = [
  { name: "Ingest orders", provider: "dlt", outcome: "Succeeded", color: "#07835D", from: 0, width: 25, dur: "34s" },
  { name: "Build orders mart", provider: "dbt", outcome: "Succeeded", color: "#07835D", from: 25, width: 54, dur: "1m 12s" },
  { name: "Validate candidate", provider: "Pandera", outcome: "Failed", color: "var(--color-danger)", from: 79, width: 21, dur: "28s", hot: true },
  { name: "Promote branch", provider: "Nessie", outcome: "Blocked", color: "var(--color-orange)", from: 0, width: 0, dur: "—", note: "No provider mutation" },
  { name: "Deliver to target", provider: "Postgres", outcome: "Not started", color: "var(--color-muted)", from: 0, width: 0, dur: "—", note: "No provider mutation" },
];

const DUPS: [string, string, string, string][] = [
  ["ORD-10482", "rec_7a8e01", "2026-09-13 09:14:08", "2"],
  ["ORD-10482", "rec_7a8e02", "2026-09-13 09:14:09", "2"],
  ["ORD-10517", "rec_7a8f14", "2026-09-13 09:18:42", "2"],
];

const EVENTS: [string, string, string, boolean][] = [
  ["09:26:46", "INFO", "Candidate snapshot 938106 created on wap/r7e42b.", false],
  ["09:27:14", "ERROR", "unique_order_id failed: 42 rows. Release withheld.", true],
  ["09:27:14", "INFO", "Evidence complete. Released snapshot 938105 unchanged.", false],
];

const DETAILS: [string, string][] = [
  ["Asset", "marts.orders"],
  ["Orchestrator", "Dagster"],
  ["Trigger", "Schedule · orders_hourly"],
  ["Partition", "2026-09-13"],
  ["Code version", "a41c9f2"],
  ["Candidate snapshot", "938106"],
  ["Released snapshot", "938105"],
  ["Candidate branch", "wap/r7e42b"],
];

const CONSUMERS = [
  ["Revenue dashboard", "Superset · Analytics"],
  ["Orders API", "PostgREST · Commerce"],
  ["Finance reconciliation", "dbt · Finance"],
];

const ARTIFACTS = ["quality-results.json", "failed-rows.parquet", "dbt-run-results.json"];

function RunDetail() {
  const navigate = useNavigate();
  const [tab, setTab] = React.useState<(typeof TABS)[number]>("Evidence");

  return (
    <div>
      <div style={{ display: "flex", alignItems: "flex-start", padding: "4px 0 16px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1 style={{ fontSize: 25, fontWeight: 600, margin: 0, letterSpacing: "-0.03em", fontFamily: "var(--font-display)" }}>
              orders_daily
            </h1>
            <span style={{ fontSize: 11, background: "#FFF1F1", color: "var(--color-danger)", borderRadius: 4, padding: "4px 8px" }}>
              Failed validation
            </span>
            <span style={{ fontSize: 10, border: "1px solid var(--color-border)", borderRadius: 4, padding: "3px 7px", color: "var(--color-muted)" }}>
              Example data
            </span>
          </div>
          <div style={{ fontSize: 12, color: "var(--color-muted)", marginTop: 6 }}>
            Run r7e42b · 13 Sep 2026, 09:25 UTC · Scheduled · Partition 2026-09-13
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="mc-btn mc-btn-ghost">Compare with last success</button>
          <button className="mc-btn mc-btn-primary" onClick={() => navigate({ to: "/releases" })}>
            <Play size={14} />
            Preview retry
          </button>
        </div>
      </div>

      <div style={{ display: "flex", padding: "0 24px 16px" }}>
        {[
          ["Execution", "Failed", "var(--color-danger)", "Blocking quality check"],
          ["Evidence", "Complete", "#07835D", "All required stages recorded"],
          ["Release", "Not promoted", "var(--color-orange)", "Consumers remain on 08:00"],
          ["Duration", "2m 14s", "var(--color-text)", "09:25:00 – 09:27:14"],
          ["Attempt", "1 of 1", "var(--color-text)", "Previous success at 08:00"],
        ].map(([k, v, c, s], i) => (
          <div key={k} style={{ flex: 1, padding: "11px 12px", background: "#FAFAFA", border: "1px solid var(--color-border)", borderLeft: i === 0 ? "1px solid var(--color-border)" : "none", borderRadius: 0 }}>
            <div style={{ fontSize: 11, color: "var(--color-muted)" }}>{k}</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: c as string, margin: "3px 0" }}>{v}</div>
            <div style={{ fontSize: 10, color: "var(--color-muted)" }}>{s}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", padding: "0 24px", gap: 26, borderBottom: "1px solid var(--color-border)", height: 38 }}>
        {TABS.map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            style={{
              background: "none",
              border: "none",
              borderBottom: tab === t ? "2px solid var(--color-accent)" : "2px solid transparent",
              height: 38,
              fontSize: 12,
              fontWeight: tab === t ? 600 : 400,
              color: tab === t ? "var(--color-accent-dark)" : "var(--color-muted)",
              cursor: "pointer",
              padding: 0,
            }}
          >
            {t}
          </button>
        ))}
      </div>

      <div style={{ display: "flex", padding: "18px 24px 20px", gap: 22 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {tab === "Evidence" && (
            <>
              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Execution timeline</h2>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-muted)" }}>5 stages · all times UTC</span>
              </div>
              <div style={{ border: "1px solid var(--color-border)", borderRadius: 7, overflow: "hidden", marginBottom: 18 }}>
                <div style={{ display: "flex", alignItems: "center", height: 28, padding: "0 12px", background: "var(--color-accent-soft)", fontSize: 10, fontWeight: 500, color: "var(--color-accent-dark)" }}>
                  <span style={{ width: 145, flexShrink: 0 }}>Stage</span>
                  <span style={{ width: 65, flexShrink: 0 }}>Provider</span>
                  <span style={{ width: 98, flexShrink: 0 }}>Outcome</span>
                  <span style={{ flex: 1, display: "flex", justifyContent: "space-between", paddingRight: 14 }}>
                    <span>0:00</span>
                    <span>1:07</span>
                    <span>2:14</span>
                  </span>
                  <span style={{ width: 48, textAlign: "right", flexShrink: 0 }}>Duration</span>
                </div>
                {STAGES.map((s) => (
                  <div key={s.name} style={{ display: "flex", alignItems: "center", height: 33, padding: "0 12px", borderTop: "1px solid var(--color-border)", background: s.hot ? "#FFF8F8" : "var(--color-panel)", fontSize: 11 }}>
                    <span style={{ width: 145, flexShrink: 0, fontWeight: 550 }}>{s.name}</span>
                    <span style={{ width: 65, flexShrink: 0, color: "var(--color-muted)" }}>{s.provider}</span>
                    <span style={{ width: 98, flexShrink: 0, color: s.color }}>{s.outcome}</span>
                    <span style={{ flex: 1, display: "flex", alignItems: "center", paddingRight: 14 }}>
                      {s.width > 0 ? (
                        <>
                          <span style={{ height: 8, width: `${s.from}%` }} />
                          <span style={{ height: 8, width: `${s.width}%`, borderRadius: 2, background: s.color, opacity: 0.7 }} />
                        </>
                      ) : (
                        <span style={{ fontSize: 10, color: "var(--color-muted)" }}>{s.note}</span>
                      )}
                    </span>
                    <span style={{ width: 48, textAlign: "right", flexShrink: 0, color: "var(--color-muted)" }}>{s.dur}</span>
                  </div>
                ))}
              </div>

              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Quality failure</h2>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-muted)" }}>1 failed · 11 passed</span>
              </div>
              <div style={{ border: "1px solid var(--color-border)", borderRadius: 7, overflow: "hidden", marginBottom: 18 }}>
                <div style={{ padding: "11px 12px", background: "#FFF8F8" }}>
                  <div style={{ display: "flex", fontSize: 12, fontWeight: 600 }}>
                    order_id must be unique
                    <span style={{ marginLeft: "auto", fontSize: 11, fontWeight: 400, color: "var(--color-danger)" }}>Blocking · Pandera</span>
                  </div>
                  <div style={{ fontSize: 11, color: "#525252", marginTop: 4 }}>
                    42 of 1,204,000 candidate rows share an order_id. The candidate is retained for inspection.
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", height: 26, padding: "0 12px", background: "var(--color-accent-soft)", fontSize: 10, fontWeight: 500, color: "var(--color-accent-dark)", borderTop: "1px solid var(--color-border)" }}>
                  <span style={{ width: 150, flexShrink: 0 }}>order_id</span>
                  <span style={{ flex: 1 }}>record_id</span>
                  <span style={{ width: 180, flexShrink: 0 }}>created_at</span>
                  <span style={{ width: 112, textAlign: "right", flexShrink: 0 }}>Occurrences</span>
                </div>
                {DUPS.map(([o, r, c, n]) => (
                  <div key={r} style={{ display: "flex", alignItems: "center", height: 27, padding: "0 12px", borderTop: "1px solid var(--color-border)", fontSize: 11 }}>
                    <span style={{ width: 150, flexShrink: 0 }}>{o}</span>
                    <span style={{ flex: 1, color: "var(--color-muted)" }}>{r}</span>
                    <span style={{ width: 180, flexShrink: 0, color: "#525252" }}>{c}</span>
                    <span style={{ width: 112, textAlign: "right", flexShrink: 0, color: "var(--color-danger)" }}>{n}</span>
                  </div>
                ))}
                <div style={{ display: "flex", alignItems: "center", height: 28, padding: "0 12px", borderTop: "1px solid var(--color-border)", fontSize: 10 }}>
                  <span style={{ color: "var(--color-muted)" }}>Sample · 3 of 42 failed rows · Candidate snapshot 938106</span>
                  <span style={{ marginLeft: "auto", color: "var(--color-accent-dark)", cursor: "pointer" }}>Inspect all failed rows</span>
                </div>
              </div>

              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Key events</h2>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-accent-dark)", cursor: "pointer" }}>Open full logs</span>
              </div>
              {EVENTS.map(([t, lvl, m]) => (
                <div key={t + m} style={{ display: "flex", alignItems: "center", padding: "4px 0", fontSize: 11 }}>
                  <span style={{ width: 78, flexShrink: 0, color: "var(--color-muted)" }}>{t}</span>
                  <span style={{ width: 53, flexShrink: 0, fontSize: 10, color: lvl === "ERROR" ? "var(--color-danger)" : "var(--color-muted)" }}>{lvl}</span>
                  <span style={{ flex: 1, color: "#525252" }}>{m}</span>
                </div>
              ))}
            </>
          )}

          {tab === "Logs · 128" && (
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Logs · 128 lines</h2>
              <div style={{ background: "#14121a", color: "#c9c4d6", borderRadius: 7, padding: "12px 14px", fontFamily: "var(--font-mono)", fontSize: 11, lineHeight: 1.7 }}>
                <div><span style={{ color: "#6b6580" }}>09:25:00</span> INFO dlt pipeline orders_incremental start · partition 2026-09-13</div>
                <div><span style={{ color: "#6b6580" }}>09:25:34</span> INFO ingest complete · 1,204,000 rows staged in 34s</div>
                <div><span style={{ color: "#6b6580" }}>09:26:46</span> INFO candidate snapshot 938106 created on wap/r7e42b</div>
                <div><span style={{ color: "#ff8080" }}>09:27:14</span> <span style={{ color: "#ff8080" }}>ERROR</span> unique_order_id failed: 42 rows. Release withheld.</div>
                <div><span style={{ color: "#6b6580" }}>09:27:14</span> INFO evidence complete · released snapshot 938105 unchanged</div>
                <div><span style={{ color: "#6b6580" }}>09:27:15</span> INFO evidence reconciled · terminal run</div>
              </div>
            </div>
          )}

          {tab === "Traces" && (
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Traces</h2>
              <div style={{ border: "1px solid var(--color-border)", borderRadius: 7, overflow: "hidden" }}>
                {[
                  ["run.orders_daily", "134,000ms", 100, "var(--color-danger)"],
                  ["stage.ingest_orders", "34,000ms", 25, "#07835D"],
                  ["stage.build_mart", "72,000ms", 54, "#07835D"],
                  ["stage.validate", "28,000ms", 21, "var(--color-danger)"],
                ].map(([n, d, w, c]) => (
                  <div key={n as string} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderTop: "1px solid var(--color-border)", fontSize: 11 }}>
                    <span style={{ width: 170, fontFamily: "var(--font-mono)", fontSize: 10.5 }}>{n}</span>
                    <span style={{ flex: 1, height: 8, background: "var(--color-app)", borderRadius: 3 }}>
                      <span style={{ display: "block", height: "100%", width: `${w}%`, background: c as string, borderRadius: 3, opacity: 0.75 }} />
                    </span>
                    <span style={{ width: 80, textAlign: "right", color: "var(--color-muted)", fontFamily: "var(--font-mono)", fontSize: 10.5 }}>{d}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {tab === "Artifacts · 6" && (
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Evidence artifacts · 6 files</h2>
              {["quality-results.json · 4 KB", "failed-rows.parquet · 18 KB", "dbt-run-results.json · 96 KB", "snapshot-938106.manifest · 2 KB", "plan.json · 3 KB", "signatures.sig · 1 KB"].map((f) => (
                <div key={f} style={{ display: "flex", padding: "8px 0", borderTop: "1px solid var(--color-border)", fontSize: 12 }}>
                  <span style={{ color: "var(--color-accent-dark)", cursor: "pointer" }}>{f.split(" · ")[0]}</span>
                  <span style={{ marginLeft: "auto", color: "var(--color-muted)", fontSize: 11 }}>{f.split(" · ")[1]}</span>
                </div>
              ))}
              <div style={{ fontSize: 10, color: "var(--color-muted)", marginTop: 8 }}>Checksums recorded · Retained for 30 days</div>
            </div>
          )}

          {tab === "Configuration" && (
            <div>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Configuration</h2>
              {[
                ["Schedule", "orders_hourly · 25 * * * *"],
                ["Timeout", "30m per stage"],
                ["Retries", "0 · manual review required"],
                ["Quality gate", "Pandera · blocking"],
                ["Promotion", "Nessie branch wap/{run_id}"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", padding: "8px 0", borderTop: "1px solid var(--color-border)", fontSize: 12 }}>
                  <span style={{ color: "var(--color-muted)", width: 140 }}>{k}</span>
                  <span style={{ fontFamily: "var(--font-mono)", fontSize: 11 }}>{v}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <aside style={{ width: 300, flexShrink: 0, borderLeft: "1px solid var(--color-border)", paddingLeft: 18 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Run details</h2>
          {DETAILS.map(([k, v]) => (
            <div key={k} style={{ display: "flex", padding: "5px 0", fontSize: 11 }}>
              <span style={{ color: "var(--color-muted)" }}>{k}</span>
              <span style={{ marginLeft: "auto" }}>{v}</span>
            </div>
          ))}
          <div style={{ borderTop: "1px solid var(--color-border)", marginTop: 14, paddingTop: 14 }}>
            <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 4px", fontFamily: "var(--font-display)" }}>Affected consumers</h2>
            <div style={{ fontSize: 11, color: "var(--color-muted)", marginBottom: 8 }}>All three still read the last successful delivery at 08:00.</div>
            {CONSUMERS.map(([n, s]) => (
              <div key={n} style={{ display: "flex", alignItems: "center", padding: "6px 0" }}>
                <div>
                  <div style={{ fontSize: 12, fontWeight: 500 }}>{n}</div>
                  <div style={{ fontSize: 10, color: "var(--color-muted)" }}>{s}</div>
                </div>
                <ChevronRight size={15} color="var(--color-muted)" style={{ marginLeft: "auto" }} />
              </div>
            ))}
          </div>
          <div style={{ borderTop: "1px solid var(--color-border)", marginTop: 14, paddingTop: 14 }}>
            <div style={{ display: "flex", alignItems: "baseline" }}>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Evidence artifacts</h2>
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-muted)" }}>6 files</span>
            </div>
            <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 5 }}>
              {ARTIFACTS.map((a) => (
                <span key={a} style={{ fontSize: 11, color: "var(--color-accent-dark)", cursor: "pointer" }}>
                  {a}
                </span>
              ))}
            </div>
            <div style={{ fontSize: 10, color: "var(--color-muted)", marginTop: 8 }}>Checksums recorded · Retained for 30 days</div>
          </div>
        </aside>
      </div>

      <div style={{ display: "flex", alignItems: "center", height: 30, padding: "0 24px", borderTop: "1px solid var(--color-border)", fontSize: 10, color: "var(--color-muted)" }}>
        <span>Terminal run · Evidence reconciled at 09:27:15 UTC</span>
        <span style={{ marginLeft: "auto" }}>Production · Example data</span>
      </div>
      <div style={{ display: "none" }}>
        <StatusPill status="ok" />
      </div>
    </div>
  );
}

export const Route = createFileRoute("/runs/orders-daily")({ component: RunDetail });
