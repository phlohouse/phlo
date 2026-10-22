/** CSV review accepts only bounded files and cannot smuggle loading or publication actions. */
import { describe, expect, it } from 'vitest'
import {
  MAX_CSV_BYTES,
  csvUploadInput,
  uploadReviewInputSchema,
} from './uploads'

describe('CSV input boundary', () => {
  it('keeps the File intact and rejects unsupported formats, oversize files and invalid environments', () => {
    const form = new FormData()
    form.set('env', 'sandbox')
    const file = new File(['a,b\r\n1,ND\r\n'], 'results.csv', {
      type: 'text/csv',
    })
    form.set('file', file)
    expect(csvUploadInput(form).file).toBe(file)
    form.set('file', new File(['xlsx'], 'results.xlsx'))
    expect(() => csvUploadInput(form)).toThrow('Choose a CSV file')
    form.set('file', new File([new Uint8Array(MAX_CSV_BYTES + 1)], 'large.csv'))
    expect(() => csvUploadInput(form)).toThrow('at most 10 MiB')
    form.set('file', file)
    form.set('env', '../prod')
    expect(() => csvUploadInput(form)).toThrow()
  })
  it('requires unique destination names and rejects loading flags', () => {
    const column = {
      column: 'result',
      name: 'result',
      type: 'string',
      nullable: false,
      date_format: 'iso',
    }
    const input = {
      env: 'sandbox',
      upload_id: 'a'.repeat(32),
      expected_version: 1,
      columns: [column],
    }
    expect(uploadReviewInputSchema.parse(input)).toEqual(input)
    expect(
      uploadReviewInputSchema.safeParse({ ...input, loaded: true }).success,
    ).toBe(false)
    expect(
      uploadReviewInputSchema.safeParse({
        ...input,
        columns: [column, { ...column, column: 'other' }],
      }).success,
    ).toBe(false)
    expect(
      uploadReviewInputSchema.safeParse({
        ...input,
        columns: [{ ...column, name: '../bad' }],
      }).success,
    ).toBe(false)
  })
})
