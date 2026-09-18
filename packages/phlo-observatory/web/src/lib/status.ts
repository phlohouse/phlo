/**
 * Status vocabulary for Mission Control.
 *
 * Backend status strings are free-form; `statusTone` maps them onto the four
 * tones the design uses. Ordering matters: failure vocabulary is matched
 * before success so "not started" never reads as positive.
 */
export type StatusTone = "success" | "warning" | "danger" | "accent" | "muted";

const RULES: Array<[RegExp, StatusTone]> = [
  [/fail|blocked|error|danger|reject|invalid|withheld|missed/, "danger"],
  [/late|delay|degrad|expir|warn|overdue|drift|unknown outcome|no provider mutation/, "warning"],
  [
    /pass|ready|fresh|verif|confirm|sync|complete|success|healthy|publish|promot|current|owned|assigned|matched|merged/,
    "success",
  ],
  [/review|await|pending|queued|reconcil|unknown|proposed|draft|not started|not promoted/, "accent"],
];

export function statusTone(status: string): StatusTone {
  const s = status.toLowerCase();
  for (const [pattern, tone] of RULES) {
    if (pattern.test(s)) return tone;
  }
  return "muted";
}

export const toneToBadgeVariant = {
  success: "success",
  warning: "warning",
  danger: "destructive",
  accent: "accent",
  muted: "muted",
} as const;
