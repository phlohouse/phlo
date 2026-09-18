/**
 * Overview page: KPIs, needs-attention, active execution, data products, health rail.
 */
import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { ChevronRight, Clock, GitBranch, Plus } from "lucide-react";
import * as React from "react";
import { Card, SectionHead, StatusPill } from "../components/mc";
import { activeExecution, dataProducts, kpis, needsAttention, releaseQueue, services } from "../data/demo";

const ATT_ICON: Record<string, any> = { clock: Clock, branch: GitBranch };

function Overview() {
  const navigate = useNavigate();
  const [range, setRange] = React.useState("Last 24 hours");
  const [showAllServices, setShowAllServices] = React.useState(false);

  const visibleServices = showAllServices ? services : services.slice(0, 6);

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 14 }}>
        <h1 style={{ fontSize: 25, fontWeight: 600, margin: 0, letterSpacing: "-0.03em", fontFamily: "var(--font-display)", color: "#000" }}>Overview</h1>
        <span style={{ fontSize: 11, border: "1px solid var(--color-border)", borderRadius: 4, padding: "3px 8px", color: "var(--color-muted)", background: "var(--color-panel)" }}>
          Example data
        </span>
        <span style={{ marginLeft: "auto", display: "flex", gap: 10 }}>
          <select value={range} onChange={(e) => setRange(e.target.value)} className="mc-select" style={{ height: 36 }}>
            <option>Last 24 hours</option>
            <option>Last 7 days</option>
            <option>Last 30 days</option>
          </select>
          <button className="mc-btn mc-btn-primary" onClick={() => navigate({ to: "/runs/orders-daily" })}>
            <Plus size={15} />
            Create workflow
          </button>
        </span>
      </div>

      <div style={{ display: "flex", gap: 20, alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Card style={{ display: "flex", padding: 0, overflow: "hidden", marginBottom: 18 }}>
            {kpis.map((k, i) => (
              <div key={k.label} style={{ flex: 1, minWidth: 0, padding: 12, borderLeft: i === 0 ? "none" : "1px solid var(--color-border)" }}>
                <div style={{ fontSize: 11, color: "var(--color-muted)" }}>{k.label}</div>
                <div style={{ fontSize: 17, fontWeight: 600, margin: "4px 0", whiteSpace: "nowrap", letterSpacing: "-0.02em", fontFamily: "var(--font-display)" }}>{k.value}</div>
                <div style={{ fontSize: 10, color: "#525252", whiteSpace: "nowrap" }}>{k.sub}</div>
              </div>
            ))}
          </Card>

          <SectionHead title="Needs attention" right={<span>3 priority items · ranked by consumer impact</span>} />
          <div
            style={{
              border: "1px solid var(--color-border)",
              borderRadius: 7,
              overflow: "hidden",
              background: "#F7F5FF",
              marginBottom: 18,
            }}
          >
            {needsAttention.map((a, i) => (
              <AlertRow
                key={a.title}
                icon={ATT_ICON[a.icon] ?? Clock}
                title={a.title}
                sub={a.sub}
                action={a.action}
                to={a.to}
                first={i === 0}
              />
            ))}
          </div>

          <SectionHead title="Active execution" right={<span>4 running · 2 queued</span>} />
          <Card style={{ padding: 0, overflow: "hidden", marginBottom: 18 }}>
            <table className="mc-table">
              <thead>
                <tr>
                  <th>Workflow</th>
                  <th>Stage</th>
                  <th>Progress</th>
                  <th>Elapsed</th>
                  <th style={{ width: 30 }} />
                </tr>
              </thead>
              <tbody>
                {activeExecution.map((r) => (
                  <tr key={r.wf} className="clickable" onClick={() => navigate({ to: r.to })}>
                    <td style={{ fontSize: 12, fontWeight: 500 }}>{r.wf}</td>
                    <td style={{ color: "var(--color-accent-dark)", fontSize: 11 }}>{r.stage}</td>
                    <td style={{ color: "#525252", fontSize: 11 }}>{r.progress}</td>
                    <td style={{ color: "var(--color-muted)", fontSize: 11 }}>{r.elapsed}</td>
                    <td>
                      <ChevronRight size={15} color="var(--color-muted)" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>

          <SectionHead
            title="Data products"
            right={
              <span>
                Showing 5 of 128 ·{" "}
                <span style={{ color: "var(--color-accent-dark)", fontWeight: 600, cursor: "pointer" }} onClick={() => navigate({ to: "/datasets/orders" })}>
                  View all datasets
                </span>
              </span>
            }
          />
          <Card style={{ padding: 0, overflow: "hidden" }}>
            <table className="mc-table">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Freshness</th>
                  <th>Quality</th>
                  <th>Last released</th>
                  <th>Consumers</th>
                  <th style={{ width: 30 }} />
                </tr>
              </thead>
              <tbody>
                {dataProducts.map((d) => (
                  <tr key={d.name} className="clickable" onClick={() => navigate({ to: d.to })}>
                    <td style={{ fontWeight: 600 }}>{d.name}</td>
                    <td>
                      <Fresh v={d.freshness} />
                    </td>
                    <td>
                      <Qual v={d.quality} />
                    </td>
                    <td style={{ color: "var(--color-muted)", fontFamily: "var(--font-mono)", fontSize: 12.5 }}>{d.released}</td>
                    <td style={{ color: "var(--color-muted)" }}>{d.consumers}</td>
                    <td>
                      <ChevronRight size={15} color="var(--color-muted)" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        </div>

        <div style={{ width: 320, flexShrink: 0, display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <SectionHead title="Service health" right={<span>11 / 12 ready</span>} />
            <Card style={{ padding: "6px 14px" }}>
              {visibleServices.map((s) => (
                <div key={s.name} style={{ display: "flex", alignItems: "center", gap: 8, padding: "7px 0", fontSize: 13, borderTop: "1px solid var(--color-border)" }}>
                  <span style={{ fontWeight: 600, width: 96 }}>{s.name}</span>
                  <span style={{ color: "var(--color-muted)", fontSize: 12.5 }}>{s.role}</span>
                  <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6, fontSize: 12.5, color: s.state === "Ready" ? "var(--color-green)" : "var(--color-orange)" }}>
                    <span style={{ width: 7, height: 7, borderRadius: 999, background: s.state === "Ready" ? "var(--color-green)" : "var(--color-orange)" }} />
                    {s.state}
                  </span>
                </div>
              ))}
              <div style={{ display: "flex", padding: "8px 0 4px", fontSize: 12.5, color: "var(--color-muted)" }}>
                <span>6 more services ready</span>
                <span
                  style={{ marginLeft: "auto", color: "var(--color-accent-dark)", fontWeight: 600, cursor: "pointer" }}
                  onClick={() => (showAllServices ? navigate({ to: "/platform" }) : setShowAllServices(true))}
                >
                  {showAllServices ? "View all services" : "Show all"}
                </span>
              </div>
            </Card>
          </div>

          <div>
            <SectionHead title="Release queue" right={<span>2 pending</span>} />
            <Card style={{ padding: "4px 14px" }}>
              {releaseQueue.map((r, i) => (
                <div
                  key={r.name}
                  onClick={() => navigate({ to: "/releases" })}
                  style={{ padding: "9px 0", borderTop: i === 0 ? "none" : "1px solid var(--color-border)", cursor: "pointer" }}
                >
                  <div style={{ display: "flex", fontSize: 13 }}>
                    <strong style={{ fontFamily: "var(--font-mono)", fontSize: 12.5 }}>{r.name}</strong>
                    <span style={{ marginLeft: "auto" }}>
                      <StatusPill status={r.state} />
                    </span>
                  </div>
                  <div style={{ fontSize: 12, color: "var(--color-muted)", marginTop: 2 }}>{r.sub}</div>
                </div>
              ))}
            </Card>
          </div>

          <div>
            <SectionHead
              title="Governance"
              right={
                <span style={{ color: "var(--color-accent-dark)", fontWeight: 600, cursor: "pointer" }} onClick={() => navigate({ to: "/governance" })}>
                  View controls
                </span>
              }
            />
            <Card style={{ padding: "4px 14px", fontSize: 13 }}>
              <GovRow k="Dataset ownership" v="124 / 128 assigned" c="var(--color-orange)" to="/governance" />
              <GovRow k="Access policies" v="In sync" c="var(--color-green)" to="/governance" />
              <GovRow k="Publication reviews" v="2 awaiting review" c="var(--color-accent-dark)" to="/governance" />
            </Card>
          </div>

          <div>
            <SectionHead
              title="Recovery & maintenance"
              right={
                <span style={{ color: "var(--color-accent-dark)", fontWeight: 600, cursor: "pointer" }} onClick={() => navigate({ to: "/platform" })}>
                  View operations
                </span>
              }
            />
            <Card style={{ padding: "4px 14px", fontSize: 13 }}>
              <GovRow k="Last backup" v="06:00 · Verified" c="var(--color-green)" to="/platform" />
              <GovRow k="Restore rehearsal" v="3 days ago · Passed" c="var(--color-green)" to="/platform" />
              <GovRow k="Table maintenance" v="2 optimizations due" c="var(--color-orange)" to="/platform" />
            </Card>
          </div>
        </div>
      </div>
    </div>
  );
}

function AlertRow({ icon: Icon, title, sub, action, to, first }: { icon: any; title: string; sub: string; action: string; to: string; first: boolean }) {
  const navigate = useNavigate();
  const [hover, setHover] = React.useState(false);
  return (
    <div
      onClick={() => navigate({ to })}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        height: 56,
        padding: "10px 12px",
        borderTop: first ? "none" : "1px solid var(--color-border)",
        background: hover ? "color-mix(in srgb, var(--color-accent) 12%, #F7F5FF)" : "transparent",
        cursor: "pointer",
      }}
    >
      <Icon size={18} color="var(--color-muted)" style={{ flexShrink: 0 }} />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 600 }}>{title}</div>
        <div style={{ fontSize: 11, color: "var(--color-muted)", marginTop: 2 }}>{sub}</div>
      </div>
      <span style={{ display: "flex", alignItems: "center", gap: 3, fontSize: 11, color: "var(--color-accent-dark)", fontWeight: 500, flexShrink: 0 }}>
        {action}
        <ChevronRight size={15} />
      </span>
    </div>
  );
}

function GovRow({ k, v, c, to }: { k: string; v: string; c: string; to: string }) {
  const navigate = useNavigate();
  return (
    <div onClick={() => navigate({ to })} style={{ display: "flex", padding: "8px 0", borderTop: "1px solid var(--color-border)", cursor: "pointer" }}>
      <span>{k}</span>
      <span style={{ marginLeft: "auto", color: c, fontSize: 12.5, textAlign: "right" }}>{v}</span>
    </div>
  );
}

function Fresh({ v }: { v: string }) {
  const c = v === "Fresh" ? "var(--color-green)" : "var(--color-orange)";
  return <span style={{ color: c }}>{v}</span>;
}

function Qual({ v }: { v: string }) {
  const c = v === "Passed" ? "var(--color-green)" : v === "Unknown" ? "var(--color-muted)" : /fail/.test(v) ? "var(--color-danger)" : "var(--color-orange)";
  return <span style={{ color: c }}>{v}</span>;
}

export const Route = createFileRoute("/")({ component: Overview });
