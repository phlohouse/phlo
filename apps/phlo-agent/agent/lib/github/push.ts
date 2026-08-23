/**
 * Helpers for pushing from the sandbox: branch-name validation that keeps
 * shell metacharacters out of git commands, and a network policy that delivers
 * the installation token as an Authorization header to github.com only.
 */
import type { GitHubChannelCredentials } from 'eve/channels/github'
import type { SandboxNetworkPolicy } from 'eve/sandbox'

const PROTECTED_BRANCHES = new Set(['main', 'master'])
const BRANCH_PATTERN = /^[A-Za-z0-9](?:[A-Za-z0-9._/-]*[A-Za-z0-9])?$/

/**
 * Validate a branch name before it is interpolated into a git command inside
 * the sandbox. Returns a user-facing refusal message, or null when the name is
 * safe. The character class admits only plain branch names, which also keeps
 * shell metacharacters out; traversal (`..`), empty path segments, ref
 * prefixes, HEAD, and protected branches are rejected explicitly.
 */

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

/**
 * Network policy for a push: github.com requests get the installation token
 * attached as an Authorization header by the sandbox network layer, and every
 * other host is denied. Delivering the credential via the policy keeps it out
 * of the git remote URL and the command line.
 */
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
