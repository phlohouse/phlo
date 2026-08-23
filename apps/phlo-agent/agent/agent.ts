/**
 * Agent definition for the phlo-house agent: selects between the text and
 * vision models per event and wraps step-start history so stale visuals are
 * replaced with placeholders before the model sees them.
 */
import type { ModelMessage } from 'ai'
import { defineAgent, defineDynamic } from 'eve'
import { gatewayOptions } from './lib/gateway'
import { modelForMessages, modelForStep } from './lib/model'

interface ModelContext {
  channel: { kind?: string }
  messages: readonly ModelMessage[]
}

function selectModel(_event: unknown, ctx: ModelContext) {
  return {
    model: modelForMessages(ctx.messages),
    modelOptions: gatewayOptions(ctx.channel.kind),
  }
}

export default defineAgent({
  // Turn and session starts choose between the text and vision models; step
  // starts can additionally wrap the text model with middleware that replaces
  // visuals left in history with placeholders.
  model: defineDynamic({
    events: {
      'session.started': selectModel,
      'turn.started': selectModel,
      'step.started': (_event: unknown, ctx: ModelContext) => ({
        model: modelForStep(ctx.messages),
        modelOptions: gatewayOptions(ctx.channel.kind),
      }),
    },
  }),
  reasoning: 'high',
  limits: {
    maxInputTokensPerSession: 20_000_000,
    maxOutputTokensPerSession: 250_000,
  },
})
