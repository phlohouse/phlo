/** Retains exact CSV bytes and reviews all rows without loading or activating a source. */
import { createServerFn } from '@tanstack/react-start'
import { z } from 'zod'
import { environmentSchema, phloApi } from './client'

export const MAX_CSV_BYTES = 10 * 1024 * 1024
export const uploadColumnSchema = z.object({
  column: z.string().min(1).max(256),
  name: z.string().regex(/^[a-z_][a-z0-9_]{0,62}$/),
  type: z.enum(['string', 'integer', 'number', 'boolean', 'date']),
  nullable: z.boolean(),
  date_format: z.enum(['iso', 'day_first', 'month_first']),
  qualifiers: z
    .object({
      value_name: z.string().regex(/^[a-z_][a-z0-9_]{0,62}$/),
      flag_name: z.string().regex(/^[a-z_][a-z0-9_]{0,62}$/),
    })
    .nullish(),
})
const uploadSchema = z.object({
  upload_id: z.string().regex(/^[a-f0-9]{32}$/),
  env: environmentSchema,
  filename: z.string(),
  checksum: z.string().regex(/^[a-f0-9]{64}$/),
  byte_count: z.number().int().nonnegative(),
  actor: z.string(),
  created_at: z.string(),
  version: z.number().int().positive(),
  status: z.enum(['retained', 'reviewed', 'invalid']),
  columns: z.array(uploadColumnSchema),
  row_count: z.number().int().nonnegative().nullable(),
  parse_errors: z.array(z.string()),
  error_count: z.number().int().nonnegative(),
  errors: z.array(
    z.object({
      row: z.number().int(),
      column: z.string(),
      message: z.string(),
    }),
  ),
  reviewed_by: z.string().nullable(),
  reviewed_at: z.string().nullable(),
  raw_columns: z.array(z.string()),
  raw_rows: z.array(z.record(z.string(), z.string())),
  rows: z.array(z.record(z.string(), z.json())),
  sample_limit: z.literal(25),
  loaded: z.literal(false),
  output_columns: z
    .array(
      z.object({
        name: z.string(),
        type: z.enum(['string', 'integer', 'number', 'boolean', 'date']),
        nullable: z.boolean(),
        source_column: z.string(),
        derived: z.boolean(),
      }),
    )
    .optional(),
  column_assistance: z
    .array(
      z.object({
        column: z.string(),
        dates: z
          .object({
            iso: z.number().int(),
            day_first: z.number().int(),
            month_first: z.number().int(),
            ambiguous: z.number().int(),
            invalid: z.number().int(),
            examples: z.array(
              z.object({
                original: z.string(),
                iso: z.string().nullable(),
                day_first: z.string().nullable(),
                month_first: z.string().nullable(),
              }),
            ),
          })
          .nullable(),
        qualifiers: z
          .object({
            numbers: z.number().int(),
            nd: z.number().int(),
            below_loq: z.number().int(),
            other: z.number().int(),
            blank: z.number().int(),
          })
          .nullable(),
      }),
    )
    .optional(),
})
export type CsvUpload = z.infer<typeof uploadSchema>
export type UploadColumn = z.infer<typeof uploadColumnSchema>
export const uploadReviewInputSchema = z
  .object({
    env: environmentSchema,
    upload_id: z.string().regex(/^[a-f0-9]{32}$/),
    expected_version: z.number().int().positive(),
    columns: z
      .array(uploadColumnSchema)
      .min(1)
      .max(100)
      .refine(
        (items) =>
          new Set(
            items.flatMap((item) => [
              item.name,
              ...(item.qualifiers
                ? [item.qualifiers.value_name, item.qualifiers.flag_name]
                : []),
            ]),
          ).size ===
          items.reduce((count, item) => count + (item.qualifiers ? 3 : 1), 0),
        'Column names must be unique.',
      )
      .refine(
        (items) =>
          new Set(items.map((item) => item.column)).size === items.length,
        'Review each original column once.',
      ),
  })
  .strict()
export function csvUploadInput(data: FormData) {
  if (!(data instanceof FormData)) throw new Error('Choose a CSV file.')
  const env = environmentSchema.parse(data.get('env'))
  const file = data.get('file')
  if (!(file instanceof File) || !file.name.toLowerCase().endsWith('.csv'))
    throw new Error(
      'Choose a CSV file. Excel and other formats are not supported in this step.',
    )
  if (
    Array.from(file.name).some(
      (character) =>
        character === '/' || character === '\\' || character.charCodeAt(0) < 32,
    )
  )
    throw new Error('Choose a CSV filename without path separators.')
  if (file.size > MAX_CSV_BYTES)
    throw new Error('CSV files must be at most 10 MiB.')
  return { env, file }
}
export const retainCsvUpload = createServerFn({ method: 'POST' })
  .inputValidator(csvUploadInput)
  .handler(async ({ data }) =>
    phloApi(
      `api/v1/uploads/csv?env=${encodeURIComponent(data.env)}`,
      uploadSchema,
      {
        env: data.env,
        method: 'POST',
        rawBody: await data.file.arrayBuffer(),
        headers: {
          'content-type': 'text/csv',
          'x-phlo-upload-name': encodeURIComponent(data.file.name),
        },
      },
    ),
  )
export const getCsvUpload = createServerFn({ method: 'GET' })
  .inputValidator(
    z.object({
      env: environmentSchema,
      upload_id: z.string().regex(/^[a-f0-9]{32}$/),
    }),
  )
  .handler(({ data }) =>
    phloApi(
      `api/v1/uploads/csv/${data.upload_id}?env=${encodeURIComponent(data.env)}`,
      uploadSchema,
      { env: data.env },
    ),
  )
export const reviewCsvUpload = createServerFn({ method: 'POST' })
  .inputValidator(uploadReviewInputSchema)
  .handler(({ data: { env, upload_id, ...body } }) =>
    phloApi(
      `api/v1/uploads/csv/${upload_id}/columns?env=${encodeURIComponent(env)}`,
      uploadSchema,
      { env, method: 'PUT', body },
    ),
  )
