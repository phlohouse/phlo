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
