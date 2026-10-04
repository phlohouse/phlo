/** Configures the SQL editor with Phlo styling and catalog completion. */
import * as React from 'react'
import { EditorState } from '@codemirror/state'
import {
  EditorView,
  drawSelection,
  highlightActiveLine,
  highlightActiveLineGutter,
  keymap,
  lineNumbers,
} from '@codemirror/view'
import {
  defaultKeymap,
  history,
  historyKeymap,
  indentWithTab,
} from '@codemirror/commands'
import {
  HighlightStyle,
  bracketMatching,
  indentOnInput,
  syntaxHighlighting,
} from '@codemirror/language'
import {
  autocompletion,
  closeBrackets,
  closeBracketsKeymap,
  completionKeymap,
} from '@codemirror/autocomplete'
import { PostgreSQL, sql } from '@codemirror/lang-sql'
import { tags as t } from '@lezer/highlight'
import type { Completion } from '@codemirror/autocomplete'
import type { SQLNamespace } from '@codemirror/lang-sql'
import type { Extension } from '@codemirror/state'
import { cn } from '@/lib/utils'

type QueryCatalog = Array<{
  name: string
  schemas: Array<{ name: string; tables: Array<string> }>
}>

/** Catalog → lang-sql schema. The API currently exposes table names, not columns. */
function toSchema(catalog: QueryCatalog): SQLNamespace {
  const ns: Record<string, Record<string, Array<Completion>>> = {}
  for (const catalogItem of catalog) {
    for (const schema of catalogItem.schemas) {
      const tables = ns[schema.name] ?? {}
      for (const table of schema.tables) tables[table] = []
      ns[schema.name] = tables
    }
  }
  return ns
}

/** Editor chrome drawn with phlo tokens, so light and dark both follow the theme. */
const phloTheme = EditorView.theme({
  '&': {
    height: '100%',
    backgroundColor: 'var(--card)',
    color: 'var(--foreground)',
    fontSize: '13px',
  },
  '&.cm-focused': {
    outline: 'none',
    boxShadow: 'inset 0 0 0 2px var(--primary-line)',
  },
  '.cm-scroller': {
    fontFamily: 'var(--font-mono)',
    lineHeight: '22px',
    overflow: 'auto',
  },
  '.cm-content': { padding: '10px 0', caretColor: 'var(--foreground)' },
  '.cm-line': { padding: '0 16px' },
  '.cm-gutters': {
    backgroundColor: 'var(--raised)',
    color: 'var(--faint)',
    borderRight: '1px solid var(--line)',
    minWidth: '44px',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 12px 0 8px',
    minWidth: '32px',
  },
  '.cm-activeLine': { backgroundColor: 'var(--soft)' },
  '.cm-activeLineGutter': {
    backgroundColor: 'var(--soft)',
    color: 'var(--muted-foreground)',
  },
  '.cm-cursor, .cm-dropCursor': {
    borderLeftColor: 'var(--foreground)',
    borderLeftWidth: '1.5px',
  },
  '&.cm-focused > .cm-scroller > .cm-selectionLayer .cm-selectionBackground, .cm-selectionBackground, ::selection':
    {
      backgroundColor: 'var(--info-soft) !important',
    },
  '&.cm-focused .cm-matchingBracket': {
    backgroundColor: 'var(--primary-line)',
    outline: 'none',
  },
  '&.cm-focused .cm-nonmatchingBracket': { backgroundColor: 'var(--bad-soft)' },
  '.cm-tooltip': {
    backgroundColor: 'var(--popover, var(--card))',
    color: 'var(--foreground)',
    border: '1px solid var(--border)',
    borderRadius: '8px',
    boxShadow: '0 8px 24px -8px rgb(0 0 0 / 0.18)',
    overflow: 'hidden',
  },
  '.cm-tooltip.cm-tooltip-autocomplete > ul': {
    fontFamily: 'var(--font-mono)',
    fontSize: '12.5px',
    maxHeight: '15em',
    padding: '4px',
  },
  '.cm-tooltip.cm-tooltip-autocomplete > ul > li': {
    padding: '3px 8px',
    borderRadius: '5px',
    lineHeight: '20px',
  },
  '.cm-tooltip-autocomplete ul li[aria-selected]': {
    backgroundColor: 'var(--primary-soft)',
    color: 'var(--foreground)',
  },
  '.cm-completionDetail': {
    color: 'var(--faint)',
    fontStyle: 'normal',
    marginLeft: '12px',
  },
  '.cm-completionMatchedText': {
    textDecoration: 'none',
    fontWeight: '600',
    color: 'var(--link)',
  },
  '.cm-completionIcon': { color: 'var(--muted-foreground)', opacity: '0.8' },
  '.cm-completionInfo': {
    backgroundColor: 'var(--card)',
    border: '1px solid var(--border)',
    borderRadius: '8px',
  },
})

/** Same colours the old regex highlighter used. */
const phloHighlight = HighlightStyle.define([
  { tag: [t.lineComment, t.blockComment, t.comment], color: 'var(--faint)' },
  { tag: [t.string, t.special(t.string)], color: 'var(--ok-text)' },
  { tag: [t.number, t.bool, t.null], color: 'var(--warn-ink)' },
  {
    tag: [t.keyword, t.operatorKeyword, t.typeName, t.standard(t.name)],
    color: 'var(--code-kw)',
  },
  {
    tag: [t.function(t.variableName), t.function(t.name)],
    color: 'var(--link)',
  },
  { tag: [t.special(t.name), t.quote], color: 'var(--foreground)' },
])

/**
 * SQL editor on CodeMirror 6: highlighting, autocomplete from the catalog (`silver.` → tables →
 * columns), ⌘/Ctrl-Enter runs. The view is created once on the client; external `value` changes
 * are synced with a transaction. On the server a same-size <pre> stands in, so there is no jump.
 */
export function SqlEditor({
  value,
  onChange,
  onRun,
  catalog,
  className,
  label = 'SQL editor',
}: {
  value: string
  onChange: (v: string) => void
  onRun?: () => void
  /** Tables and columns for autocomplete */
  catalog?: QueryCatalog
  className?: string
  label?: string
}) {
  const host = React.useRef<HTMLDivElement>(null)
  const view = React.useRef<EditorView | null>(null)
  const [ready, setReady] = React.useState(false)
  // Latest callbacks without re-creating the view.
  const cb = React.useRef({ onChange, onRun })
  cb.current = { onChange, onRun }
  const initial = React.useRef(value)
  const schema = React.useMemo(
    () => (catalog ? toSchema(catalog) : undefined),
    [catalog],
  )

  React.useEffect(() => {
    if (!host.current) return
    const extensions: Array<Extension> = [
      lineNumbers(),
      highlightActiveLineGutter(),
      history(),
      drawSelection(),
      highlightActiveLine(),
      bracketMatching(),
      closeBrackets(),
      indentOnInput(),
      autocompletion({ icons: true }),
      keymap.of([
        {
          key: 'Mod-Enter',
          preventDefault: true,
          run: () => {
            cb.current.onRun?.()
            return true
          },
        },
        ...closeBracketsKeymap,
        ...defaultKeymap,
        ...historyKeymap,
        ...completionKeymap,
        indentWithTab,
      ]),
      sql({ dialect: PostgreSQL, schema, upperCaseKeywords: true }),
      syntaxHighlighting(phloHighlight),
      phloTheme,
      EditorView.contentAttributes.of({
        'aria-label': label,
        spellcheck: 'false',
        autocapitalize: 'off',
        autocorrect: 'off',
      }),
      EditorView.updateListener.of((u) => {
        if (u.docChanged) cb.current.onChange(u.state.doc.toString())
      }),
    ]
    const v = new EditorView({
      parent: host.current,
      state: EditorState.create({ doc: initial.current, extensions }),
    })
    view.current = v
    setReady(true)
    return () => {
      v.destroy()
      view.current = null
    }
  }, [schema, label])

  // Sync value changes that did not come from typing (tab switch, saved query, …).
  React.useEffect(() => {
    initial.current = value
    const v = view.current
    if (!v) return
    const cur = v.state.doc.toString()
    if (cur !== value)
      v.dispatch({ changes: { from: 0, to: cur.length, insert: value } })
  }, [value])

  return (
    <div className={cn('relative min-h-0 overflow-hidden bg-card', className)}>
      {ready ? null : (
        <div
          aria-hidden
          className="absolute inset-0 flex font-mono text-[13px] leading-[22px]"
        >
          <div className="w-11 shrink-0 border-r border-line bg-raised py-2.5 pr-3 text-right text-faint select-none">
            {value.split('\n').map((_, i) => (
              <div key={i}>{i + 1}</div>
            ))}
          </div>
          <pre className="m-0 min-w-0 flex-1 overflow-hidden px-4 py-2.5 font-mono whitespace-pre text-foreground">
            {value}
          </pre>
        </div>
      )}
      <div ref={host} className="h-full" />
    </div>
  )
}
