import { cpSync, existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { basename, dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { isIP } from 'node:net'
import { spawnSync } from 'node:child_process'

const project = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const appid = (process.env.WECHAT_MINIAPP_APP_ID || '').trim()
const rawOrigin = (process.env.WECHAT_MINIAPP_API_ORIGIN || '').trim()
if (!/^wx[0-9a-f]{16}$/.test(appid)) {
  throw new Error('Set WECHAT_MINIAPP_APP_ID to the actual mini-program AppID')
}
let origin
try { origin = new URL(rawOrigin) } catch { throw new Error('Set WECHAT_MINIAPP_API_ORIGIN to an HTTPS domain') }
if (origin.protocol !== 'https:' || !origin.hostname.includes('.') || isIP(origin.hostname) ||
    origin.port || origin.pathname !== '/' || origin.search || origin.hash ||
    origin.username || origin.password) {
  throw new Error('WECHAT_MINIAPP_API_ORIGIN must be a plain HTTPS domain without IP, port, path or credentials')
}
const modules = resolve(process.env.WECHAT_MINIAPP_NODE_MODULES || join(project, 'node_modules'))
if (!existsSync(join(modules, '.bin', 'uni'))) throw new Error('Run npm ci in frontend/ before building')
const output = resolve(process.env.WECHAT_MINIAPP_OUTPUT_DIR || join(project, 'dist/release/mp-weixin'))
const temp = mkdtempSync(join(tmpdir(), 'dangkou-miniapp-release-'))
try {
  const build = join(temp, 'frontend')
  cpSync(project, build, { recursive: true, filter: path =>
    path === project || !['node_modules', 'dist', '.git'].includes(basename(path)) })
  symlinkSync(modules, join(build, 'node_modules'), 'dir')
  const manifestPath = join(build, 'src/manifest.json')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  manifest['mp-weixin'].appid = appid
  manifest['mp-weixin'].setting.urlCheck = true
  writeFileSync(manifestPath, JSON.stringify(manifest, null, 2) + '\n')
  writeFileSync(join(build, 'src/release-config.js'),
    '// Generated in an isolated release build; no secrets.\nexport const MINIAPP_RELEASE_ORIGIN = ' + JSON.stringify(origin.origin) + '\n')
  const result = spawnSync('npm', ['run', 'build:mp-weixin'], { cwd: build, stdio: 'inherit' })
  if (result.status !== 0) throw new Error('Mini-program build failed')
  const compiled = join(build, 'dist/build/mp-weixin')
  const config = JSON.parse(readFileSync(join(compiled, 'project.config.json'), 'utf8'))
  if (config.appid !== appid) throw new Error('Compiled AppID does not match the configured AppID')
  rmSync(output, { recursive: true, force: true })
  mkdirSync(dirname(output), { recursive: true })
  cpSync(compiled, output, { recursive: true })
  process.stdout.write(`Mini-program release artifact: ${output}\n`)
} finally {
  rmSync(temp, { recursive: true, force: true })
}
