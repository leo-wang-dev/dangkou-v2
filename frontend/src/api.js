// 统一 API 层（三页共用）。
// - H5：与旧 static/ 页一致走同源 fetch。工具页(user-app)接口用相对路径
//   （页面部署在 nginx /tool/ 下，前缀被剥掉后落到 user-app 根路由）；
//   客服/清单接口用 /cs/... 绝对路径（同源的商家运行时）。
// - 小程序：uni.request / uni.uploadFile / uni.downloadFile，
//   baseURL 走常量（可用 setBases() 运行时覆盖，存 storage，无需重编译）。
//
// 后端接口对照：
// - user-app（catalog/userapp.py，:19100）：POST /guest、POST /photo、POST /notes、
//   GET /notes/{id}/photo、GET /export.xlsx、POST /auth/code、POST /auth/verify、GET /me
// - 商家运行时（catalog/api.py，443→:8890）：POST /cs/chat/{token}/{message|photo|lang}、
//   GET /cs/chat/{token}/list-token、GET /cs/link/{k}、PATCH /cs/link/{k}/note/{id}、
//   GET /cs/link/{k}/export.xlsx
import { storage } from './storage.js'
import { getLanguage, translate } from './i18n.js'

// baseURL 默认值（小程序开发期指向测试服务器；上线前改成正式域名）
let DEFAULT_TOOL_BASE = ''  // 工具端：H5 同源相对路径
let DEFAULT_CS_BASE = ''    // 客服/清单：H5 同源绝对路径
// #ifdef MP-WEIXIN
if (typeof uni !== 'undefined') {  // node 冒烟导入时无 uni，退回同源默认
  DEFAULT_TOOL_BASE = 'https://134.175.135.102/tool'  // nginx location /tool/ 剥前缀 → user-app
  DEFAULT_CS_BASE = 'https://134.175.135.102'         // nginx 443 → 商家运行时
}
// #endif

let customerTenant = ''
export function setCustomerTenant(mid = '') {
  customerTenant = /^[a-f0-9]{24}$/.test(mid) ? mid : ''
}
export function customerSessionKey(token) {
  const base = getBases().cs
  return 'h5v:' + (base ? base + ':' : '') + token
}
function customerPrefix() {
  const path = typeof location !== 'undefined' ? (location.pathname || '') : ''
  const mounted = path.match(/^\/merchant\/customer\/[a-f0-9]{24}(?=\/|$)/)?.[0]
  return mounted || (customerTenant ? '/merchant/customer/' + customerTenant : '')
}

export function getBases() {
  let override = null
  try { override = JSON.parse(storage.get('dk_bases') || '') } catch (e) { override = null }
  return {
    tool: (override && override.tool) || DEFAULT_TOOL_BASE,
    cs: customerPrefix() ? (((override && override.cs) || DEFAULT_CS_BASE).replace(/\/merchant\/customer\/[a-f0-9]{24}\/?$/, '').replace(/\/+$/, '') + customerPrefix()) : ((override && override.cs) || DEFAULT_CS_BASE)
  }
}

// 运行时覆盖 baseURL（配置页/调试用）：{ tool: '...', cs: '...' }，传空串恢复默认
export function setBases(bases) {
  storage.set('dk_bases', JSON.stringify({ tool: (bases && bases.tool) || '', cs: (bases && bases.cs) || '' }))
}

function joinUrl(base, path) {
  if (!base) return /^\/?cs\//.test(path) ? '/' + path.replace(/^\/+/, '') : path
  return base.replace(/\/+$/, '') + '/' + String(path).replace(/^\/+/, '')
}

// uni-app rewrites root-relative image src against its H5 public path. Resolve
// browser image URLs fully before passing them to <image> under /tool/.
function assetUrl(url) {
  if(typeof location !== 'undefined' && location.href) return new URL(url,location.href).href
  return url
}

// 通用请求：返回统一形状 { ok, status, data }，不抛异常（页面自行 tip）
export async function request(path, opts = {}) {
  const { method = 'GET', data, base = '', headers = {} } = opts
  const url = joinUrl(base, path)
  const header = { 'X-Customer-Language':getLanguage(), ...headers }
  if (data !== undefined) header['Content-Type'] = 'application/json'
  // #ifdef H5
  try {
    const r = await fetch(url, {
      method,
      headers: header,
      body: data !== undefined ? JSON.stringify(data) : undefined
    })
    let d = null
    try { d = await r.json() } catch (e) { d = null }
    return { ok: r.ok, status: r.status, data: d }
  } catch (e) {
    return { ok: false, status: 0, data: null }
  }
  // #endif
  // #ifdef MP-WEIXIN
  return new Promise((resolve) => {
    uni.request({
      url,
      method: method.toUpperCase(),
      data,
      header,
      success: (res) => resolve({
        ok: res.statusCode >= 200 && res.statusCode < 300,
        status: res.statusCode,
        data: res.data
      }),
      fail: () => resolve({ ok: false, status: 0, data: null })
    })
  })
  // #endif
  // 两个条件块编译后必留其一；node 冒烟导入时不会执行到这
  return { ok: false, status: 0, data: null }
}

// 文件上传（H5 与小程序统一走 uni.uploadFile；filePath 来自 chooseImage/chooseMedia）
export function upload(path, filePath, formData = {}, opts = {}) {
  const { base = '', headers = {} } = opts
  const url = joinUrl(base, path)
  return new Promise((resolve) => {
    uni.uploadFile({
      url,
      filePath,
      name: 'file',
      formData,
      header: { 'X-Customer-Language':getLanguage(), ...headers },
      success: (res) => {
        let d = null
        try { d = JSON.parse(res.data) } catch (e) { d = null }
        resolve({ ok: res.statusCode >= 200 && res.statusCode < 300, status: res.statusCode, data: d })
      },
      fail: () => resolve({ ok: false, status: 0, data: null })
    })
  })
}

// 文件下载（导出 Excel）：
// - H5：fetch blob + <a download>（同旧页）
// - 小程序：uni.downloadFile + uni.openDocument
export async function downloadFile(path, opts = {}) {
  const { base = '', headers = {}, fallbackName = 'download.xlsx' } = opts
  const url = joinUrl(base, path)
  // #ifdef H5
  try {
    const r = await fetch(url, { headers: { 'X-Customer-Language':getLanguage(), ...headers } })
    if (!r.ok) {
      let d = null
      try { d = await r.json() } catch (e) { d = null }
      return { ok: false, error: (d && d.detail) || translate('exportError') }
    }
    const blob = await r.blob()
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = fallbackName
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(a.href), 5000)
    return { ok: true }
  } catch (e) {
    return { ok: false, error: translate('networkError') }
  }
  // #endif
  // #ifdef MP-WEIXIN
  return new Promise((resolve) => {
    uni.downloadFile({
      url,
      header: { 'X-Customer-Language':getLanguage(), ...headers },
      success: (res) => {
        if (res.statusCode !== 200) return resolve({ ok: false, error: translate('exportError') })
        uni.openDocument({
          filePath: res.tempFilePath,
          fileType: 'xlsx',
          showMenu: true,
          success: () => resolve({ ok: true }),
          fail: () => resolve({ ok: false, error: translate('exportError') })
        })
      },
      fail: () => resolve({ ok: false, error: translate('exportError') })
    })
  })
  // #endif
}

// 拍照/相册选图（工具页与客服页共用）：
// 小程序优先 chooseMedia（chooseImage 已停止维护），其余端走 chooseImage（H5 通吃）。
export function choosePhoto() {
  return new Promise((resolve, reject) => {
    // #ifdef MP-WEIXIN
    if (typeof uni.chooseMedia === 'function') {
      uni.chooseMedia({
        count: 1,
        mediaType: ['image'],
        sizeType: ['compressed'],
        sourceType: ['camera', 'album'],
        success: (r) => resolve(r.tempFiles && r.tempFiles[0] && r.tempFiles[0].tempFilePath),
        fail: reject
      })
      return
    }
    // #endif
    uni.chooseImage({
      count: 1,
      sizeType: ['compressed'],
      sourceType: ['camera', 'album'],
      success: (r) => resolve(r.tempFilePaths && r.tempFilePaths[0]),
      fail: reject
    })
  })
}

// ---------- 工具端（user-app）API ----------

function toolAuthHeaders() {
  const token = storage.get('ut_token')
  return token ? { Authorization: 'Bearer ' + token } : {}
}

function toolQuery() {
  const token = storage.get('ut_token')
  const guest = storage.get('ut_guest')
  if (token) return '?token=' + encodeURIComponent(token)
  return '?guest=' + encodeURIComponent(guest)
}

export const toolApi = {
  setLang: lang=>request('lang',{method:'POST',base:getBases().tool,headers:toolAuthHeaders(),data:{lang,guest:storage.get('ut_guest')}}),
  endSession: () => request('session/end', { method:'POST', base:getBases().tool, headers:toolAuthHeaders(), data:{guest:storage.get('ut_guest')} }),
  confirmBatch: (batch_id, note_ids = [], fields = null, action = 'confirm') => request((fields ? 'batches' : 'batches/confirm') + toolQuery(), { method:'POST',base:getBases().tool,headers:toolAuthHeaders(),data:{batch_id,note_ids,fields,action} }),
  newGuest: () => request('guest', { method: 'POST', base: getBases().tool }),
  sendCode: (email) => request('auth/code', { method: 'POST', base: getBases().tool, data: { email } }),
  verify: (email, code, guest) =>
    request('auth/verify', { method: 'POST', base: getBases().tool, data: { email, code, guest } }),
  me: () => request('me', { base: getBases().tool, headers: toolAuthHeaders() }),
  uploadPhoto: (filePath) =>
    upload('photo', filePath, { owner: storage.get('ut_guest') }, { base: getBases().tool, headers: toolAuthHeaders() }),
  notes: () => request('notes' + toolQuery(), { method: 'POST', base: getBases().tool }),
  photoUrl: (id) => assetUrl(joinUrl(getBases().tool, 'notes/' + id + '/photo' + toolQuery())),
  exportXlsx: () => downloadFile('export.xlsx' + toolQuery(), {
    base: getBases().tool, headers: toolAuthHeaders(), fallbackName: '拍照清单.xlsx'
  })
}

// ---------- 客服 / 清单（商家运行时）API ----------

export const csApi = {
  newSession: (token) => request('cs/chat/'+token+'/session',{method:'POST',base:getBases().cs,headers:toolAuthHeaders()}),
  session: (token,visitor) => request('cs/chat/'+token+'/session?visitor='+encodeURIComponent(visitor),{base:getBases().cs,headers:toolAuthHeaders()}),
  claim: (token,visitor) => request('cs/chat/'+token+'/session/claim',{method:'POST',base:getBases().cs,headers:toolAuthHeaders(),data:{visitor}}),
  history: (token,visitor,before=0) => request('cs/chat/'+token+'/history?visitor='+encodeURIComponent(visitor)+'&before='+before,{base:getBases().cs,headers:toolAuthHeaders()}),
  endSession: (token,visitor) => request('cs/chat/'+token+'/session/end',{method:'POST',base:getBases().cs,headers:toolAuthHeaders(),data:{visitor}}),
  discardPhoto: (token,visitor) => request('cs/chat/'+token+'/pending-photo/discard',{method:'POST',base:getBases().cs,headers:toolAuthHeaders(),data:{visitor}}),
  setMode: (token,visitor,mode) => request('cs/chat/'+token+'/mode',{method:'POST',base:getBases().cs,headers:toolAuthHeaders(),data:{visitor,mode}}),
  confirmBatch: (token,visitor,batch_id,note_ids=[],fields=null,action='confirm') => request('cs/chat/'+token+'/batches/confirm',{method:'POST',base:getBases().cs,headers:toolAuthHeaders(),data:{visitor,batch_id,note_ids,fields,action}}),
  send: (token, text, visitor, action) =>
    request('cs/chat/' + token + '/message', { method: 'POST', base: getBases().cs, headers:toolAuthHeaders(), data: { text, visitor, action } }),
  uploadPhoto: (token, visitor, filePath) =>
    upload('cs/chat/' + token + '/photo', filePath, { visitor }, { base: getBases().cs,headers:toolAuthHeaders() }),
  setLang: (token, lang, visitor) =>
    request('cs/chat/' + token + '/lang', { method: 'POST', base: getBases().cs,headers:toolAuthHeaders(), data: { lang, visitor } }),
  listToken: (token, visitor) =>
    request('cs/chat/' + token + '/list-token?visitor=' + encodeURIComponent(visitor), { base: getBases().cs,headers:toolAuthHeaders() }),
  linkNotes: (k) => request('cs/link/' + k, { base: getBases().cs,headers:toolAuthHeaders() }),
  editNote: (k, noteId, field, value) =>
    request('cs/link/' + k + '/note/' + noteId, { method: 'PATCH', base: getBases().cs,headers:toolAuthHeaders(), data: { field, value } }),
  photoUrl: (p) => assetUrl(joinUrl(getBases().cs, p)),
  exportXlsx: (k) => downloadFile('cs/link/' + k + '/export.xlsx', {
    base: getBases().cs,headers:toolAuthHeaders(), fallbackName: '采购清单.xlsx'
  })
}
