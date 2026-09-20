/** Authenticated bridge that publishes Amp-generated reviews as phlo-agent. */
import { POST, defineChannel } from 'eve/channels'
import { githubCredentials } from '../lib/github/credentials'
import { mintInstallationToken } from '../lib/github/push'
import { handleAmpPublishRequest } from '../lib/github/amp-publisher'

export default defineChannel({
  routes: [
    POST('/amp/v1/github-comments', async (request) =>
      handleAmpPublishRequest(request, {
        installationToken: () => mintInstallationToken(githubCredentials),
        publishToken: process.env.PHLO_AGENT_AMP_PUBLISH_TOKEN,
        writesEnabled: process.env.PHLO_AGENT_AMP_PUBLISH_WRITES === '1',
      })),
  ],
})
