/**
 * Documentation route: renders the Mission Control state-model guide.
 */
import { createFileRoute } from "@tanstack/react-router";

import { Page, PageStack } from "@/components/layout/page";
import { PageHeader } from "@/components/layout/page-header";
import { DocGroupSection } from "@/components/sections/documentation/doc-group";
import { documentationGroups } from "@/content/documentation";

function DocumentationPage() {
  return (
    <Page>
      <PageHeader
        title="Documentation"
        description="How to read Mission Control — the state model, statuses, and what you can safely do"
      />
      <PageStack className="w-290 max-w-full gap-5 px-6 pt-4.5 pb-5">
        {documentationGroups.map((group) => (
          <DocGroupSection key={group.title} group={group} />
        ))}
      </PageStack>
    </Page>
  );
}

export const Route = createFileRoute("/docs")({ component: DocumentationPage });
