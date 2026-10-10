/** Defines the gallery route for reviewing application states. */
import { createFileRoute } from '@tanstack/react-router'
import { PageBody, PageHeader } from '@/components/phlo/page'
import {
  CatalogDown,
  FirstLoad,
  JobRunning,
  NotAllowed,
  NothingFound,
  QueryFailed,
  StateFrame,
} from '@/components/states/gallery'

export const Route = createFileRoute('/_app/states')({
  head: () => ({ meta: [{ title: 'Harder states · phlo' }] }),
  component: StatesPage,
})

function StatesPage() {
  return (
    <>
      <PageHeader
        title="Harder states"
        meta="How each part of the app behaves when it’s busy, broken, empty or not allowed"
      />
      <PageBody className="gap-6 lg:p-8">
        <div className="flex flex-col gap-1">
          <h2 className="m-0 text-xl font-semibold tracking-tight lg:text-2xl">
            When things aren’t normal
          </h2>
          <p className="m-0 text-sm text-muted-foreground md:hidden">
            How each part of the app behaves when it’s busy, broken, empty or
            not allowed
          </p>
        </div>
        <div className="grid grid-cols-1 gap-x-6 gap-y-7 md:grid-cols-2 xl:grid-cols-3">
          <StateFrame n={1} title="Job running" where="Jobs › run detail">
            <JobRunning />
          </StateFrame>
          <StateFrame n={2} title="Query failed" where="Query › editor">
            <QueryFailed />
          </StateFrame>
          <StateFrame n={3} title="Nothing found" where="Assets › search">
            <NothingFound />
          </StateFrame>
          <StateFrame n={4} title="Not allowed" where="Branches › merge">
            <NotAllowed />
          </StateFrame>
          <StateFrame n={5} title="Catalog unreachable" where="Every screen">
            <CatalogDown />
          </StateFrame>
          <StateFrame n={6} title="First load" where="Overview">
            <FirstLoad />
          </StateFrame>
        </div>
      </PageBody>
    </>
  )
}
