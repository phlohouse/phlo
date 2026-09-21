import { createServer } from 'node:http'
import { handleRequest, maxRequestLength } from './publisher.js'

const port = Number(process.env.PORT ?? '3000')

createServer(async (incoming, outgoing) => {
  const chunks: Buffer[] = []
  let length = 0
  for await (const chunk of incoming) {
    const buffer = Buffer.from(chunk)
    length += buffer.length
    if (length > maxRequestLength) {
      outgoing.writeHead(413, { 'content-type': 'application/json' })
      outgoing.end(JSON.stringify({ error: 'Request body is too large.' }))
      return
    }
    chunks.push(buffer)
  }
  const origin = process.env.AMP_DEPLOYMENT_URL ?? `http://localhost:${port}/`
  const request = new Request(new URL(incoming.url ?? '/', origin), {
    method: incoming.method ?? 'GET',
    headers: incoming.headers as HeadersInit,
    ...(incoming.method === 'GET' || incoming.method === 'HEAD'
      ? {}
      : { body: Buffer.concat(chunks) }),
  })
  const appId = process.env.PHLO_GITHUB_APP_ID
  const privateKey = process.env.PHLO_GITHUB_APP_PRIVATE_KEY?.replace(/\\n/g, '\n')
  const publishToken = process.env.PHLO_GITHUB_PUBLISH_TOKEN
  const response = await handleRequest(request, {
    ...(appId === undefined ? {} : { appId }),
    ...(privateKey === undefined ? {} : { privateKey }),
    ...(publishToken === undefined ? {} : { publishToken }),
  })
  outgoing.writeHead(response.status, Object.fromEntries(response.headers.entries()))
  outgoing.end(Buffer.from(await response.arrayBuffer()))
}).listen(port, '0.0.0.0')
