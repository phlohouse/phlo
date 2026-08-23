// Model selection for the phlo agent. Sessions run on a cheap text model and
// upgrade to the vision model only when the current turn carries visual parts.
// Visuals left over from earlier turns are stripped by middleware instead of
// keeping the whole session on the more expensive vision model.
import type { LanguageModel, LanguageModelMiddleware, ModelMessage } from 'ai'
import { gateway, wrapLanguageModel } from 'ai'

export const MODEL = process.env.PHLO_AGENT_MODEL ?? 'deepseek/deepseek-v4-flash'
export const VISION_MODEL = process.env.PHLO_AGENT_VISION_MODEL ?? 'alibaba/qwen3.7-flash'

function messageHasVisualParts(message: ModelMessage): boolean {
  if (message.role === 'user' && Array.isArray(message.content)) {
    return message.content.some((part) => part.type === 'image' || part.type === 'file')
  }
  if (message.role === 'tool') {
    for (const part of message.content) {
      if (part.type !== 'tool-result' || part.output.type !== 'content') continue
      if (part.output.value.some((item) => item.type !== 'text')) return true
    }
  }
  return false
}

function hasVisualParts(messages: readonly ModelMessage[]): boolean {
  return messages.some(messageHasVisualParts)
}

// Index of the last user message: the start of the turn currently being
// answered. Returns -1 when the history contains no user message.
function currentTurnStart(messages: readonly ModelMessage[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index]?.role === 'user') return index
  }
  return -1
}

/**
 * Pick the model from the current turn's messages only. Earlier turns may
 * contain visuals, but a text-only turn does not justify the vision model.
 */
export function modelForMessages(messages: readonly ModelMessage[]): string {
  const start = currentTurnStart(messages)
  const currentTurn = start === -1 ? messages : messages.slice(start)
  return hasVisualParts(currentTurn) ? VISION_MODEL : MODEL
}

type StepParams = Awaited<ReturnType<NonNullable<LanguageModelMiddleware['transformParams']>>>
type ProviderPrompt = StepParams['prompt']

const REMOVED_VISUAL = '[image removed from history; ask for it again if it is still needed]'

// Rewrite every visual part into a text placeholder, including items nested
// inside tool-result content arrays, so the outgoing prompt carries no visual
// parts at all.
function stubVisualPrompt(prompt: ProviderPrompt): ProviderPrompt {
  return prompt.map((message) => {
    if (message.role === 'user') {
      return {
        ...message,
        content: message.content.map((part) =>
          part.type === 'file' ? { type: 'text' as const, text: REMOVED_VISUAL } : part,
        ),
      }
    }
    if (message.role === 'assistant' || message.role === 'tool') {
      return {
        ...message,
        content: message.content.map((part) => {
          if (part.type === 'file') return { type: 'text' as const, text: REMOVED_VISUAL }
          if (part.type !== 'tool-result' || part.output.type !== 'content') return part
          return {
            ...part,
            output: {
              ...part.output,
              value: part.output.value.map((item) =>
                item.type === 'text' ? item : { type: 'text' as const, text: REMOVED_VISUAL },
              ),
            },
          }
        }),
      } as typeof message
    }
    return message
  })
}

const stubVisualPartsMiddleware: LanguageModelMiddleware = {
  transformParams: ({ params }) =>
    Promise.resolve({ ...params, prompt: stubVisualPrompt(params.prompt) }),
}

/**
 * Model for one generation step. A turn that itself carries visuals gets
 * VISION_MODEL directly. Otherwise the text model is returned, wrapped in
 * middleware when any earlier message still holds visual parts, since those
 * must be replaced with placeholders before the text model sees the prompt.
 */

export function modelForStep(messages: readonly ModelMessage[]): string | LanguageModel {
  const model = modelForMessages(messages)
  if (model === VISION_MODEL || !hasVisualParts(messages)) return model
  return wrapLanguageModel({
    model: gateway(MODEL),
    middleware: stubVisualPartsMiddleware,
  })
}
