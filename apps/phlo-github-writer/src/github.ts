const API = 'https://api.github.com'
const REPOSITORY = 'phlohouse/phlo'
const RSA_ALGORITHM = { name: 'RSASSA-PKCS1-v1_5', hash: 'SHA-256' }
const PKCS8_RSA_PREFIX = Uint8Array.from([
  0x02, 0x01, 0x00,
  0x30, 0x0d, 0x06, 0x09, 0x2a, 0x86, 0x48, 0x86, 0xf7, 0x0d, 0x01, 0x01, 0x01, 0x05, 0x00,
])

export type Fetcher = typeof fetch

function encoded(value: object): string {
  return Buffer.from(JSON.stringify(value)).toString('base64url')
}

function der(tag: number, value: Uint8Array): Uint8Array<ArrayBuffer> {
  const bytes = []
  for (let remaining = value.length; remaining > 0; remaining >>= 8) bytes.unshift(remaining & 0xff)
  const length = value.length < 128 ? [value.length] : [0x80 | bytes.length, ...bytes]
  return Uint8Array.from([tag, ...length, ...value])
}

function pkcs8(privateKey: string): Uint8Array<ArrayBuffer> {
  const pkcs1 = privateKey.includes('BEGIN RSA PRIVATE KEY')
  const bytes = Uint8Array.from(Buffer.from(privateKey.replace(/-----(?:BEGIN|END) (?:RSA )?PRIVATE KEY-----|\s/g, ''), 'base64'))
  return pkcs1 ? der(0x30, Uint8Array.from([...PKCS8_RSA_PREFIX, ...der(0x04, bytes)])) : bytes
}

export async function appJwt(appId: string, privateKey: string, now = Date.now()): Promise<string> {
  const issuedAt = Math.floor(now / 1_000) - 60
  const unsigned = `${encoded({ alg: 'RS256', typ: 'JWT' })}.${encoded({
    exp: issuedAt + 600,
    iat: issuedAt,
    iss: appId,
  })}`
  const key = await crypto.subtle.importKey('pkcs8', pkcs8(privateKey), RSA_ALGORITHM, false, ['sign'])
  const signature = await crypto.subtle.sign(RSA_ALGORITHM, key, new TextEncoder().encode(unsigned))
  return `${unsigned}.${Buffer.from(signature).toString('base64url')}`
}

export async function githubRequest(
  path: string,
  token: string,
  fetcher: Fetcher,
  init: RequestInit = {},
): Promise<Response> {
  return fetcher(`${API}${path}`, {
    ...init,
    headers: {
      accept: 'application/vnd.github+json',
      authorization: `Bearer ${token}`,
      'content-type': 'application/json',
      'user-agent': 'phlo-agent/1.0',
      'x-github-api-version': '2022-11-28',
      ...init.headers,
    },
  })
}

export async function installationToken(
  appId: string,
  privateKey: string,
  fetcher: Fetcher,
): Promise<string> {
  const jwt = await appJwt(appId, privateKey)
  const installation = await githubRequest(`/repos/${REPOSITORY}/installation`, jwt, fetcher)
  if (!installation.ok) {
    throw new Error(`GitHub App installation lookup failed with HTTP ${installation.status}.`)
  }
  const { id } = await installation.json() as { id?: unknown }
  if (!Number.isSafeInteger(id)) throw new Error('GitHub returned an invalid installation ID.')

  const response = await githubRequest(`/app/installations/${id}/access_tokens`, jwt, fetcher, {
    method: 'POST',
  })
  if (!response.ok) {
    throw new Error(`GitHub App token creation failed with HTTP ${response.status}.`)
  }
  const { token } = await response.json() as { token?: unknown }
  if (typeof token !== 'string' || token.length === 0) {
    throw new Error('GitHub returned an invalid installation token.')
  }
  return token
}

export const repository = REPOSITORY
