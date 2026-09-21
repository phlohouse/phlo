import { handleRequest } from './publisher.js'

interface Environment {
  PHLO_GITHUB_APP_ID?: string
  PHLO_GITHUB_APP_PRIVATE_KEY?: string
  PHLO_GITHUB_PUBLISH_TOKEN?: string
}

export default {
  async fetch(request: Request, environment: Environment): Promise<Response> {
    const privateKey = environment.PHLO_GITHUB_APP_PRIVATE_KEY?.replace(/\\n/g, '\n')
    return handleRequest(request, {
      ...(environment.PHLO_GITHUB_APP_ID === undefined
        ? {}
        : { appId: environment.PHLO_GITHUB_APP_ID }),
      ...(privateKey === undefined ? {} : { privateKey }),
      ...(environment.PHLO_GITHUB_PUBLISH_TOKEN === undefined
        ? {}
        : { publishToken: environment.PHLO_GITHUB_PUBLISH_TOKEN }),
    })
  },
}
