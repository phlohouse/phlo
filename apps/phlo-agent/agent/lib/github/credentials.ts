/** GitHub connector binding for the phlo-agent's installation credentials. */
import { connectGitHubCredentials } from '@vercel/connect/eve'

export const GITHUB_CONNECTOR = 'github/phlo-agent'
export const githubCredentials = connectGitHubCredentials(GITHUB_CONNECTOR)
