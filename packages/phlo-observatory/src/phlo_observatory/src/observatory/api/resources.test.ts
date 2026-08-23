/**
 * Tests the observatory API middleware: request context propagation and
 * serializable-payload validation when proxying calls to phlo-api.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

type ServerFnBuilderMock = {
  middleware: (middlewares: Array<RequestMiddleware>) => ServerFnBuilderMock
  inputValidator: <TInputValidator>(
    inputValidator: TInputValidator,
  ) => ServerFnBuilderMock
  handler: <THandler>(handler: THandler) => THandler
}

type RequestMiddleware = (options: {
  next: (options?: { context?: Record<string, unknown> }) => unknown
  request: Request
}) => unknown

type ServerFnOptions = {
  data: unknown
  headers?: HeadersInit
}

const apiGet = vi.fn()
const apiPost = vi.fn()

vi.mock('@tanstack/react-start', () => ({
  createMiddleware: () => ({
    server: <THandler>(handler: THandler): THandler => handler,
  }),
  createServerFn: () => {
    const middlewares: Array<RequestMiddleware> = []
    let validateInput = (input: unknown): unknown => input
    const builder: ServerFnBuilderMock = {
      middleware: (registered) => {
        middlewares.push(...registered)
        return builder
      },
      inputValidator: (validator) => {
        validateInput = validator as (input: unknown) => unknown
        return builder
      },
      handler: (handler) =>
        ((options: ServerFnOptions) => {
          const invokeHandler = handler as unknown as (context: {
            context: Record<string, unknown>
            data: unknown
          }) => unknown
          let context: Record<string, unknown> = {}
          const invokeMiddleware = (index: number): unknown => {
            if (index === middlewares.length) {
              return invokeHandler({
                context,
                data: validateInput(options.data),
              })
            }
            return middlewares[index]({
              request: new Request('https://observatory.example.test', {
                headers: options.headers,
              }),
              next: (nextOptions = {}) => {
                context = { ...context, ...nextOptions.context }
                return invokeMiddleware(index + 1)
              },
            })
          }
          return invokeMiddleware(0)
        }) as unknown as typeof handler,
    }
    return builder
  },
}))

vi.mock('@/server/phlo-api', () => ({
  apiGet,
  apiPost,
}))

describe('observatory dataset resources', () => {
  beforeEach(() => {
    apiGet.mockReset()
    apiPost.mockReset()
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('fetches Dataset records from phlo-api', async () => {
    apiGet.mockResolvedValue({
      items: [
        {
          id: 'gold.orders',
          name: 'gold.orders',
          classifications: ['internal'],
          publication_state: 'published',
          readiness_state: 'ok',
          candidate: false,
          kinds: ['table'],
          source_refs: [],
          metadata: {},
        },
      ],
    })

    const { getObservatoryDatasetRecords } = await import('./resources')
    const result = await getObservatoryDatasetRecords()

    expect(result.error).toBeNull()
    expect(result.data).toEqual([
      expect.objectContaining({
        id: 'gold.orders',
        publication_state: 'published',
      }),
    ])
    expect(apiGet).toHaveBeenCalledWith(
      '/api/observatory/datasets',
      undefined,
      8000,
    )
  })

  it('fetches publishing readiness in one bounded browser request', async () => {
    const fetch = vi.fn().mockResolvedValue({
      json: () =>
        Promise.resolve({
          items: [
            { dataset_id: 'gold.orders', publishing: { state: 'unknown' } },
          ],
        }),
      ok: true,
    })
    vi.stubGlobal('document', { querySelector: () => null })
    vi.stubGlobal('window', {
      __PHLO_API_BROWSER_URL__: 'https://api.example.test',
      clearTimeout,
      setTimeout,
    })
    vi.stubGlobal('fetch', fetch)

    const { getObservatoryPublishingReadinessDirect } =
      await import('./resources')
    const result = await getObservatoryPublishingReadinessDirect()

    expect(result).toMatchObject({
      data: [expect.objectContaining({ dataset_id: 'gold.orders' })],
      error: null,
    })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(fetch).toHaveBeenCalledWith(
      'https://api.example.test/api/observatory/datasets/publishing-readiness',
      expect.any(Object),
    )
  })

  it('forwards the signed-in operator bearer credential to the run report API', async () => {
    apiGet.mockResolvedValue({
      schema_version: 1,
      project_id: 'finance',
      run_id: 'daily-orders',
      attempt: 2,
      lifecycle: { run: null, events: [] },
      stages: [],
      inputs: [],
      staging: [],
      outputs: [],
      lineage: [],
      transformations: [],
      quality: [],
      iceberg_snapshots: [],
      catalog_changes: [],
      artifacts: [],
      terminal_outcome: null,
      gaps: [],
    })

    const { getObservatoryRunReport } = await import('./resources')
    const result = await getObservatoryRunReport({
      data: { projectId: 'finance', runId: 'daily-orders', attempt: '2' },
      headers: {
        authorization: 'Bearer operator-token',
        'x-unrelated-header': 'must-not-reach-phlo-api',
      },
    })

    expect(result).toMatchObject({
      data: { attempt: 2 },
      error: null,
    })
    expect(apiGet).toHaveBeenCalledWith(
      '/api/observatory/projects/finance/runs/daily-orders/attempts/2/report',
      undefined,
      8000,
      'Bearer operator-token',
    )
  })

  it('does not forward non-Bearer or unrelated request headers', async () => {
    apiGet.mockResolvedValue({
      schema_version: 1,
      project_id: 'finance',
      run_id: 'daily-orders',
      attempt: 2,
      lifecycle: { run: null, events: [] },
      stages: [],
      inputs: [],
      staging: [],
      outputs: [],
      lineage: [],
      transformations: [],
      quality: [],
      iceberg_snapshots: [],
      catalog_changes: [],
      artifacts: [],
      terminal_outcome: null,
      gaps: [],
    })

    const { getObservatoryRunReport } = await import('./resources')
    await getObservatoryRunReport({
      data: { projectId: 'finance', runId: 'daily-orders', attempt: 2 },
      headers: {
        authorization: 'Basic should-not-forward',
        'x-unrelated-header': 'must-not-reach-phlo-api',
      },
    })

    expect(apiGet).toHaveBeenCalledWith(
      '/api/observatory/projects/finance/runs/daily-orders/attempts/2/report',
      undefined,
      8000,
      undefined,
    )
  })

  it.each([401, 403])(
    'classifies report %i failures without changing the API contract',
    async (status) => {
      apiGet.mockRejectedValue(new Error(`phlo-api error: ${status} Forbidden`))

      const { getObservatoryRunReport } = await import('./resources')
      const result = await getObservatoryRunReport({
        data: { projectId: 'finance', runId: 'daily-orders', attempt: 2 },
        headers: { authorization: 'Bearer operator-token' },
      })

      expect(result).toEqual({
        data: null,
        error:
          'Access denied: this account cannot read the requested run report.',
        errorCode: 'access_denied',
      })
    },
  )

  it.each([
    null,
    'finance/daily-orders/2',
    [],
    { attempt: 2, projectId: 1, runId: 'daily-orders' },
    { attempt: 2, projectId: 'finance', runId: null },
    { attempt: '1e2', projectId: 'finance', runId: 'daily-orders' },
    {
      attempt: Number.MAX_SAFE_INTEGER + 1,
      projectId: 'finance',
      runId: 'daily-orders',
    },
    {
      attempt: '9007199254740992',
      projectId: 'finance',
      runId: 'daily-orders',
    },
    { attempt: 0, projectId: 'finance', runId: 'daily-orders' },
    { attempt: -1, projectId: 'finance', runId: 'daily-orders' },
  ])(
    'rejects invalid run report input %o before calling the API',
    async (data) => {
      const { getObservatoryRunReport } = await import('./resources')
      const result = await getObservatoryRunReport({ data })

      expect(result).toEqual({
        data: null,
        error:
          'Enter a project, run, and positive attempt number to open a report.',
        errorCode: 'invalid_request',
      })
      expect(apiGet).not.toHaveBeenCalled()
    },
  )

  it('does not expose unexpected upstream failure details', async () => {
    apiGet.mockRejectedValue(
      new Error('phlo-api error: 500 internal token=secret'),
    )

    const { getObservatoryRunReport } = await import('./resources')
    const result = await getObservatoryRunReport({
      data: { projectId: 'finance', runId: 'daily-orders', attempt: 2 },
    })

    expect(result).toEqual({
      data: null,
      error: 'The run report request failed. Please try again.',
      errorCode: 'request_failed',
    })
  })

  it('creates workflow proposals through the server API during SSR', async () => {
    const proposal = workflowProposal()
    apiPost.mockResolvedValue(proposal)

    const { createObservatoryWorkflowProposal } = await import('./resources')
    const request = workflowProposalRequest()
    const result = await createObservatoryWorkflowProposal({ data: request })

    expect(result).toEqual({ data: proposal, error: null })
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/workflow-wizard/proposals',
      request,
      12000,
    )
  })

  it('creates workflow proposals through the browser API in the client', async () => {
    const proposal = workflowProposal()
    const fetchMock = stubBrowserPost(proposal)

    const { createObservatoryWorkflowProposal } = await import('./resources')
    const request = workflowProposalRequest()
    const result = await createObservatoryWorkflowProposal({ data: request })

    expect(result).toEqual({ data: proposal, error: null })
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/api/observatory/workflow-wizard/proposals',
      expect.objectContaining({
        body: JSON.stringify(request),
        method: 'POST',
      }),
    )
  })

  it('runs workflow actions through the server API during SSR', async () => {
    const proposal = workflowProposal()
    const actionResult = workflowActionResult()
    apiPost.mockResolvedValue(actionResult)

    const { runObservatoryWorkflowAction } = await import('./resources')
    const result = await runObservatoryWorkflowAction({
      data: { actionId: 'apply', proposal },
    })

    expect(result).toEqual({ data: actionResult, error: null })
    expect(apiPost).toHaveBeenCalledWith(
      '/api/observatory/workflow-wizard/actions',
      { action_id: 'apply', proposal_id: proposal.proposal_id },
      12000,
    )
  })

  it('runs workflow actions through the browser API in the client', async () => {
    const proposal = workflowProposal()
    const actionResult = workflowActionResult()
    const fetchMock = stubBrowserPost(actionResult)

    const { runObservatoryWorkflowAction } = await import('./resources')
    const result = await runObservatoryWorkflowAction({
      data: { actionId: 'apply', proposal },
    })

    expect(result).toEqual({ data: actionResult, error: null })
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.example.test/api/observatory/workflow-wizard/actions',
      expect.objectContaining({
        body: JSON.stringify({
          action_id: 'apply',
          proposal_id: proposal.proposal_id,
        }),
        method: 'POST',
      }),
    )
  })
})

function workflowProposalRequest() {
  return {
    domain: 'finance',
    graph: { edges: [], nodes: [] },
    workflow_name: 'Revenue refresh',
  }
}

function workflowProposal() {
  return {
    actions: [],
    disabled_stages: {},
    domain: 'finance',
    files: [],
    missing_capabilities: [],
    planned_assets: [],
    planned_models: [],
    planned_tables: [],
    selected_contributions: [],
    warnings: [],
    workflow_name: 'Revenue refresh',
    proposal_id: 'proposal_1234567890',
  }
}

function workflowActionResult() {
  return {
    action_id: 'apply',
    files: [],
    message: 'Applied',
    status: 'succeeded' as const,
  }
}

function stubBrowserPost(payload: unknown) {
  vi.stubGlobal('window', {
    __PHLO_API_BROWSER_URL__: 'https://api.example.test',
    clearTimeout: globalThis.clearTimeout,
    setTimeout: globalThis.setTimeout,
  })
  const fetchMock = vi.fn().mockResolvedValue({
    json: vi.fn().mockResolvedValue(payload),
    ok: true,
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}
