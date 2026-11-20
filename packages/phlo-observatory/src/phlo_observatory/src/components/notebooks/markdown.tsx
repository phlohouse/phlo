/** Renders the small Markdown subset notebooks use, as React elements and never raw HTML. */
import * as React from 'react'

function inline(text: string, key: string): Array<React.ReactNode> {
  const parts: Array<React.ReactNode> = []
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g
  let last = 0
  let index = 0
  for (const match of text.matchAll(pattern)) {
    const at = match.index
    if (at > last) parts.push(text.slice(last, at))
    const token = match[0]
    const id = `${key}-${index++}`
    if (token.startsWith('`'))
      parts.push(
        <code
          key={id}
          className="rounded-[4px] bg-soft px-1 py-px font-mono text-[12.5px] text-foreground"
        >
          {token.slice(1, -1)}
        </code>,
      )
    else if (token.startsWith('**'))
      parts.push(
        <strong key={id} className="font-medium text-foreground">
          {token.slice(2, -2)}
        </strong>,
      )
    else parts.push(<em key={id}>{token.slice(1, -1)}</em>)
    last = at + token.length
  }
  if (last < text.length) parts.push(text.slice(last))
  return parts
}

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'list'; items: Array<string> }
  | { kind: 'code'; text: string }
  | { kind: 'paragraph'; text: string }

export function markdownBlocks(source: string): Array<Block> {
  const blocks: Array<Block> = []
  const lines = source.replace(/\r\n/g, '\n').split('\n')
  let paragraph: Array<string> = []
  const flush = () => {
    if (paragraph.length)
      blocks.push({ kind: 'paragraph', text: paragraph.join(' ') })
    paragraph = []
  }
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    const trimmed = line.trim()
    if (trimmed.startsWith('```')) {
      flush()
      const code: Array<string> = []
      for (i++; i < lines.length && !lines[i].trim().startsWith('```'); i++)
        code.push(lines[i])
      blocks.push({ kind: 'code', text: code.join('\n') })
      continue
    }
    const heading = /^(#{1,6})\s+(.*)$/.exec(trimmed)
    if (heading) {
      flush()
      blocks.push({
        kind: 'heading',
        level: heading[1].length,
        text: heading[2],
      })
      continue
    }
    const item = /^[-*]\s+(.*)$/.exec(trimmed)
    if (item) {
      flush()
      const previous = blocks.at(-1)
      if (previous?.kind === 'list') previous.items.push(item[1])
      else blocks.push({ kind: 'list', items: [item[1]] })
      continue
    }
    if (!trimmed) flush()
    else paragraph.push(trimmed)
  }
  flush()
  return blocks
}

export function Markdown({ source }: { source: string }) {
  const blocks = markdownBlocks(source)
  return (
    <div className="flex flex-col gap-2 text-[13.5px] leading-6 text-text-2">
      {blocks.map((block, index) => {
        const key = String(index)
        if (block.kind === 'heading') {
          const Tag = (['h2', 'h3', 'h4'] as const)[
            Math.min(block.level, 3) - 1
          ]
          return (
            <Tag
              key={key}
              className={
                block.level === 1
                  ? 'text-xl font-semibold tracking-tight text-foreground'
                  : 'text-[15px] font-semibold text-foreground'
              }
            >
              {inline(block.text, key)}
            </Tag>
          )
        }
        if (block.kind === 'list')
          return (
            <ul key={key} className="list-disc pl-5">
              {block.items.map((text, item) => (
                <li key={item}>{inline(text, `${key}-${item}`)}</li>
              ))}
            </ul>
          )
        if (block.kind === 'code')
          return (
            <pre
              key={key}
              className="m-0 overflow-x-auto rounded-md bg-sunken px-3 py-2 font-mono text-[12.5px] leading-5"
            >
              {block.text}
            </pre>
          )
        return <p key={key}>{inline(block.text, key)}</p>
      })}
    </div>
  )
}
