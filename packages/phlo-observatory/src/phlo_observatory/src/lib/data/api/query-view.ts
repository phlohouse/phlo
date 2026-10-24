/** Validates saved query presentation separately from SQL and result evidence. */
import { z } from 'zod'

export const queryViewSchema = z
  .object({
    version: z.literal(1).default(1),
    view: z.enum(['results', 'chart', 'pivot']).default('results'),
    bucket: z.enum(['raw', 'minute', 'hour', 'day']).default('raw'),
    type: z.enum(['line', 'bar', 'area', 'scatter']).default('line'),
    x: z.string().max(256).nullable().default(null),
    y: z.string().max(256).nullable().default(null),
    group: z.string().max(256).default(''),
    operation: z.enum(['avg', 'sum', 'min', 'max', 'count']).default('avg'),
    reference: z.boolean().default(false),
    lower: z.string().max(32).default('80'),
    upper: z.string().max(32).default('125'),
    labels: z.boolean().default(false),
  })
  .strict()
export type QueryView = z.infer<typeof queryViewSchema>

export function restoreQueryView(
  metadata?: Record<string, unknown>,
): QueryView {
  const parsed = queryViewSchema.safeParse(metadata?.query_view)
  return parsed.success ? parsed.data : queryViewSchema.parse({})
}

export function savedQueryMetadata(
  view?: QueryView,
): Record<string, QueryView> {
  return view ? { query_view: queryViewSchema.parse(view) } : {}
}
