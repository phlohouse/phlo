/**
 * Status Pill component.
 */
import type {StatusTone} from "@/lib/status";
import { Badge } from "@/components/ui/badge";
import {  statusTone, toneToBadgeVariant } from "@/lib/status";
import { cn } from "@/lib/utils";

const DOT: Record<StatusTone, string> = {
  success: "bg-success",
  warning: "bg-warning",
  danger: "bg-destructive",
  accent: "bg-primary",
  muted: "bg-muted-foreground",
};

/**
 * Tinted status chip. Pass `status` to derive the tone from backend text, or
 * `tone` to pin it explicitly.
 */
export function StatusPill({
  status,
  tone,
  dot = true,
  className,
  children,
}: {
  status?: string;
  tone?: StatusTone;
  dot?: boolean;
  className?: string;
  children?: React.ReactNode;
}) {
  const resolved = tone ?? (status ? statusTone(status) : "muted");
  return (
    <Badge variant={toneToBadgeVariant[resolved]} className={className}>
      {dot ? <span className={cn("size-1.5 shrink-0 rounded-full", DOT[resolved])} /> : null}
      {children ?? status}
    </Badge>
  );
}
