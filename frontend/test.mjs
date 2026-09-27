// 最小冒烟（无网络、无 uni 运行时依赖）：
// 1) 工程结构完整：pages.json/manifest.json 可解析，每个页面文件存在
// 2) api/storage 模块可被 node 直接加载且导出齐全（条件编译块在 node 下退化为普通代码，不炸）
// 真正的编译验证走 npm run build:h5 / npm run build:mp-weixin。
import { readFileSync, existsSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = dirname(fileURLToPath(import.meta.url))
let failed = 0
const ok = (cond, msg) => {
  console.log((cond ? '  ✓ ' : '  ✗ ') + msg)
  if (!cond) failed++
}

console.log('[1/3] 工程结构与配置')
const pkg = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
ok(pkg.scripts['build:h5'] === 'uni build', 'package.json 有 build:h5 脚本')
ok(pkg.scripts['build:mp-weixin'] === 'uni build -p mp-weixin', 'package.json 有 build:mp-weixin 脚本')
const pages = JSON.parse(readFileSync(join(root, 'src', 'pages.json'), 'utf8'))
ok(Array.isArray(pages.pages) && pages.pages.length === 3, 'pages.json 含 3 个页面')
for (const p of pages.pages) {
  ok(existsSync(join(root, 'src', p.path + '.vue')), `页面文件存在：src/${p.path}.vue`)
}
const manifest = JSON.parse(readFileSync(join(root, 'src', 'manifest.json'), 'utf8'))
ok(manifest.h5 && manifest.h5.router, 'manifest.json 含 h5 路由配置')
ok(manifest['mp-weixin'] && manifest['mp-weixin'].appid !== undefined, 'manifest.json 含 mp-weixin 配置')
ok(existsSync(join(root, 'src', 'components', 'note-table.vue')), '清单表格组件存在（chat 抽屉/list 页共用，替代 iframe）')

console.log('[2/3] API 层可加载（node 环境模拟无 uni）')
const api = await import(join(root, 'src', 'api.js'))
for (const name of ['request', 'upload', 'downloadFile', 'choosePhoto', 'getBases', 'setBases']) {
  ok(typeof api[name] === 'function', `api.js 导出 ${name}()`)
}
for (const name of ['newGuest', 'sendCode', 'verify', 'me', 'uploadPhoto', 'notes', 'photoUrl', 'exportXlsx']) {
  ok(typeof api.toolApi[name] === 'function', `toolApi.${name}() 存在（user-app 接口）`)
}
for (const name of ['send', 'uploadPhoto', 'setLang', 'listToken', 'linkNotes', 'editNote', 'photoUrl', 'exportXlsx']) {
  ok(typeof api.csApi[name] === 'function', `csApi.${name}() 存在（/cs/* 接口）`)
}
const bases = api.getBases()
ok(bases.tool === '' && bases.cs === '', 'H5/node 默认 baseURL 为空（同源相对路径）')

console.log('[3/3] 模板引用一致性')
const chatVue = readFileSync(join(root, 'src', 'pages', 'chat', 'chat.vue'), 'utf8')
ok(chatVue.includes('note-table'), 'chat.vue 内嵌清单组件（小程序无 iframe）')
ok(chatVue.includes('csApi.listToken'), 'chat.vue 走 /cs/chat/{token}/list-token 取清单令牌')
const apiSrc = readFileSync(join(root, 'src', 'api.js'), 'utf8')
for (const ep of ['guest', 'photo', 'notes', 'auth/code', 'auth/verify', 'export.xlsx']) {
  ok(apiSrc.includes(`'${ep}'`), `工具页 API 覆盖 ${ep}`)
}
for (const ep of ['cs/chat/', 'message', '/lang', 'list-token', 'cs/link/']) {
  ok(apiSrc.includes(ep), `客服/清单 API 覆盖 ${ep}`)
}

if (failed) {
  console.error(`\n冒烟失败：${failed} 项未通过`)
  process.exit(1)
}
console.log('\n冒烟全部通过 ✓')
