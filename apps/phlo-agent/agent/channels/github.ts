import { githubChannel } from 'eve/channels/github'
import { githubCredentials } from '../lib/github/credentials'

export default githubChannel({
  botName: process.env.PHLO_AGENT_GITHUB_BOT ?? 'phlo-agent',
  credentials: githubCredentials,
})
