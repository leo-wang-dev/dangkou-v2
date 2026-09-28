// Anonymous credentials last only for the browser/app session. Old localStorage
// visitor strings are deliberately not imported as authorization.
const sessionMemory = new Map()
function transient(key) { return key === 'ut_guest' || key.startsWith('h5v') || key === 'dk_lang_done' }
// localStorage → uni.setStorageSync 等价封装。
// H5 端 uni 底层即 localStorage（字符串原样存），键名与旧 static/ 页保持一致
// （ut_guest / ut_token / ut_email / h5v），共用域名部署时旧页身份可无缝带过来；
// 小程序端落到本地缓存。模块顶层不触碰 uni，保证可被 node 直接 import 做冒烟。
export const storage = {
  get(key, fallback = '') {
    if (transient(key)) {
      try { return (typeof sessionStorage !== 'undefined' ? sessionStorage.getItem(key) : sessionMemory.get(key)) || fallback } catch (e) { return fallback }
    }
    try {
      const v = typeof localStorage !== 'undefined' ? localStorage.getItem(key) : uni.getStorageSync(key)
      return (v === '' || v === null || v === undefined) ? fallback : v
    } catch (e) {
      return fallback
    }
  },
  set(key, value) {
    if (transient(key)) {
      try { if (typeof sessionStorage !== 'undefined') sessionStorage.setItem(key, value); else sessionMemory.set(key, value) } catch (e) {}
      return
    }
    try { if(typeof localStorage !== 'undefined')localStorage.setItem(key,value);else uni.setStorageSync(key, value) } catch (e) { /* 存储满等异常忽略 */ }
  },
  remove(key) {
    if (transient(key)) {
      try { if (typeof sessionStorage !== 'undefined') sessionStorage.removeItem(key); sessionMemory.delete(key) } catch (e) {}
      return
    }
    try { if(typeof localStorage !== 'undefined')localStorage.removeItem(key);else uni.removeStorageSync(key) } catch (e) { /* 同上 */ }
  }
}
