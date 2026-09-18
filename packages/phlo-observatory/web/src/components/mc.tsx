/**
 * Shared Mission Control primitives: status pills, avatars, cards, tabs, chips, pager.
 */
import * as React from "react";

export type Theme = "light" | "dark";

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = React.useState<Theme>(() => {
    if (typeof window === "undefined") return "light";
    return (window.localStorage.getItem("phlo-theme") as Theme) || "light";
  });
  React.useEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("phlo-theme", theme);
  }, [theme]);
  return [theme, () => setTheme((t) => (t === "light" ? "dark" : "light"))];
}

export function statusTone(s: string): { bg: string; fg: string; dot?: string } {
  const t = s.toLowerCase();
  if (/fail|blocked|danger|error/.test(t)) return { bg: "color-mix(in srgb, var(--color-danger) 12%, transparent)", fg: "var(--color-danger)", dot: "var(--color-danger)" };
  if (/warn|late|delayed|degrad|expir|unknown outcome/.test(t)) return { bg: "color-mix(in srgb, var(--color-orange) 14%, transparent)", fg: "var(--color-orange)", dot: "var(--color-orange)" };
  if (/pass|ready|fresh|verif|synced|in sync|assigned|complet|success|health|active|schedul|publish/.test(t))
    return { bg: "color-mix(in srgb, var(--color-green) 12%, transparent)", fg: "var(--color-green)", dot: "var(--color-green)" };
  if (/review|await|pend|unknown|queued|reconcil/.test(t)) return { bg: "var(--color-accent-soft)", fg: "var(--color-accent-dark)", dot: "var(--color-accent)" };
  return { bg: "color-mix(in srgb, var(--color-muted) 12%, transparent)", fg: "var(--color-muted)" };
}

export function StatusPill({ status, style }: { status: string; style?: React.CSSProperties }) {
  const c = statusTone(status);
  return (
    <span className="mc-pill" style={{ background: c.bg, color: c.fg, ...style }}>
      {c.dot && <span style={{ width: 6, height: 6, borderRadius: 999, background: c.dot, display: "inline-block" }} />}
      {status}
    </span>
  );
}

export function Avatar({ name, size = 28 }: { name: string; size?: number }) {
  const initials = name
    .split(/[\s.]+/)
    .filter(Boolean)
    .map((w) => w[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
  return (
    <span
      style={{
        width: size,
        height: size,
        borderRadius: 999,
        background: "var(--color-accent-soft)",
        color: "var(--color-accent-dark)",
        display: "inline-flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: Math.round(size * 0.36),
        fontWeight: 700,
        flexShrink: 0,
      }}
    >
      {initials}
    </span>
  );
}

export function Card({ children, style }: { children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="mc-card" style={style}>
      {children}
    </div>
  );
}

export function SectionHead({ title, right }: { title: string; right?: React.ReactNode }) {
  return (
    <div style={{ display: "flex", alignItems: "baseline", marginBottom: 10 }}>
      <h2 style={{ fontSize: 15, fontWeight: 600, margin: 0, fontFamily: "var(--font-display)" }}>{title}</h2>
      <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 10, fontSize: 11, color: "var(--color-muted)" }}>{right}</span>
    </div>
  );
}

export function Seg<T extends string>({ options, value, onChange }: { options: readonly T[]; value: T; onChange: (v: T) => void }) {
  return (
    <span style={{ display: "inline-flex", border: "1px solid var(--color-border)", borderRadius: 8, background: "var(--color-panel)", padding: 2, gap: 2 }}>
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          style={{
            border: "1px solid transparent",
            background: value === o ? "var(--color-accent-soft)" : "transparent",
            borderRadius: 6,
            padding: "4px 12px",
            fontSize: 13,
            fontWeight: value === o ? 650 : 400,
            color: value === o ? "var(--color-accent-dark)" : "var(--color-muted)",
            cursor: "pointer",
          }}
        >
          {o}
        </button>
      ))}
    </span>
  );
}

export function TabsUnderline<T extends string>({
  options,
  value,
  onChange,
}: {
  options: readonly T[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 4, borderBottom: "1px solid var(--color-border)" }}>
      {options.map((o) => (
        <button
          key={o}
          onClick={() => onChange(o)}
          style={{
            background: "none",
            border: "none",
            borderBottom: value === o ? "2px solid var(--color-accent)" : "2px solid transparent",
            color: value === o ? "var(--color-accent-dark)" : "var(--color-muted)",
            fontWeight: value === o ? 650 : 400,
            fontSize: 13.5,
            padding: "8px 12px",
            cursor: "pointer",
          }}
        >
          {o}
        </button>
      ))}
    </div>
  );
}

export function Chip({ children, onRemove }: { children: React.ReactNode; onRemove?: () => void }) {
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 6,
        fontSize: 12.5,
        border: "1px solid var(--color-border)",
        background: "var(--color-accent-soft)",
        color: "var(--color-accent-dark)",
        borderRadius: 7,
        padding: "5px 10px",
        fontWeight: 550,
      }}
    >
      {children}
      {onRemove && (
        <button onClick={onRemove} style={{ border: "none", background: "none", cursor: "pointer", color: "inherit", fontSize: 13, padding: 0 }}>
          ×
        </button>
      )}
    </span>
  );
}

export function Pager({
  total,
  page,
  setPage,
  per,
  setPer,
  label,
}: {
  total: number;
  page: number;
  setPage: (p: number) => void;
  per: number;
  setPer: (n: number) => void;
  label: string;
}) {
  const pages = Math.max(1, Math.ceil(total / per));
  const btn = {
    minWidth: 28,
    height: 28,
    borderRadius: 7,
    border: "1px solid var(--color-border)",
    background: "var(--color-panel)",
    color: "var(--color-text)",
    cursor: "pointer",
    fontSize: 13,
  } as const;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 13, color: "var(--color-muted)" }}>
      <span>{label}</span>
      <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
        Rows
        <select
          value={per}
          onChange={(e) => {
            setPer(Number(e.target.value));
            setPage(0);
          }}
          className="mc-select"
          style={{ height: 28 }}
        >
          {[8, 25, 50].map((o) => (
            <option key={o}>{o}</option>
          ))}
        </select>
        Page {page + 1} of {pages}
        <button onClick={() => setPage(Math.max(0, page - 1))} disabled={page === 0} style={btn} aria-label="Previous page">
          ‹
        </button>
        <button onClick={() => setPage(Math.min(pages - 1, page + 1))} disabled={page >= pages - 1} style={btn} aria-label="Next page">
          ›
        </button>
      </span>
    </div>
  );
}
