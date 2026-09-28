/** Canonical v1 administration settings and caller identity. */
import { createFileRoute } from '@tanstack/react-router'
import { KeyRound, Save, Settings } from 'lucide-react'
import { useEffect, useState } from 'react'

import type {
  AdminSettingValue,
  AdminSettings,
} from '@/observatory/api/adminSettings'
import type { CurrentIdentity } from '@/observatory/api/settingsIdentity'
import {
  getAdminSettings,
  putAdminSettings,
} from '@/observatory/api/adminSettings'
import { getCurrentIdentity } from '@/observatory/api/settingsIdentity'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'

export const Route = createFileRoute('/settings')({ component: SettingsRoute })

type SettingsState =
  | { kind: 'loading' }
  | { kind: 'unavailable'; message: string }
  | { kind: 'ready'; settings: AdminSettings }

export function SettingsRoute() {
  const [settings, setSettings] = useState<SettingsState>({ kind: 'loading' })
  const [identity, setIdentity] = useState<CurrentIdentity | null>(null)
  const [identityError, setIdentityError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    void loadSettings()
    void loadIdentity()
  }, [])

  async function loadSettings() {
    const result = await getAdminSettings()
    setSettings(
      result.data
        ? { kind: 'ready', settings: result.data }
        : {
            kind: 'unavailable',
            message: result.error ?? 'Admin settings are unavailable.',
          },
    )
  }

  async function loadIdentity() {
    const result = await getCurrentIdentity()
    setIdentity(result.data)
    setIdentityError(result.error)
  }

  function changeValue(name: string, value: string) {
    if (settings.kind !== 'ready') return
    setSettings({
      kind: 'ready',
      settings: {
        ...settings.settings,
        values: { ...settings.settings.values, [name]: value },
      },
    })
  }

  async function save() {
    if (settings.kind !== 'ready') return
    setSaving(true)
    setSaveError(null)
    const result = await putAdminSettings({
      data: {
        expectedVersion: settings.settings.version,
        values: settings.settings.values,
      },
    })
    setSaving(false)
    if (result.data) {
      setSettings({ kind: 'ready', settings: result.data })
      return
    }
    setSaveError(result.error ?? 'Admin settings could not be saved.')
  }

  return (
    <ObservatoryPage
      kicker="Settings"
      title="Platform settings"
      description="Durable platform settings and the authenticated caller, supplied by canonical phlo-api v1 contracts."
      action={<Settings className="size-4" />}
    >
      <section className="phlo-observatory-settings-workbench">
        <SettingsPanel icon={<KeyRound className="size-4" />} title="Identity">
          {identity ? (
            <div className="phlo-observatory-detail-list">
              <Detail label="Subject" value={identity.subject} />
              <Detail label="Email" value={identity.email ?? 'not supplied'} />
              <Detail
                label="Roles"
                value={identity.roles.join(', ') || 'none'}
              />
            </div>
          ) : (
            <Unavailable
              message={identityError ?? 'Identity is unavailable.'}
            />
          )}
        </SettingsPanel>

        <SettingsPanel
          icon={<Settings className="size-4" />}
          title="Admin settings"
        >
          {settings.kind === 'loading' && <p>Loading durable settings…</p>}
          {settings.kind === 'unavailable' && (
            <Unavailable message={settings.message} />
          )}
          {settings.kind === 'ready' && (
            <>
              <p>
                Version {settings.settings.version}. Values are managed as the
                flat, credential-free v1 admin-settings contract.
              </p>
              <div className="phlo-observatory-detail-list">
                {Object.entries(settings.settings.values).map(
                  ([name, value]) => (
                    <label
                      className="phlo-observatory-settings-field"
                      key={name}
                    >
                      <span>{name}</span>
                      <input
                        onChange={(event) =>
                          changeValue(name, event.target.value)
                        }
                        value={displayValue(value)}
                      />
                    </label>
                  ),
                )}
                {Object.keys(settings.settings.values).length === 0 && (
                  <p>No durable admin settings are configured.</p>
                )}
              </div>
              <button
                disabled={saving}
                onClick={() => void save()}
                type="button"
              >
                <Save className="size-3.5" />
                {saving ? 'Saving…' : 'Save admin settings'}
              </button>
              {saveError && <Unavailable message={saveError} />}
            </>
          )}
        </SettingsPanel>

        <SettingsPanel
          icon={<Settings className="size-4" />}
          title="Unavailable settings"
        >
          <Unavailable message="Browser preferences, auth-token storage, cache maintenance, runtime capability metadata, and dataset workflow defaults have no matching canonical v1 admin-settings or identity contract. They are intentionally not editable on this screen." />
        </SettingsPanel>
      </section>
    </ObservatoryPage>
  )
}

function displayValue(value: AdminSettingValue): string {
  return value === null ? '' : String(value)
}

function SettingsPanel({
  children,
  icon,
  title,
}: {
  children: React.ReactNode
  icon: React.ReactNode
  title: string
}) {
  return (
    <section className="phlo-observatory-settings-panel">
      <div className="phlo-observatory-callout-title">
        {icon}
        {title}
      </div>
      <div className="phlo-observatory-settings-panel-body">{children}</div>
    </section>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="phlo-observatory-mini-row">
      <span>{label}</span>
      <small>{value}</small>
    </div>
  )
}

function Unavailable({ message }: { message: string }) {
  return <div className="phlo-observatory-settings-error">{message}</div>
}
