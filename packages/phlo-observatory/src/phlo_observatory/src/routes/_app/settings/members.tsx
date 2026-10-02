/** Defines membership, invitation, and service-account administration. */
import * as React from 'react'
import { createFileRoute, useRouter } from '@tanstack/react-router'
import { PlusIcon } from 'lucide-react'
import type {
  AdminMember,
  AdminRole,
  AdminServiceAccount,
} from '@/lib/data/api/admin'
import {
  changeMemberRoles,
  createInvitation,
  createServiceAccount,
  getAdminIdentity,
  revokeServiceAccount,
} from '@/lib/data/api/admin'
import { Eyebrow, PageHeader } from '@/components/phlo/page'
import { Mono } from '@/components/phlo/status'
import { SettingsFrame } from '@/components/settings/frame'
import { Avatar } from '@/components/ui/avatar'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { CheckLine } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input, Textarea } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/menu'
import { Select } from '@/components/ui/select'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/settings/members')({
  loader: () => getAdminIdentity(),
  head: () => ({ meta: [{ title: 'Members and access · phlo' }] }),
  component: MembersPage,
})

const roles: Array<AdminRole> = [
  'admin',
  'operator',
  'developer',
  'analyst',
  'viewer',
  'service',
]
const roleOptions = roles.map((role) => ({
  value: role,
  label: role[0].toUpperCase() + role.slice(1),
}))
const peopleGrid =
  '@[640px]:grid @[640px]:grid-cols-[minmax(0,1.6fr)_112px_180px_84px_110px] @[640px]:items-center @[640px]:gap-x-3.5'
const serviceGrid =
  'md:grid md:grid-cols-[minmax(0,1.2fr)_minmax(0,1.4fr)_100px_90px] md:items-center md:gap-x-3.5'

function MembersPage() {
  const data = Route.useLoaderData()
  const router = useRouter()
  const refresh = () => router.invalidate()
  React.useEffect(() => {
    if (window.location.hash === '#service-accounts')
      document.getElementById('service-accounts')?.scrollIntoView()
  }, [])

  return (
    <SettingsFrame
      header={
        <PageHeader
          crumbs={[{ label: 'Settings', to: '/settings' }]}
          title="Members and access"
          actions={<InviteButton onDone={refresh} />}
        />
      }
    >
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto xl:flex-row xl:overflow-hidden">
        <section className="flex min-w-0 flex-1 flex-col gap-[26px] p-4 xl:overflow-y-auto lg:px-6 lg:py-5">
          <div className="flex flex-col">
            <div className="flex items-baseline gap-2.5 pb-2">
              <h2 className="m-0 text-[15px] font-medium">People</h2>
              <span className="text-[13px] text-muted-foreground">
                {data.members.filter((member) => member.active).length} active ·{' '}
                {
                  data.invitations.filter(
                    (invite) => invite.status === 'pending',
                  ).length
                }{' '}
                pending
              </span>
            </div>
            <div
              role="table"
              aria-label="People"
              className="@container flex flex-col"
            >
              <div
                role="row"
                className={cn(
                  peopleGrid,
                  'hidden h-[34px] border-b border-line text-xs text-muted-foreground',
                )}
              >
                <span role="columnheader">Identity</span>
                <span role="columnheader">Type</span>
                <span role="columnheader">Roles</span>
                <span role="columnheader">Status</span>
                <span role="columnheader">Updated</span>
              </div>
              {data.members.map((member) => (
                <MemberRow
                  key={member.subject}
                  member={member}
                  onDone={refresh}
                />
              ))}
              {data.invitations
                .filter((invite) => invite.status === 'pending')
                .map((invite) => (
                  <div
                    key={invite.invitation_id}
                    role="row"
                    className={cn(
                      peopleGrid,
                      'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-line-soft py-3 text-[13.5px] @[640px]:min-h-[50px] @[640px]:py-2',
                    )}
                  >
                    <span
                      role="cell"
                      className="flex min-w-0 items-center gap-2.5"
                    >
                      <Avatar
                        initials="··"
                        size="md"
                        className="bg-soft text-muted-foreground"
                      />
                      <span className="break-all">{invite.email}</span>
                    </span>
                    <span
                      role="cell"
                      className="hidden text-text-2 @[640px]:block"
                    >
                      Invitation
                    </span>
                    <span role="cell" className="text-text-2">
                      {invite.roles.join(', ')}
                    </span>
                    <span role="cell">
                      <Badge variant="warn">Pending</Badge>
                    </span>
                    <span
                      role="cell"
                      className="hidden text-xs text-muted-foreground @[640px]:block"
                    >
                      Expires {formatDate(invite.expires_at)}
                    </span>
                  </div>
                ))}
              {data.members.length === 0 && data.invitations.length === 0 ? (
                <p className="m-0 py-6 text-[13.5px] text-muted-foreground">
                  No members or invitations.
                </p>
              ) : null}
            </div>
          </div>

          <div id="service-accounts" className="flex scroll-mt-4 flex-col">
            <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-2 pb-2">
              <h2 className="m-0 text-[15px] font-medium">Service accounts</h2>
              <span className="text-[13px] text-muted-foreground">
                Machines that access Phlo
              </span>
              <ServiceAccountButton onDone={refresh} />
            </div>
            <div
              role="table"
              aria-label="Service accounts"
              className="flex flex-col"
            >
              <div
                role="row"
                className={cn(
                  serviceGrid,
                  'hidden h-[34px] border-b border-line text-xs text-muted-foreground',
                )}
              >
                <span role="columnheader">Account</span>
                <span role="columnheader">Roles</span>
                <span role="columnheader">Status</span>
                <span role="columnheader">Actions</span>
              </div>
              {data.serviceAccounts.map((account, index) => (
                <div
                  key={account.subject}
                  role="row"
                  className={cn(
                    serviceGrid,
                    'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 py-3 text-[13.5px] md:min-h-[50px] md:py-1',
                    index < data.serviceAccounts.length - 1 &&
                      'border-b border-line-soft',
                  )}
                >
                  <span role="cell" className="min-w-0">
                    <Mono className="text-[13px] break-all">
                      {account.name}
                    </Mono>
                    <span className="block break-all text-xs text-muted-foreground">
                      {account.subject}
                    </span>
                  </span>
                  <span role="cell" className="text-text-2">
                    {account.roles.join(', ')}
                  </span>
                  <span role="cell">
                    <Badge variant={account.active ? 'ok' : 'neutral'}>
                      {account.active ? 'Active' : 'Revoked'}
                    </Badge>
                  </span>
                  <span role="cell">
                    {account.active ? (
                      <RevokeServiceAccount
                        account={account}
                        onDone={refresh}
                      />
                    ) : null}
                  </span>
                </div>
              ))}
              {data.serviceAccounts.length === 0 ? (
                <p className="m-0 py-6 text-[13.5px] text-muted-foreground">
                  No service accounts.
                </p>
              ) : null}
            </div>
          </div>
        </section>
        <aside
          aria-label="Roles"
          className="flex shrink-0 flex-col border-t border-line bg-raised px-4 py-5 xl:w-[300px] xl:overflow-y-auto xl:border-t-0 xl:border-l xl:px-[22px]"
        >
          <Eyebrow className="pb-1">Access model</Eyebrow>
          <RoleNote title="Viewer / Analyst">
            Read access for operational and analytical work.
          </RoleNote>
          <RoleNote title="Developer / Operator">
            Build and operate data workflows according to API policy.
          </RoleNote>
          <RoleNote title="Admin" last>
            Manage identities. Every change requires explicit confirmation, a
            justification, and recent MFA.
          </RoleNote>
          <div className="mt-4 rounded-[10px] border border-primary-line bg-primary-soft px-3.5 py-3 text-[12.5px] leading-normal text-primary-ink xl:mt-auto">
            Identity and audit data is installation-wide, independent of the
            selected environment.
          </div>
        </aside>
      </div>
    </SettingsFrame>
  )
}

function MemberRow({
  member,
  onDone,
}: {
  member: AdminMember
  onDone: () => void
}) {
  const [nextRole, setNextRole] = React.useState<AdminRole | null>(null)
  return (
    <div
      role="row"
      className={cn(
        peopleGrid,
        'grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 border-b border-line-soft py-3 text-[13.5px] @[640px]:min-h-[50px] @[640px]:py-2',
      )}
    >
      <span role="cell" className="flex min-w-0 items-center gap-2.5">
        <Avatar
          initials={(member.email ?? member.subject).slice(0, 2).toUpperCase()}
          size="md"
        />
        <span className="min-w-0">
          <span className="block break-all">
            {member.email ?? member.subject}
          </span>
          <span className="block break-all text-xs text-muted-foreground">
            {member.subject}
          </span>
        </span>
      </span>
      <span role="cell" className="hidden text-text-2 @[640px]:block">
        {member.principal_type}
      </span>
      <span role="cell">
        <Select
          aria-label={`Role for ${member.email ?? member.subject}`}
          value={member.roles[0] ?? 'viewer'}
          onValueChange={(role) => setNextRole(role)}
          options={roleOptions.filter((option) => option.value !== 'service')}
          className="h-10 w-[170px] rounded-md px-2 text-[13px] @[640px]:h-7"
        />
      </span>
      <span role="cell">
        <Badge variant={member.active ? 'ok' : 'neutral'}>
          {member.active ? 'Active' : 'Inactive'}
        </Badge>
      </span>
      <span
        role="cell"
        className="hidden text-xs text-muted-foreground @[640px]:block"
      >
        {formatDate(member.updated_at)}
      </span>
      {nextRole && nextRole !== member.roles[0] ? (
        <ConfirmAction
          title={`Change roles for ${member.email ?? member.subject}`}
          submitLabel="Approve change"
          onCancel={() => setNextRole(null)}
          onSubmit={(justification, operationId) =>
            changeMemberRoles({
              data: {
                subject: member.subject,
                expectedVersion: member.version,
                roles: [nextRole],
                email: member.email,
                principalType:
                  member.principal_type === 'platform'
                    ? 'platform'
                    : member.principal_type === 'service'
                      ? 'service'
                      : 'user',
                active: member.active,
                justification,
                confirmed: true,
                operationId,
              },
            }).then(() => {
              setNextRole(null)
              onDone()
            })
          }
        />
      ) : null}
    </div>
  )
}

function InviteButton({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = React.useState(false)
  const [email, setEmail] = React.useState('')
  const [role, setRole] = React.useState<AdminRole>('viewer')
  const [justification, setJustification] = React.useState('')
  const [confirmed, setConfirmed] = React.useState(false)
  const [token, setToken] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const operationId = React.useRef(crypto.randomUUID())
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (busy) return
        setOpen(value)
        if (!value) {
          setToken(null)
          operationId.current = crypto.randomUUID()
        }
      }}
    >
      <PopoverTrigger className={cn(buttonVariants(), 'h-10 lg:h-8')}>
        <PlusIcon /> Invite people
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[min(360px,calc(100vw-2rem))] p-4"
      >
        {token ? (
          <div className="flex flex-col gap-3">
            <p className="m-0 text-sm font-medium">
              Copy the invitation token now
            </p>
            <p className="m-0 text-xs text-muted-foreground">
              Send it securely to the invitee. It will not be shown again.
            </p>
            <Input
              readOnly
              value={token}
              aria-label="Invitation token"
              onFocus={(event) => event.currentTarget.select()}
            />
            <Button onClick={() => setOpen(false)}>Done</Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={async (event) => {
              event.preventDefault()
              if (busy) return
              setBusy(true)
              setError(null)
              try {
                const result = await createInvitation({
                  data: {
                    email,
                    roles: [role],
                    justification,
                    confirmed: true,
                    operationId: operationId.current,
                  },
                })
                setToken(result.token)
                setEmail('')
                setJustification('')
                setConfirmed(false)
                onDone()
              } catch (cause) {
                setError(message(cause))
              } finally {
                setBusy(false)
              }
            }}
          >
            <Field>
              <FieldLabel>Email</FieldLabel>
              <Input
                type="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
              />
            </Field>
            <label className="text-[13.5px] font-medium">
              Role
              <Select
                value={role}
                onValueChange={setRole}
                options={roleOptions.filter(
                  (option) => option.value !== 'service',
                )}
              />
            </label>
            <ApprovalFields
              justification={justification}
              setJustification={setJustification}
              confirmed={confirmed}
              setConfirmed={setConfirmed}
            />
            {error ? <ErrorText text={error} /> : null}
            <Button
              type="submit"
              disabled={busy || !confirmed || !justification.trim()}
              className="self-end"
            >
              {busy ? 'Creating…' : 'Create signed invite'}
            </Button>
          </form>
        )}
      </PopoverContent>
    </Popover>
  )
}

function ServiceAccountButton({ onDone }: { onDone: () => void }) {
  const [open, setOpen] = React.useState(false)
  const [name, setName] = React.useState('')
  const [role, setRole] = React.useState<AdminRole>('viewer')
  const [justification, setJustification] = React.useState('')
  const [confirmed, setConfirmed] = React.useState(false)
  const [token, setToken] = React.useState<string | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const operationId = React.useRef(crypto.randomUUID())
  return (
    <Popover
      open={open}
      onOpenChange={(value) => {
        if (busy) return
        setOpen(value)
        if (!value) {
          setToken(null)
          operationId.current = crypto.randomUUID()
        }
      }}
    >
      <PopoverTrigger
        className={cn(
          buttonVariants({ variant: 'outline', size: 'sm' }),
          'h-10 sm:ml-auto md:h-7',
        )}
      >
        New service account
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-[min(380px,calc(100vw-2rem))] p-4"
      >
        {token ? (
          <div className="flex flex-col gap-3">
            <p className="m-0 text-sm font-medium">Copy this token now</p>
            <p className="m-0 text-xs text-muted-foreground">
              It will not be shown again.
            </p>
            <Input
              readOnly
              value={token}
              aria-label="New service account token"
              onFocus={(event) => event.currentTarget.select()}
            />
            <Button onClick={() => setOpen(false)}>Done</Button>
          </div>
        ) : (
          <form
            className="flex flex-col gap-3"
            onSubmit={async (event) => {
              event.preventDefault()
              if (busy) return
              setBusy(true)
              setError(null)
              try {
                const result = await createServiceAccount({
                  data: {
                    name,
                    roles: [role],
                    justification,
                    confirmed: true,
                    operationId: operationId.current,
                  },
                })
                setToken(result.token)
                onDone()
              } catch (cause) {
                setError(message(cause))
              } finally {
                setBusy(false)
              }
            }}
          >
            <Field>
              <FieldLabel>Name</FieldLabel>
              <Input
                required
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </Field>
            <label className="text-[13.5px] font-medium">
              Role
              <Select
                value={role}
                onValueChange={setRole}
                options={roleOptions}
              />
            </label>
            <ApprovalFields
              justification={justification}
              setJustification={setJustification}
              confirmed={confirmed}
              setConfirmed={setConfirmed}
            />
            {error ? <ErrorText text={error} /> : null}
            <Button
              type="submit"
              disabled={busy || !confirmed || !justification.trim()}
              className="self-end"
            >
              {busy ? 'Creating…' : 'Create and show token'}
            </Button>
          </form>
        )}
      </PopoverContent>
    </Popover>
  )
}

function RevokeServiceAccount({
  account,
  onDone,
}: {
  account: AdminServiceAccount
  onDone: () => void
}) {
  const [open, setOpen] = React.useState(false)
  return (
    <>
      <Button variant="outline" size="sm" onClick={() => setOpen(true)}>
        Revoke
      </Button>
      {open ? (
        <ConfirmAction
          title={`Revoke ${account.name}`}
          submitLabel="Revoke account"
          onCancel={() => setOpen(false)}
          onSubmit={(justification, operationId) =>
            revokeServiceAccount({
              data: {
                subject: account.subject,
                expectedVersion: account.version,
                justification,
                confirmed: true,
                operationId,
              },
            }).then(() => {
              setOpen(false)
              onDone()
            })
          }
        />
      ) : null}
    </>
  )
}

function ConfirmAction({
  title,
  submitLabel,
  onCancel,
  onSubmit,
}: {
  title: string
  submitLabel: string
  onCancel: () => void
  onSubmit: (justification: string, operationId: string) => Promise<unknown>
}) {
  const [justification, setJustification] = React.useState('')
  const [confirmed, setConfirmed] = React.useState(false)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const operationId = React.useRef(crypto.randomUUID())
  return (
    <Dialog
      open
      onOpenChange={(value) => {
        if (!value && !busy) onCancel()
      }}
    >
      <DialogContent>
        <form
          onSubmit={async (event) => {
            event.preventDefault()
            if (busy) return
            setBusy(true)
            setError(null)
            try {
              await onSubmit(justification, operationId.current)
            } catch (cause) {
              setError(message(cause))
              setBusy(false)
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <ApprovalFields
              justification={justification}
              setJustification={setJustification}
              confirmed={confirmed}
              setConfirmed={setConfirmed}
            />
            {error ? <ErrorText text={error} /> : null}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onCancel}
              disabled={busy}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={busy || !confirmed || !justification.trim()}
            >
              {busy ? 'Working…' : submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function ApprovalFields({
  justification,
  setJustification,
  confirmed,
  setConfirmed,
}: {
  justification: string
  setJustification: (value: string) => void
  confirmed: boolean
  setConfirmed: (value: boolean) => void
}) {
  return (
    <>
      <Field>
        <FieldLabel>Justification</FieldLabel>
        <Textarea
          required
          maxLength={4000}
          value={justification}
          onChange={(event) => setJustification(event.target.value)}
          rows={3}
        />
      </Field>
      <CheckLine
        checked={confirmed}
        onCheckedChange={(value) => setConfirmed(value === true)}
      >
        I confirm this administrative action and understand it will be signed
        and audited.
      </CheckLine>
    </>
  )
}
function RoleNote({
  title,
  children,
  last,
}: {
  title: string
  children: React.ReactNode
  last?: boolean
}) {
  return (
    <div
      className={cn(
        'flex flex-col gap-[3px] py-3',
        !last && 'border-b border-line-soft',
      )}
    >
      <span className="text-[13.5px] font-medium">{title}</span>
      <span className="text-[12.5px] leading-snug text-muted-foreground">
        {children}
      </span>
    </div>
  )
}
function ErrorText({ text }: { text: string }) {
  return (
    <p role="alert" className="m-0 text-[13px] text-bad-ink">
      {text}
    </p>
  )
}
function message(error: unknown) {
  return error instanceof Error
    ? error.message
    : 'The action could not be completed.'
}
function formatDate(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleDateString('en-GB')
}
