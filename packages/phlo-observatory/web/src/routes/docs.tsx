/**
 * Documentation route: renders the Mission Control state-model guide from content data.
 */
import { createFileRoute } from "@tanstack/react-router";

import { ExampleDataChip, PageHeader } from "@/components/page-header";
import { Button } from "@/components/ui/button";
import { documentationGroups } from "@/content/documentation";
import { cn } from "@/lib/utils";

/**
 * "How to read Mission Control" reference: grouped explainer cards covering the
 * state model, data kinds, service distinctions and safe operations.
 */
function DocumentationPage() {
  return (
    <div className="flex flex-col">
      <div className="px-6">
        <PageHeader
          title="Documentation"
          titleAccessory={<ExampleDataChip />}
          description="How to read Mission Control — the state model, statuses, and what you can safely do"
          actions={
            <>
              <Button variant="outline">View configuration</Button>
              <Button>Run diagnostics</Button>
            </>
          }
        />
      </div>

      <div className="flex w-290 max-w-full flex-col gap-5 px-6 pt-4.5 pb-5">
        {documentationGroups.map((group) => (
          <section key={group.title} className="flex flex-col gap-2.5">
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
                  <h3
                    className={cn(
                      "text-[13px] leading-4.5 font-semibold text-foreground",
                      card.tone,
                    )}
                  >
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
        ))}
      </div>
    </div>
  );
}

export const Route = createFileRoute("/docs")({ component: DocumentationPage });
