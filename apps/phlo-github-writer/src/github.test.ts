import assert from 'node:assert/strict'
import { createVerify, generateKeyPairSync } from 'node:crypto'
import test from 'node:test'
import { appJwt } from './github.js'

for (const type of ['pkcs1', 'pkcs8'] as const) {
  test(`signs GitHub App JWTs from ${type} private keys`, async () => {
    const { privateKey, publicKey } = generateKeyPairSync('rsa', { modulusLength: 2048 })
    const pem = privateKey.export({ format: 'pem', type }).toString()
    const jwt = await appJwt('4662586', pem, Date.UTC(2026, 8, 21))
    const [header, payload, signature] = jwt.split('.')

    assert.ok(header !== undefined && payload !== undefined && signature !== undefined)
    assert.equal(
      createVerify('RSA-SHA256')
        .update(`${header}.${payload}`)
        .verify(publicKey, Buffer.from(signature, 'base64url')),
      true,
    )
  })
}
