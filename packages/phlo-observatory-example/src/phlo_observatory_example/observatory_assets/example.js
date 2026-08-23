/**
 * Example observatory extension. Registers a route under /extensions/example,
 * dashboard and hub slot widgets, and a settings panel that exercises the
 * host-provided settings API.
 */
export function registerRoutes({ createRoute, rootRoute }) {
  return createRoute({
    getParentRoute: () => rootRoute,
    path: '/extensions/example',
    component: ExamplePage,
  })
}

export function registerDashboardSlot({ register }) {
  register(DashboardNote)
}

export function registerHubSlot({ register }) {
  register(HubNote)
}

const settingsApi = { loadSettings: null, saveSettings: null }

export function registerSettings({ register, loadSettings, saveSettings }) {
  settingsApi.loadSettings = loadSettings
  settingsApi.saveSettings = saveSettings
  register({
    id: 'example-settings',
    title: 'Example Extension',
    description: 'Demonstrates extension settings panels.',
    component: ExampleSettings,
  })
}

function ExamplePage() {
  return 'Example extension route loaded.'
}

function DashboardNote() {
  return 'Extension slot: dashboard.after-cards.'
}

function HubNote() {
  return 'Extension slot: hub.after-stats.'
}

function ExampleSettings() {
  const React = typeof globalThis !== 'undefined' ? globalThis.__phloReact : null
  if (!React) return 'React is not available for extension settings.'

  const { useEffect, useState } = React
  const el = React.createElement
  const loadSettingsFn = settingsApi.loadSettings
  const saveSettingsFn = settingsApi.saveSettings
  if (!loadSettingsFn || !saveSettingsFn) {
    return 'Settings API is not available for this extension.'
  }
  const [loading, setLoading] = useState(true)
  const [message, setMessage] = useState('')
  const [enabled, setEnabled] = useState(false)
  const [status, setStatus] = useState(null)

  useEffect(() => {
    let active = true
    loadSettingsFn()
      .then((data) => {
        if (!active) return
        setEnabled(Boolean(data.enabled))
        setMessage(typeof data.message === 'string' ? data.message : '')
        setLoading(false)
      })
      .catch(() => {
        if (!active) return
        setStatus('Failed to load settings.')
        setLoading(false)
      })
    return () => {
      active = false
    }
  }, [loadSettingsFn])

  const handleSave = async () => {
    setStatus(null)
    try {
      await saveSettingsFn({ enabled, message })
      setStatus('Saved.')
    } catch {
      setStatus('Save failed.')
    }
  }

  if (loading) return 'Loading settings...'

  return el(
    'div',
    { className: 'space-y-3' },
    el(
      'div',
      { className: 'space-y-2' },
      el(
        'label',
        {
          className: 'text-sm font-medium',
          htmlFor: 'example-settings-message',
        },
        'Message',
      ),
      el('input', {
        id: 'example-settings-message',
        className:
          'w-full rounded-md border border-input bg-background px-3 py-2 text-sm',
        placeholder: 'Message',
        value: message,
        onChange: (event) => setMessage(event.target.value),
      }),
    ),
    el(
      'label',
      { className: 'flex items-center gap-2 text-sm' },
      el('input', {
        type: 'checkbox',
        className: 'h-4 w-4',
        checked: enabled,
        onChange: (event) => setEnabled(event.target.checked),
      }),
      'Enabled',
    ),
    el(
      'div',
      { className: 'flex items-center gap-2' },
      el(
        'button',
        {
          type: 'button',
          className:
            'inline-flex items-center rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground shadow-sm',
          onClick: handleSave,
        },
        'Save',
      ),
      status
        ? el('span', { className: 'text-sm text-muted-foreground' }, status)
        : null,
    ),
  )
}
