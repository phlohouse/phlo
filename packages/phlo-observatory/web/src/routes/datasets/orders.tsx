/**
 * Dataset detail: Orders with Overview, Schema, Preview, Quality, Lineage,
 * Runs and Contract & access tabs translated from the Paper Dataset artboards.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ArrowRight, Bell, ChevronRight, Search } from "lucide-react";
import * as React from "react";

const TABS = ["Overview", "Schema · 8", "Preview", "Quality", "Lineage", "Runs", "Contract & access"] as const;

const SCHEMA: [string, string, string, string][] = [
  ["order_id", "string", "No", "Primary key · Unique"],
  ["customer_id", "string", "No", "Customer reference"],
  ["created_at", "timestamp", "No", "Partition source"],
  ["status", "string", "No", "Accepted values"],
  ["order_total", "decimal(12,2)", "No", "Non-negative"],
  ["currency", "string", "No", "ISO 4217"],
  ["line_items", "json", "Yes", "Nested array"],
  ["updated_at", "timestamp", "Yes", "CDC watermark"],
];

const PREVIEW_ROWS: [string, string, string, string, string][] = [
  ["ORD-20913", "C-88142", "2026-09-13 08:59:01", "shipped", "412.90"],
  ["ORD-20914", "C-10293", "2026-09-13 08:59:02", "pending", "89.99"],
  ["ORD-20915", "C-55310", "2026-09-13 08:59:02", "shipped", "1204.00"],
  ["ORD-20916", "C-88142", "2026-09-13 08:59:03", "cancelled", "45.50"],
  ["ORD-20917", "C-29401", "2026-09-13 08:59:04", "shipped", "231.75"],
  ["ORD-20918", "C-77120", "2026-09-13 08:59:05", "pending", "99.00"],
];

const CHECKS: [string, string, string][] = [
  ["order_id must be unique", "Failed · blocking", "var(--color-danger)"],
  ["order_total non-negative", "Passed", "#07835D"],
  ["currency in ISO 4217", "Passed", "#07835D"],
  ["status in accepted set", "Passed", "#07835D"],
  ["created_at not null", "Passed", "#07835D"],
  ["customer_id references dim_customer", "Warning", "var(--color-orange)"],
];

const RUNS: [string, string, string, string, string][] = [
  ["r7e42b", "09:27", "Failed validation", "Not promoted", "2m 14s"],
  ["r6b190", "08:00", "Succeeded", "938105", "1m 58s"],
  ["r5d871", "07:00", "Succeeded", "938104", "2m 03s"],
];

function outcomeColor(o: string) {
  if (/fail/i.test(o)) return "var(--color-danger)";
  if (/succeed|pass/i.test(o)) return "#07835D";
  return "var(--color-text)";
}

function DatasetDetail() {
  const navigate = useNavigate();
  const [tab, setTab] = React.useState<(typeof TABS)[number]>("Overview");

  return (
    <div className="mc-card" style={{ overflow: "hidden" }}>
      <div style={{ display: "flex", alignItems: "center", height: 46, padding: "0 24px", borderBottom: "1px solid var(--color-border)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12 }}>
          <span style={{ color: "var(--color-muted)" }}>Data</span>
          <ChevronRight size={14} />
          <span style={{ color: "var(--color-muted)" }}>Datasets</span>
          <ChevronRight size={14} />
          <span style={{ fontWeight: 600 }}>Orders</span>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 18, color: "var(--color-muted)" }}>
          <Search size={16} />
          <span style={{ display: "flex", alignItems: "center", gap: 7, fontSize: 12 }}>
            <span style={{ width: 5, height: 5, borderRadius: 2, background: "#07835D", display: "inline-block" }} />
            Production
          </span>
          <Bell size={16} />
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "flex-start", padding: "20px 24px 16px" }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <h1 style={{ fontSize: 25, fontWeight: 600, margin: 0, letterSpacing: "-0.03em", fontFamily: "var(--font-display)" }}>
              Orders
            </h1>
            <span style={{ fontSize: 11, background: "#E7F7F0", color: "#07835D", borderRadius: 4, padding: "4px 8px" }}>
              Published
            </span>
            <span style={{ fontSize: 10, border: "1px solid var(--color-border)", borderRadius: 4, padding: "3px 7px", color: "var(--color-muted)" }}>
              Example data
            </span>
          </div>
          <div style={{ fontSize: 12, color: "var(--color-muted)", marginTop: 6 }}>
            marts.orders · Order-level revenue and fulfilment data for analytics and downstream APIs.
          </div>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8 }}>
          <button className="mc-btn mc-btn-ghost">Preview materialization</button>
          <button className="mc-btn mc-btn-primary" onClick={() => setTab("Preview")}>
            Explore data
          </button>
        </div>
      </div>

      <div style={{ display: "flex", padding: "0 24px 16px" }}>
        {[
          ["Freshness", "Fresh", "#07835D", "95m old · 2h freshness target"],
          ["Last released", "08:00 UTC", "var(--color-text)", "13 Sep 2026 · Run r6b190"],
          ["Released snapshot", "938105", "var(--color-text)", "Iceberg · Nessie main"],
          ["Released rows", "1,187,320", "var(--color-text)", "24.8 MB · 12 data files"],
          ["Released quality", "12 / 12 passed", "#07835D", "Checks bound to snapshot 938105"],
        ].map(([k, v, c, s], i) => (
          <div key={k} style={{ flex: 1, padding: "11px 12px", background: "#FAFAFA", border: "1px solid var(--color-border)", borderLeft: i === 0 ? "1px solid var(--color-border)" : "none" }}>
            <div style={{ fontSize: 11, color: "var(--color-muted)" }}>{k}</div>
            <div style={{ fontSize: 15, fontWeight: 600, color: c as string, margin: "3px 0" }}>{v}</div>
            <div style={{ fontSize: 10, color: "var(--color-muted)" }}>{s}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", padding: "0 24px", gap: 24, borderBottom: "1px solid var(--color-border)", height: 38 }}>
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
          {tab === "Overview" && (
            <>
              <div
                onClick={() => navigate({ to: "/runs/orders-daily" })}
                style={{ display: "flex", alignItems: "center", gap: 10, padding: "11px 12px", borderRadius: 7, background: "#FFF8F8", border: "1px solid var(--color-border)", cursor: "pointer", marginBottom: 18 }}
              >
                <span style={{ color: "var(--color-danger)", fontSize: 15 }}>ⓘ</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 12, fontWeight: 600 }}>Next delivery blocked by a quality check</div>
                  <div style={{ fontSize: 11, color: "var(--color-muted)", marginTop: 2 }}>Run r7e42b · 42 duplicate order IDs · Released data is unchanged.</div>
                </div>
                <span style={{ fontSize: 11, color: "var(--color-accent-dark)" }}>Inspect run</span>
              </div>

              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Lineage &amp; consumers</h2>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-accent-dark)", cursor: "pointer" }} onClick={() => setTab("Lineage")}>
                  View full graph →
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 6 }}>
                {[
                  ["postgres.orders", "Postgres · Source", false],
                  ["stg_orders", "dbt · Model", false],
                  ["marts.orders", "This dataset", true],
                  ["3 consumers", "Analytics · API · dbt", false],
                ].map(([n, s, hot], i) => (
                  <React.Fragment key={i}>
                    {i > 0 && <ArrowRight size={15} color="#999" style={{ flexShrink: 0 }} />}
                    <div style={{ flex: 1, padding: "12px 10px", borderRadius: 7, background: "#FAFAFA", border: hot ? "1px solid #C6B8FA" : "1px solid var(--color-border)", backgroundColor: hot ? "#F4F1FF" : undefined }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: hot ? "var(--color-accent-dark)" : "var(--color-text)" }}>{n}</div>
                      <div style={{ fontSize: 11, color: "var(--color-muted)", marginTop: 3 }}>{s}</div>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "var(--color-muted)", marginBottom: 18 }}>
                Declared dependencies · Consumers read released snapshot 938105
              </div>

              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Schema</h2>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-accent-dark)", cursor: "pointer" }} onClick={() => setTab("Schema · 8")}>
                  8 columns · View schema →
                </span>
              </div>
              <SchemaTable rows={SCHEMA.slice(0, 6)} />
              <div style={{ fontSize: 11, color: "var(--color-muted)", margin: "8px 0 18px" }}>
                Showing 6 of 8 columns · Schema version 3 · No pending changes
              </div>

              <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
                <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>Recent runs</h2>
                <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-accent-dark)", cursor: "pointer" }} onClick={() => navigate({ to: "/runs/orders-daily" })}>
                  View all runs →
                </span>
              </div>
              <RunsTable rows={RUNS} onRun={() => navigate({ to: "/runs/orders-daily" })} />
            </>
          )}

          {tab === "Schema · 8" && (
            <>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Schema · 8 columns · v3</h2>
              <SchemaTable rows={SCHEMA} />
            </>
          )}

          {tab === "Preview" && (
            <>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Preview · released snapshot 938105</h2>
              <div style={{ border: "1px solid var(--color-border)", borderRadius: 7, overflow: "hidden" }}>
                <div style={{ display: "flex", height: 28, padding: "0 12px", background: "var(--color-accent-soft)", fontSize: 11, fontWeight: 500, color: "var(--color-accent-dark)", alignItems: "center" }}>
                  <span style={{ width: 110 }}>order_id</span>
                  <span style={{ width: 110 }}>customer_id</span>
                  <span style={{ flex: 1 }}>created_at</span>
                  <span style={{ width: 90 }}>status</span>
                  <span style={{ width: 80, textAlign: "right" }}>order_total</span>
                </div>
                {PREVIEW_ROWS.map(([o, c, t, s, tot]) => (
                  <div key={o} style={{ display: "flex", height: 30, padding: "0 12px", borderTop: "1px solid var(--color-border)", fontSize: 11, alignItems: "center" }}>
                    <span style={{ width: 110, fontWeight: 550 }}>{o}</span>
                    <span style={{ width: 110, color: "var(--color-muted)" }}>{c}</span>
                    <span style={{ flex: 1, color: "#525252" }}>{t}</span>
                    <span style={{ width: 90 }}>{s}</span>
                    <span style={{ width: 80, textAlign: "right" }}>{tot}</span>
                  </div>
                ))}
              </div>
              <div style={{ fontSize: 11, color: "var(--color-muted)", marginTop: 8 }}>Sample · 6 of 1,187,320 rows · snapshot 938105</div>
            </>
          )}

          {tab === "Quality" && (
            <>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Quality · 11 passed · 1 failed</h2>
              {CHECKS.map(([n, st, c]) => (
                <div key={n} style={{ display: "flex", padding: "9px 0", borderTop: "1px solid var(--color-border)", fontSize: 12 }}>
                  <span style={{ fontWeight: 550 }}>{n}</span>
                  <span style={{ marginLeft: "auto", color: c as string }}>{st}</span>
                </div>
              ))}
            </>
          )}

          {tab === "Lineage" && (
            <>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Lineage · depth 2</h2>
              {[
                ["postgres.orders", "Postgres · Source", "Upstream"],
                ["stg_orders", "dbt · Model", "Upstream"],
                ["marts.orders", "This dataset", "Current"],
                ["Revenue dashboard", "Superset · Analytics", "Downstream"],
                ["Orders API", "PostgREST · Commerce", "Downstream"],
                ["Finance reconciliation", "dbt · Finance", "Downstream"],
              ].map(([n, s, d]) => (
                <div key={n} style={{ display: "flex", padding: "9px 0", borderTop: "1px solid var(--color-border)", fontSize: 12, alignItems: "center" }}>
                  <span style={{ fontWeight: 600 }}>{n}</span>
                  <span style={{ color: "var(--color-muted)", marginLeft: 8 }}>{s}</span>
                  <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--color-accent-dark)" }}>{d}</span>
                </div>
              ))}
            </>
          )}

          {tab === "Runs" && (
            <>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Runs</h2>
              <RunsTable rows={RUNS} onRun={() => navigate({ to: "/runs/orders-daily" })} />
            </>
          )}

          {tab === "Contract & access" && (
            <>
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Contract v3 · Approved</h2>
              {[
                ["Freshness target", "2 hours"],
                ["Accepted statuses", "pending · shipped · cancelled"],
                ["PII handling", "customer_id tokenized downstream"],
                ["Retention", "7 years · cold after 1"],
                ["Breaking-change policy", "Minor versions only · 30-day notice"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", padding: "9px 0", borderTop: "1px solid var(--color-border)", fontSize: 12 }}>
                  <span style={{ color: "var(--color-muted)", width: 180 }}>{k}</span>
                  <span>{v}</span>
                </div>
              ))}
              <h2 style={{ fontSize: 15, fontWeight: 600, margin: "18px 0 10px", fontFamily: "var(--font-display)" }}>Access</h2>
              {[
                ["Analytics", "Read · Sup932"],
                ["Finance", "Read · Sup932"],
                ["Orders API", "Service · PostgREST"],
              ].map(([k, v]) => (
                <div key={k} style={{ display: "flex", padding: "9px 0", borderTop: "1px solid var(--color-border)", fontSize: 12 }}>
                  <span style={{ color: "var(--color-muted)", width: 180 }}>{k}</span>
                  <span>{v}</span>
                </div>
              ))}
            </>
          )}
        </div>

        <aside style={{ width: 300, flexShrink: 0, borderLeft: "1px solid var(--color-border)", paddingLeft: 18 }}>
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: "0 0 10px", fontFamily: "var(--font-display)" }}>Ownership &amp; contract</h2>
          {[
            ["Owner", "Data platform"],
            ["Domain", "Commerce"],
            ["Freshness target", "2 hours"],
            ["Schedule", "Hourly"],
            ["Classification", "Internal"],
            ["Contract version", "v3 · Approved"],
          ].map(([k, v]) => (
            <div key={k} style={{ display: "flex", padding: "7px 0", borderBottom: "1px solid #EEEEEE", fontSize: 12 }}>
              <span style={{ color: "var(--color-muted)" }}>{k}</span>
              <span style={{ marginLeft: "auto", fontWeight: 500 }}>{v}</span>
            </div>
          ))}
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: "18px 0 4px", fontFamily: "var(--font-display)" }}>Consumers</h2>
          <div style={{ fontSize: 11, color: "var(--color-muted)", marginBottom: 8 }}>3 downstream</div>
          {[
            ["Revenue dashboard", "Superset · Analytics"],
            ["Orders API", "PostgREST · Commerce"],
            ["Finance reconciliation", "dbt · Finance"],
          ].map(([n, s]) => (
            <div key={n} style={{ display: "flex", alignItems: "center", padding: "6px 0" }}>
              <div>
                <div style={{ fontSize: 12, fontWeight: 500 }}>{n}</div>
                <div style={{ fontSize: 10, color: "var(--color-muted)" }}>{s}</div>
              </div>
              <ChevronRight size={15} color="var(--color-muted)" style={{ marginLeft: "auto" }} />
            </div>
          ))}
          <h2 style={{ fontSize: 15, fontWeight: 600, margin: "18px 0 10px", fontFamily: "var(--font-display)" }}>Delivery &amp; access</h2>
          {[
            ["Serving target", "Postgres · marts.orders"],
            ["REST endpoint", "PostgREST · /orders"],
            ["Read access", "Analytics · Finance"],
            ["Service access", "Orders API"],
          ].map(([k, v]) => (
            <div key={k} style={{ display: "flex", padding: "6px 0", fontSize: 11 }}>
              <span style={{ color: "var(--color-muted)" }}>{k}</span>
              <span style={{ marginLeft: "auto" }}>{v}</span>
            </div>
          ))}
          <div style={{ fontSize: 11, color: "var(--color-accent-dark)", marginTop: 8, cursor: "pointer" }} onClick={() => setTab("Contract & access")}>
            View contract &amp; access →
          </div>
        </aside>
      </div>

      <div style={{ display: "flex", padding: "12px 24px", borderTop: "1px solid var(--color-border)", fontSize: 10, color: "var(--color-muted)" }}>
        <span>Updated 09:35 UTC · Showing released snapshot 938105</span>
        <span style={{ marginLeft: "auto" }}>Production · Example data</span>
      </div>
    </div>
  );
}

function SchemaTable({ rows }: { rows: [string, string, string, string][] }) {
  return (
    <div style={{ border: "1px solid var(--color-border)", borderRadius: 7, overflow: "hidden" }}>
      <div style={{ display: "flex", height: 28, padding: "0 12px", background: "var(--color-accent-soft)", fontSize: 11, fontWeight: 500, color: "var(--color-accent-dark)", alignItems: "center" }}>
        <span style={{ width: 190 }}>Field</span>
        <span style={{ width: 160 }}>Type</span>
        <span style={{ width: 90 }}>Nullable</span>
        <span style={{ flex: 1 }}>Role / validation</span>
      </div>
      {rows.map(([f, t, n, r]) => (
        <div key={f} style={{ display: "flex", height: 28, padding: "0 12px", borderTop: "1px solid #EEEEEE", fontSize: 12, alignItems: "center" }}>
          <span style={{ width: 190, fontWeight: 500 }}>{f}</span>
          <span style={{ width: 160 }}>{t}</span>
          <span style={{ width: 90, color: "var(--color-muted)" }}>{n}</span>
          <span style={{ flex: 1 }}>{r}</span>
        </div>
      ))}
    </div>
  );
}

function RunsTable({ rows, onRun }: { rows: [string, string, string, string, string][]; onRun: () => void }) {
  return (
    <div style={{ border: "1px solid var(--color-border)", borderRadius: 7, overflow: "hidden" }}>
      <div style={{ display: "flex", height: 30, padding: "0 12px", background: "var(--color-accent-soft)", fontSize: 11, fontWeight: 500, color: "var(--color-accent-dark)", alignItems: "center" }}>
        <span style={{ width: 150 }}>Run</span>
        <span style={{ width: 150 }}>Finished (UTC)</span>
        <span style={{ flex: 1 }}>Outcome</span>
        <span style={{ width: 150 }}>Release</span>
        <span style={{ width: 70, textAlign: "right" }}>Duration</span>
      </div>
      {rows.map(([r, f, o, rel, d]) => (
        <div key={r} onClick={onRun} style={{ display: "flex", height: 30, padding: "0 12px", borderTop: "1px solid #EEEEEE", fontSize: 12, alignItems: "center", cursor: "pointer" }}>
          <span style={{ width: 150, color: "var(--color-accent-dark)" }}>{r}</span>
          <span style={{ width: 150 }}>{f}</span>
          <span style={{ flex: 1, color: outcomeColor(o) }}>{o}</span>
          <span style={{ width: 150 }}>{rel}</span>
          <span style={{ width: 70, textAlign: "right" }}>{d}</span>
        </div>
      ))}
    </div>
  );
}

export const Route = createFileRoute("/datasets/orders")({ component: DatasetDetail });
