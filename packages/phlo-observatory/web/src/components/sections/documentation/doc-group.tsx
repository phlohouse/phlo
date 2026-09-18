/**
 * Documentation group: a titled set of explainer cards.
 */
import type { DocGroup } from "@/content/documentation";
import { cn } from "@/lib/utils";

export function DocGroupSection({ group }: { group: DocGroup }) {
  return (
    <section className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-baseline gap-2.5">
        <h2 className="text-sm leading-5 font-semibold text-foreground">{group.title}</h2>
        {group.intro ? (
          <p className="text-xs leading-4 text-muted-foreground">{group.intro}</p>
        ) : null}
      </div>
      <div className="flex gap-3.5">
        {group.cards.map((card) => (
          <div
            key={card.title}
            className="flex flex-1 flex-col gap-1.5 rounded-lg border border-border bg-card p-3.5"
          >
            <h3 className={cn("text-[13px] leading-4.5 font-semibold text-foreground", card.tone)}>
              {card.title}
            </h3>
            <p className="text-xs leading-4.25 text-[#525252] dark:text-muted-foreground">
              {card.body}
            </p>
            {card.seen ? (
              <p className="text-[11px] leading-3.75 text-muted-foreground">{card.seen}</p>
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
