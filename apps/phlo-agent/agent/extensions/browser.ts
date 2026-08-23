/**
 * Browser tool extension restricted to an allowlist of GitHub, Vercel, and
 * local hosts, with content boundaries and inline screenshots enabled.
 */
import browser from '@agent-browser/eve'

export default browser({
  allowedDomains: [
    'github.com',
    '*.githubusercontent.com',
    '*.vercel.app',
    'localhost',
    '127.0.0.1',
  ],
  contentBoundaries: true,
  maxOutputChars: 50_000,
  inlineScreenshots: true,
})
