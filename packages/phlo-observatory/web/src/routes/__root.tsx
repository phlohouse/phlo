/**
 * Root shell: sidebar nav, topbar, command palette, alerts inbox, theme toggle, status bar.
 */
import {
  HeadContent,
  Link,
  Outlet,
  Scripts,
  createRootRoute,
  useLocation,
  useNavigate,
} from "@tanstack/react-router";
import {
  Bell,
  BookOpen,
  Boxes,
  ChevronDown,
  Database,
  GitBranch,
  LayoutGrid,
  Moon,
  Play,
  Search,
  Settings,
  ShieldCheck,
  Sun,
} from "lucide-react";
import * as React from "react";

import appCss from "../styles.css?url";
import { useTheme } from "../lib/theme";
import { alerts, envs, searchIndex } from "../data/demo";

const NAV: { to: string; label: string; icon: any; badge?: number }[] = [
  { to: "/", label: "Overview", icon: LayoutGrid },
  { to: "/datasets/orders", label: "Data", icon: Database },
  { to: "/runs/orders-daily", label: "Runs", icon: Play, badge: 4 },
  { to: "/releases", label: "Releases", icon: GitBranch, badge: 2 },
  { to: "/platform", label: "Platform", icon: Boxes },
  { to: "/governance", label: "Governance", icon: ShieldCheck },
  { to: "/docs", label: "Documentation", icon: BookOpen },
  { to: "/settings", label: "Settings", icon: Settings },
];

function crumbs(pathname: string): { label: string; to?: string }[] {
  if (pathname === "/") return [{ label: "Overview" }];
  if (pathname.startsWith("/runs")) return [{ label: "Runs", to: "/runs/orders-daily" }, { label: "Orders daily" }];
  if (pathname.startsWith("/datasets")) return [{ label: "Data", to: "/datasets/orders" }, { label: "Orders" }];
  if (pathname.startsWith("/releases")) return [{ label: "Releases" }];
  if (pathname.startsWith("/platform")) return [{ label: "Platform" }];
  if (pathname.startsWith("/governance")) return [{ label: "Governance" }];
  if (pathname.startsWith("/settings")) return [{ label: "Settings" }];
  if (pathname.startsWith("/docs")) return [{ label: "Documentation" }];
  if (pathname.startsWith("/reference")) return [{ label: "Reference" }];
  return [{ label: pathname.slice(1) }];
}

const SEV_DOT: Record<string, string> = {
  danger: "var(--color-danger)",
  accent: "var(--color-accent)",
  muted: "var(--color-muted)",
};

function AlertsInbox({ onClose }: { onClose: () => void }) {
  return (
    <div
      onClick={onClose}
      style={{ position: "fixed", inset: 0, zIndex: 60 }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          position: "absolute",
          top: 52,
          right: 16,
          width: 400,
          maxWidth: "92vw",
          background: "var(--color-panel)",
          border: "1px solid var(--color-border)",
          borderRadius: 12,
          boxShadow: "0 24px 64px rgba(0,0,0,0.22)",
          overflow: "hidden",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", padding: "12px 16px", borderBottom: "1px solid var(--color-border)" }}>
          <strong style={{ fontSize: 14 }}>Alerts · 4 new</strong>
          <span style={{ marginLeft: "auto", fontSize: 13, color: "var(--color-accent-dark)", fontWeight: 600, cursor: "pointer" }}>
            Rules →
          </span>
        </div>
        <div style={{ maxHeight: 380, overflowY: "auto" }}>
          {alerts.map((a) => (
            <div key={a.title} style={{ display: "flex", gap: 10, padding: "11px 16px", borderBottom: "1px solid var(--color-border)" }}>
              <span style={{ width: 8, height: 8, borderRadius: 999, background: SEV_DOT[a.sev], marginTop: 5, flexShrink: 0 }} />
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", gap: 8 }}>
                  <strong style={{ fontSize: 13 }}>{a.title}</strong>
                  <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--color-muted)", flexShrink: 0 }}>{a.time}</span>
                </div>
                <div style={{ fontSize: 12.5, color: "var(--color-muted)", marginTop: 2 }}>{a.sub}</div>
                {a.action && (
                  <div style={{ fontSize: 12.5, color: "var(--color-accent-dark)", fontWeight: 600, marginTop: 3, cursor: "pointer" }}>
                    {a.action}
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
        <div style={{ display: "flex", padding: "10px 16px", fontSize: 12.5, color: "var(--color-muted)" }}>
          Alerts report events — they are not evidence
          <span style={{ marginLeft: "auto", color: "var(--color-accent-dark)", fontWeight: 600, cursor: "pointer" }}>Audit →</span>
        </div>
      </div>
    </div>
  );
}

function CommandPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [q, setQ] = React.useState("");
  const [idx, setIdx] = React.useState(0);
  const navigate = useNavigate();
  const inputRef = React.useRef<HTMLInputElement>(null);

  React.useEffect(() => {
    if (open) {
      setQ("");
      setIdx(0);
      setTimeout(() => inputRef.current?.focus(), 30);
    }
  }, [open ]);

  const pages = NAV.map((l) => ({ kind: "Go to", title: l.label, meta: "Page", to: l.to }));
  const results = React.useMemo(() => {
    const needle = q.trim().toLowerCase();
    const all = [...pages, ...searchIndex.map((r) => ({ ...r, to: r.kind === "Dataset" ? "/datasets/orders" : r.kind === "Run" ? "/runs/orders-daily" : r.kind === "Release" ? "/releases" : r.kind === "Service" ? "/platform" : "/" }))];
    if (!needle) return all.slice(0, 9);
    return all.filter((r) => `${r.kind} ${r.title} ${r.meta}`.toLowerCase().includes(needle)).slice(0, 9);
  }, [q]);

  React.useEffect(() => setIdx(0), [q]);
  if (!open) return null;

  return (
    <div onClick={onClose} style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,0.35)", zIndex: 70, display: "flex", justifyContent: "center", paddingTop: "12vh" }}>
      <div
        onClick={(e) => e.stopPropagation()}
        onKeyDown={(e) => {
          if (e.key === "Escape") onClose();
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setIdx((i) => Math.min(i + 1, results.length - 1));
          }
          if (e.key === "ArrowUp") {
            e.preventDefault();
            setIdx((i) => Math.max(i - 1, 0));
          }
          if (e.key === "Enter" && results[idx]) {
            navigate({ to: results[idx]!.to });
            onClose();
          }
        }}
        style={{ width: 560, maxWidth: "90vw", height: "fit-content", background: "var(--color-panel)", border: "1px solid var(--color-border)", borderRadius: 12, boxShadow: "0 24px 64px rgba(0,0,0,0.25)", overflow: "hidden" }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "12px 14px", borderBottom: "1px solid var(--color-border)" }}>
          <Search size={16} color="var(--color-muted)" />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search Phlo"
            style={{ flex: 1, border: "none", outline: "none", fontSize: 14, background: "transparent", color: "var(--color-text)" }}
          />
          <kbd style={{ fontSize: 11, border: "1px solid var(--color-border)", borderRadius: 4, padding: "1px 6px", color: "var(--color-muted)" }}>
            esc
          </kbd>
        </div>
        <div style={{ maxHeight: 320, overflowY: "auto", padding: 6 }}>
          {results.length === 0 && (
            <div style={{ padding: "16px 12px", fontSize: 13, color: "var(--color-muted)" }}>No matches for “{q}”</div>
          )}
          {results.map((r, i) => (
            <div
              key={`${r.kind}-${r.title}-${i}`}
              onMouseEnter={() => setIdx(i)}
              onClick={() => {
                navigate({ to: r.to });
                onClose();
              }}
              style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 10px", borderRadius: 8, background: i === idx ? "var(--color-accent-soft)" : "transparent", cursor: "pointer", fontSize: 13 }}
            >
              <span style={{ fontSize: 11, color: "var(--color-muted)", width: 64, flexShrink: 0 }}>{r.kind}</span>
              <span style={{ fontWeight: 600 }}>{r.title}</span>
              <span style={{ marginLeft: "auto", fontSize: 12, color: "var(--color-muted)" }}>{r.meta}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function Shell() {
  const loc = useLocation();
  const pathname = loc.pathname;
  const [theme, toggleTheme] = useTheme();
  const [palette, setPalette] = React.useState(false);
  const [inbox, setInbox] = React.useState(false);
  const [envOpen, setEnvOpen] = React.useState(false);
  const [env, setEnv] = React.useState("Production");

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette((v) => !v);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const isActive = (to: string) => (to === "/" ? pathname === "/" : pathname === to || pathname.startsWith(to + "/"));
  const trail = crumbs(pathname);

  return (
    <div style={{ display: "flex", minHeight: "100vh", background: "var(--color-app)", padding: 12, gap: 12 }}>
      <HeadContent />
      <aside
        style={{
          width: "var(--sidebar-width)",
          display: "flex",
          flexDirection: "column",
          flexShrink: 0,
          height: "calc(100vh - 24px)",
          position: "sticky",
          top: 12,
          padding: "12px 4px",
          gap: 24,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "4px 8px" }}>
          <span
            style={{
              width: 30,
              height: 30,
              borderRadius: 8,
              background: "var(--color-accent)",
              color: "#fff",
              display: "inline-flex",
              alignItems: "center",
              justifyContent: "center",
              fontWeight: 800,
              fontSize: 16,
              flexShrink: 0,
            }}
          >
            P
          </span>
          <strong style={{ fontSize: 25, fontWeight: 700, letterSpacing: "-0.04em", fontFamily: "system-ui, sans-serif" }}>phlo</strong>
        </div>
        <nav style={{ display: "flex", flexDirection: "column", gap: 5, padding: "0 4px", flex: 1 }}>
          {NAV.map((it) => {
            const active = isActive(it.to);
            const Icon = it.icon;
            return (
              <Link
                key={it.to + it.label}
                to={it.to}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  height: 39,
                  padding: "0 12px",
                  borderRadius: 7,
                  fontSize: 14,
                  fontFamily: "system-ui, sans-serif",
                  background: active ? "var(--color-accent-soft)" : "transparent",
                  color: active ? "var(--color-accent-dark)" : "#525252",
                  fontWeight: active ? 600 : 400,
                  textDecoration: "none",
                }}
              >
                <Icon size={18} style={{ flexShrink: 0 }} />
                <span style={{ flex: 1 }}>{it.label}</span>
                {it.badge !== undefined && (
                  <span style={{ fontSize: 12, color: "#525252" }}>{it.badge}</span>
                )}
              </Link>
            );
          })}
        </nav>
        <div style={{ padding: "0 12px 4px" }}>
          <div style={{ height: 1, background: "#D3D3D3", marginBottom: 16 }} />
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <span
              style={{
                width: 30,
                height: 30,
                borderRadius: 15,
                background: "#DED9EE",
                color: "#35277F",
                display: "inline-flex",
                alignItems: "center",
                justifyContent: "center",
                fontSize: 11,
                fontWeight: 600,
                flexShrink: 0,
              }}
            >
              GP
            </span>
            <span>
              <span style={{ display: "block", fontSize: 13, fontWeight: 500 }}>Gareth Price</span>
              <span style={{ display: "block", fontSize: 11, color: "var(--color-muted)" }}>Workspace admin</span>
            </span>
          </div>
        </div>
      </aside>

      <div
        style={{
          flex: 1,
          display: "flex",
          flexDirection: "column",
          minWidth: 0,
          background: "var(--color-panel)",
          border: "1px solid var(--color-border)",
          borderRadius: 12,
          overflow: "clip",
        }}
      >
        <header
          style={{
            height: "var(--topbar-height)",
            borderBottom: "1px solid var(--color-border)",
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "0 20px",
            background: "var(--color-panel)",
            position: "sticky",
            top: 0,
            zIndex: 20,
          }}
        >
          <nav style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 13, color: "var(--color-muted)" }}>
            <span>Workspace</span>
            {trail.map((c, i) => (
              <span key={i} style={{ display: "flex", alignItems: "center", gap: 8 }}>
                <span>›</span>
                {c.to ? (
                  <Link to={c.to} style={{ color: "inherit", textDecoration: "none" }}>
                    {c.label}
                  </Link>
                ) : (
                  <span style={{ color: "var(--color-text)", fontWeight: 600 }}>{c.label}</span>
                )}
              </span>
            ))}
          </nav>
          <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10 }}>
            <button
              onClick={() => setPalette(true)}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                width: 250,
                height: 36,
                borderRadius: 8,
                border: "1px solid var(--color-border)",
                background: "var(--color-panel)",
                padding: "0 12px",
                fontSize: 13,
                color: "var(--color-muted)",
                cursor: "pointer",
              }}
            >
              <Search size={14} />
              <span style={{ flex: 1, textAlign: "left" }}>Search Phlo</span>
              <kbd style={{ fontSize: 11, color: "var(--color-muted)" }}>⌘ K</kbd>
            </button>
            <span style={{ position: "relative" }}>
              <button
                onClick={() => setEnvOpen((v) => !v)}
                style={{ display: "flex", alignItems: "center", gap: 7, background: "none", border: "none", fontSize: 13, fontWeight: 550, cursor: "pointer", color: "var(--color-text)" }}
              >
                <span style={{ width: 7, height: 7, borderRadius: 999, background: "var(--color-green)", display: "inline-block" }} />
                {env}
                <ChevronDown size={14} color="var(--color-muted)" />
              </button>
              {envOpen && (
                <span style={{ position: "absolute", right: 0, top: 28, background: "var(--color-panel)", border: "1px solid var(--color-border)", borderRadius: 9, boxShadow: "0 12px 32px rgba(0,0,0,0.14)", zIndex: 30, minWidth: 170, padding: 5 }}>
                  {envs.map((e) => (
                    <button
                      key={e}
                      onClick={() => {
                        setEnv(e);
                        setEnvOpen(false);
                      }}
                      style={{
                        display: "flex",
                        width: "100%",
                        alignItems: "center",
                        gap: 8,
                        background: e === env ? "var(--color-accent-soft)" : "none",
                        border: "none",
                        borderRadius: 6,
                        padding: "8px 10px",
                        fontSize: 13,
                        cursor: "pointer",
                        color: "var(--color-text)",
                      }}
                    >
                      <span style={{ width: 7, height: 7, borderRadius: 999, background: e === "Production" ? "var(--color-green)" : e === "Staging" ? "var(--color-orange)" : "var(--color-muted)" }} />
                      {e}
                    </button>
                  ))}
                </span>
              )}
            </span>
            <button
              onClick={() => setInbox((v) => !v)}
              title="Alerts"
              style={{ position: "relative", background: "none", border: "none", cursor: "pointer", color: "var(--color-text)", padding: 6 }}
            >
              <Bell size={17} />
              <span style={{ position: "absolute", top: 2, right: 2, width: 8, height: 8, borderRadius: 999, background: "var(--color-danger)", border: "2px solid var(--color-panel)" }} />
            </button>
            <button
              onClick={toggleTheme}
              title={theme === "light" ? "Dark mode" : "Light mode"}
              style={{ background: "none", border: "none", cursor: "pointer", color: "var(--color-text)", padding: 6 }}
            >
              {theme === "light" ? <Moon size={17} /> : <Sun size={17} />}
            </button>
          </div>
        </header>
        <main style={{ padding: "20px 24px 16px", flex: 1, minWidth: 0 }}>
          <Outlet />
        </main>
        <footer
          style={{
            display: "flex",
            padding: "10px 24px",
            fontSize: 12,
            color: "var(--color-muted)",
            borderTop: "1px solid var(--color-border)",
            background: "var(--color-panel)",
          }}
        >
          <span>Updated 09:35:12 UTC · Auto-refresh 15s</span>
          <span style={{ marginLeft: "auto" }}>{env} · Retail analytics · Example data</span>
        </footer>
      </div>
      {inbox && <AlertsInbox onClose={() => setInbox(false)} />}
      <CommandPalette open={palette} onClose={() => setPalette(false)} />
      <Scripts />
    </div>
  );
}

export const Route = createRootRoute({
  head: () => ({
    meta: [
      { charSet: "utf-8" },
      { name: "viewport", content: "width=device-width, initial-scale=1" },
      { title: "Phlo Mission Control" },
    ],
    links: [
      { rel: "stylesheet", href: appCss },
      { rel: "preconnect", href: "https://fonts.googleapis.com" },
      { rel: "stylesheet", href: "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" },
    ],
  }),
  component: Shell,
});
