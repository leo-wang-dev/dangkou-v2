import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawnSync } from 'node:child_process'

test('release build rejects a test IP even with a syntactically valid AppID', () => {
  const result = spawnSync('node', ['scripts/build-release-miniapp.mjs'], {
    cwd: new URL('.', import.meta.url), encoding: 'utf8',
    env: { ...process.env, WECHAT_MINIAPP_APP_ID: 'wx0123456789abcdef',
      WECHAT_MINIAPP_API_ORIGIN: 'https://134.175.135.102:80' }
  })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /plain HTTPS domain/)
})

test('release build requires an AppID before touching build files', () => {
  const result = spawnSync('node', ['scripts/build-release-miniapp.mjs'], {
    cwd: new URL('.', import.meta.url), encoding: 'utf8',
    env: { ...process.env, WECHAT_MINIAPP_APP_ID: '',
      WECHAT_MINIAPP_API_ORIGIN: 'https://buyer.example.com' }
  })
  assert.notEqual(result.status, 0)
  assert.match(result.stderr, /actual mini-program AppID/)
})
