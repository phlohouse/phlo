/**
 * Nitro startup plugin that refuses to boot Observatory in a production-like
 * environment without an authentication credential. Registered through the
 * `plugins` list in vite.config.ts, so it runs once when the server starts —
 * before any request is served — and a missing OBSERVATORY_AUTH_TOKEN is a
 * fatal, actionable startup error instead of a silent anonymous control
 * plane.
 */
import type { NitroAppPlugin } from 'nitro/types'

import { assertObservatoryAuthConfiguration } from '../observatory/api/auth'

const observatoryAuthStartup: NitroAppPlugin = () => {
  assertObservatoryAuthConfiguration()
}

export default observatoryAuthStartup
