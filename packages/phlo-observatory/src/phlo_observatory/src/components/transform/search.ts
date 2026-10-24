/** TanStack decodes bare numeric search values as numbers; preserve the focus URL contract. */
import { z } from 'zod'

export const transformSearchSchema = z.object({
  model: z.string().optional(),
  focus: z
    .preprocess(
      (value) => (typeof value === 'number' ? String(value) : value),
      z.enum(['0', '1']),
    )
    .default('0'),
  draft: z.string().uuid().optional(),
  revision: z.number().int().positive().optional(),
})
