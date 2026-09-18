/**
 * Reference building blocks: colour grid, status matrix and component cards.
 */
import { Swatch } from "@/components/foundation/swatch";
import { cn } from "@/lib/utils";

export interface ColorToken {
  name: string;
  usage: string;
}

export function ColorTokenGrid({ tokens }: { tokens: Array<ColorToken> }) {
  return (
    <div className="grid grid-cols-3 gap-3.5 xl:grid-cols-4">
      {tokens.map((token) => (
        <Swatch key={token.name} token={token.name} usage={token.usage} />
      ))}
    </div>
  );
}

export function ComponentCardGrid({
  items,
}: {
  items: Array<{ name: string; hint: string; preview: React.ReactNode }>;
}) {
  return (
    <div className="grid grid-cols-3 gap-3.5">
      {items.map((item) => (
        <div
          key={item.name}
          className="flex flex-col gap-2.5 rounded-lg border border-border bg-card p-3.5"
        >
          <div className="flex h-8 items-center">{item.preview}</div>
          <div className="flex flex-col gap-0.5">
            <span className="text-[13px] leading-4.5 font-semibold">{item.name}</span>
            <span className="text-[11px] leading-3.75 text-muted-foreground">{item.hint}</span>
          </div>
        </div>
      ))}
    </div>
  );
}

export function RuleCardGrid({
  items,
  className,
}: {
  items: Array<{ title: string; body: string }>;
  className?: string;
}) {
  return (
    <div className={cn("grid grid-cols-2 gap-3.5", className)}>
      {items.map((item) => (
        <div
          key={item.title}
          className="flex flex-col gap-1.5 rounded-lg border border-border bg-card p-3.5"
        >
          <span className="text-[13px] leading-4.5 font-semibold">{item.title}</span>
          <span className="text-xs leading-4.25 text-muted-foreground">{item.body}</span>
        </div>
      ))}
    </div>
  );
}
