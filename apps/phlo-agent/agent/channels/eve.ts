/**
 * Eve channel wiring: accepts Vercel OIDC in production, falls back to local
 * dev auth, and rejects unauthenticated requests via the placeholder policy.
 */
import { localDev, placeholderAuth, vercelOidc } from 'eve/channels/auth'
import { eveChannel } from 'eve/channels/eve'

export default eveChannel({
  auth: [vercelOidc(), localDev(), placeholderAuth()],
})
