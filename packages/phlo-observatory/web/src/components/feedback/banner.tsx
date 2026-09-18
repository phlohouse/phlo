/**
 * Inline feedback banner: the tinted callout used for degraded providers,
 * blocked deliveries and publication warnings.
 */
import * as React from "react";
import { AlertCircle, Info, TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

export type BannerTone = "danger" | "warning" | "info";

const TONE: Record<BannerTone, { surface: string; icon: string; Icon: typeof Info }> = {
  danger: {
    surface: "border-destructive-border bg-destructive-surface",
    icon: "text-destructive",
    Icon: AlertCircle,
  },
  warning: { surface: "border-warning/25 bg-warning-surface", icon: "text-warning", Icon: TriangleAlert },
  info: { surface: "border-border bg-subtle", icon: "text-muted-foreground", Icon: Info },
};

export function Banner({
  tone = "info",
  title,
  detail,
  action,
  icon,
  className,
}: {
  tone?: BannerTone;
  title: React.ReactNode;
  detail?: React.ReactNode;
  action?: React.ReactNode;
  /** Override the default tone icon. */
  icon?: React.ReactNode;
  className?: string;
}) {
  const config = TONE[tone];
  const Icon = config.Icon;
  return (
    <div
      className={cn(
        "flex items-start gap-2.5 rounded-[7px] border px-3.5 py-3",
        config.surface,
        className,
      )}
    >
      {icon ?? <Icon className={cn("mt-0.5 size-4.5 shrink-0", config.icon)} strokeWidth={1.6} />}
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <span className="text-xs leading-4 font-semibold">{title}</span>
        {detail ? (
          <span className="text-[11px] leading-4 text-[#525252] dark:text-muted-foreground">
            {detail}
          </span>
        ) : null}
      </div>
      {action ? <div className="shrink-0 self-center">{action}</div> : null}
    </div>
  );
}
