/** Sources preserve exact asset identity and reject secrets or code in authoring inputs. */
import { describe, expect, it } from 'vitest'
import { sourceJobIds, sourceWorkflowInputSchema } from './sources'

const input = {
  domain: 'lab',
  table: 'samples',
  unique_key: 'sample_id',
  api_base_url: 'https://example.org/api',
  fields: ['sample_id:str', 'result:float?'],
}
describe('source authoring inputs', () => {
  it('accepts a bounded typed source and rejects credentials, unsupported types and unsafe paths', () => {
    expect(sourceWorkflowInputSchema.parse(input)).toEqual(input)
    for (const api_base_url of [
      'https://user:password@example.org',
      'https://example.org?token=secret',
      'file:///tmp/data',
      'javascript:alert(1)',
    ])
      expect(
        sourceWorkflowInputSchema.safeParse({ ...input, api_base_url }).success,
      ).toBe(false)
    for (const fields of [['id:object'], ['id:str;import os'], []])
      expect(
        sourceWorkflowInputSchema.safeParse({ ...input, fields }).success,
      ).toBe(false)
    expect(
      sourceWorkflowInputSchema.safeParse({ ...input, domain: '../outside' })
        .success,
    ).toBe(false)
  })
  it('matches every key segment when linking registered sources to pipelines', () => {
    const source = {
      id: 'bronze/samples',
      key: ['bronze', 'samples'],
      description: null,
      compute_kind: null,
      relation: null,
      last_materialization_at: null,
      last_run_id: null,
    }
    const base = {
      repository_name: 'repo',
      description: null,
      domain: null,
      owners: [],
      source: null,
      feeds_batch_release: false,
    }
    expect(
      sourceJobIds(source, [
        { ...base, id: 'correct', selected_assets: [['bronze', 'samples']] },
        {
          ...base,
          id: 'wrong_layer',
          selected_assets: [['silver', 'samples']],
        },
        {
          ...base,
          id: 'wrong_depth',
          selected_assets: [['bronze', 'samples', 'extra']],
        },
      ]),
    ).toEqual(['correct'])
  })
})
