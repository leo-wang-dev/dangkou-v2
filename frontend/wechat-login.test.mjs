import assert from 'node:assert/strict'
import { test } from 'node:test'
import { wechatLoginCode } from './src/wechat-login.js'
import { toolApi } from './src/api.js'

test('requests a new WeChat code with the supported provider', async () => {
  let provider = ''
  globalThis.uni = { login: opts => { provider = opts.provider; opts.success({ code: 'one-use-code' }) } }
  assert.equal(await wechatLoginCode(), 'one-use-code')
  assert.equal(provider, 'weixin')
  delete globalThis.uni
})

test('rejects a response without a code', async () => {
  globalThis.uni = { login: opts => opts.success({}) }
  await assert.rejects(wechatLoginCode(), /Missing WeChat code/)
  delete globalThis.uni
})

test('central login sends code; existing email binding sends bearer explicitly', async () => {
  const previousFetch = globalThis.fetch
  const calls = []
  globalThis.fetch = async (url, options) => {
    calls.push({ url, options })
    return { ok: true, status: 200, json: async () => ({ token: 'new-token' }) }
  }
  try {
    await toolApi.wechat('fresh-code', 'guest-capability')
    await toolApi.wechat('next-code', '', 'email-bearer')
    assert.equal(calls[0].url, 'auth/wechat')
    assert.deepEqual(JSON.parse(calls[0].options.body), { code: 'fresh-code', guest: 'guest-capability' })
    assert.equal(calls[0].options.headers.Authorization, undefined)
    assert.equal(calls[1].options.headers.Authorization, 'Bearer email-bearer')
    await toolApi.verify('buyer@example.com', '123456', '', 'wechat-bearer')
    assert.equal(JSON.parse(calls[2].options.body).link_token, 'wechat-bearer')
  } finally {
    globalThis.fetch = previousFetch
  }
})
