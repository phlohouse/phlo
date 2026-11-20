/** CodeMirror editor for notebook cells: grows with its content, Shift-Enter runs. */
import * as React from 'react'
import { EditorState } from '@codemirror/state'
import {
  EditorView,
  drawSelection,
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
  StreamLanguage,
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
import type { CompletionSource } from '@codemirror/autocomplete'
import type { StringStream } from '@codemirror/language'
import type { Extension } from '@codemirror/state'
import { phloHighlight, phloTheme } from '@/components/query/sql-editor'
import { cn } from '@/lib/utils'

const keywords = new Set(
  'and as assert async await break class continue def del elif else except finally for from global if import in is lambda match case nonlocal not or pass raise return try while with yield'.split(
    ' ',
  ),
)
const atoms = new Set(['True', 'False', 'None'])

type PyState = { quote: string | null }

function pyString(stream: StringStream, state: PyState) {
  const quote = state.quote ?? ''
  while (!stream.eol()) {
    if (stream.match(quote)) {
      state.quote = null
      return 'string'
    }
    if (stream.next() === '\\') stream.next()
  }
  // Only triple-quoted strings run past the end of a line.
  if (quote.length === 1) state.quote = null
  return 'string'
}

/** Enough Python highlighting for analysis code without another dependency. */
const python = StreamLanguage.define<PyState>({
  name: 'python',
  startState: () => ({ quote: null }),
  token(stream, state) {
    if (state.quote) return pyString(stream, state)
    if (stream.eatSpace()) return null
    if (stream.match('#')) {
      stream.skipToEnd()
      return 'comment'
    }
    const quote = stream.match(/^[rRbBfFuU]{0,2}("""|'''|"|')/)
    if (quote && typeof quote !== 'boolean') {
      state.quote = quote[1]
      return pyString(stream, state)
    }
    if (stream.match(/^(0x[0-9a-fA-F_]+|\d[\d_]*\.?\d*(e[+-]?\d+)?j?|\.\d+)/))
      return 'number'
    const word = stream.match(/^[A-Za-z_][A-Za-z0-9_]*/)
    if (word && typeof word !== 'boolean') {
      if (keywords.has(word[0])) return 'keyword'
      if (atoms.has(word[0])) return 'atom'
      if (stream.peek() === '(') return 'variableName.function'
      return 'variableName'
    }
    if (stream.match(/^(\*\*|\/\/|[-+*/%=<>!&|^~@])/)) return 'operator'
    stream.next()
    return null
  },
  languageData: { commentTokens: { line: '#' } },
})

const extraHighlight = HighlightStyle.define([
  { tag: t.atom, color: 'var(--warn-ink)' },
])

/** Cells size to their content; the page scrolls, not the editor. */
const cellTheme = EditorView.theme({
  '&': { height: 'auto', backgroundColor: 'transparent' },
  '&.cm-focused': { boxShadow: 'none' },
  '.cm-scroller': {
    overflowX: 'auto',
    overflowY: 'hidden',
    lineHeight: '20px',
  },
  '.cm-content': { padding: '10px 0' },
  '.cm-gutters': {
    backgroundColor: 'transparent',
    borderRight: 'none',
    minWidth: '36px',
  },
  '.cm-lineNumbers .cm-gutterElement': {
    padding: '0 8px 0 12px',
    minWidth: '28px',
  },
  '.cm-line': { padding: '0 14px 0 6px' },
})

export function CellEditor({
  value,
  onChange,
  onRun,
  language,
  label,
  className,
  complete,
  readOnly = false,
}: {
  value: string
  onChange: (value: string) => void
  /** Shift-Enter or Mod-Enter */
  onRun?: () => void
  language: 'python' | 'sql' | 'text'
  label: string
  className?: string
  /** Completions for this cell: the catalog for SQL, the live kernel for Python */
  complete?: CompletionSource
  readOnly?: boolean
}) {
  const host = React.useRef<HTMLDivElement>(null)
  const view = React.useRef<EditorView | null>(null)
  const [ready, setReady] = React.useState(false)
  const cb = React.useRef({ onChange, onRun, complete })
  cb.current = { onChange, onRun, complete }
  const initial = React.useRef(value)

  React.useEffect(() => {
    if (!host.current) return
    const run = () => {
      cb.current.onRun?.()
      return true
    }
    const extensions: Array<Extension> = [
      lineNumbers(),
      history(),
      drawSelection(),
      bracketMatching(),
      closeBrackets(),
      indentOnInput(),
      keymap.of([
        { key: 'Shift-Enter', preventDefault: true, run },
        { key: 'Mod-Enter', preventDefault: true, run },
        ...closeBracketsKeymap,
        ...completionKeymap,
        ...defaultKeymap,
        ...historyKeymap,
        indentWithTab,
      ]),
      language === 'text'
        ? []
        : autocompletion({
            override: [(context) => cb.current.complete?.(context) ?? null],
            activateOnTypingDelay: 150,
          }),
      EditorState.readOnly.of(readOnly),
      EditorView.editable.of(!readOnly),
      language === 'sql'
        ? sql({ dialect: PostgreSQL, upperCaseKeywords: true })
        : language === 'python'
          ? python
          : [],
      syntaxHighlighting(phloHighlight),
      syntaxHighlighting(extraHighlight),
      phloTheme,
      cellTheme,
      language === 'text' ? EditorView.lineWrapping : [],
      EditorView.contentAttributes.of({
        'aria-label': label,
        spellcheck: language === 'text' ? 'true' : 'false',
        autocapitalize: 'off',
        autocorrect: 'off',
      }),
      EditorView.updateListener.of((update) => {
        if (update.docChanged) cb.current.onChange(update.state.doc.toString())
      }),
    ]
    const editor = new EditorView({
      parent: host.current,
      state: EditorState.create({ doc: initial.current, extensions }),
    })
    view.current = editor
    setReady(true)
    return () => {
      editor.destroy()
      view.current = null
    }
  }, [language, label, readOnly])

  React.useEffect(() => {
    initial.current = value
    const editor = view.current
    if (!editor) return
    const current = editor.state.doc.toString()
    if (current !== value)
      editor.dispatch({
        changes: { from: 0, to: current.length, insert: value },
      })
  }, [value])

  return (
    <div className={cn('relative min-w-0', className)}>
      {ready ? null : (
        <pre
          aria-hidden
          className="m-0 overflow-hidden py-2.5 pr-3.5 pl-[42px] font-mono text-[13px] leading-5 whitespace-pre text-foreground"
        >
          {value || ' '}
        </pre>
      )}
      <div ref={host} />
    </div>
  )
}
