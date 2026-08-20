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
