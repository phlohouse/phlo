import type { GitHubChannelCredentials } from 'eve/channels/github'
import type { SandboxNetworkPolicy } from 'eve/sandbox'

const PROTECTED_BRANCHES = new Set(['main', 'master'])
const BRANCH_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$/

export function validatePushBranch(branch: string): string | null {
  if (!BRANCH_PATTERN.test(branch) || branch.includes('..') || branch.includes('//')) {
    return `"${branch}" is not a valid branch name.`
  }
  if (branch.startsWith('refs/') || branch === 'HEAD') {
    return `"${branch}" is not a plain branch name.`
  }
  if (PROTECTED_BRANCHES.has(branch)) {
    return `Direct pushes to ${branch} are not allowed. Push a feature branch instead.`
  }
  return null
}

export function pushBrokerPolicy(installationToken: string): SandboxNetworkPolicy {
  const authorization = `Basic ${Buffer.from(`x-access-token:${installationToken}`).toString('base64')}`
  return {
    allow: {
      'github.com': [{ transform: [{ headers: { Authorization: authorization } }] }],
      '*': [],
    },
  }
}

export async function mintInstallationToken(
  credentials: GitHubChannelCredentials,
): Promise<string> {
  const token = credentials.installationToken
  if (token === undefined) {
    throw new Error('The GitHub connector exposes no installation token.')
  }
  return typeof token === 'function' ? await token() : token
}
