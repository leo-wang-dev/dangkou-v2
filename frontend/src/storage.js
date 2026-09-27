// localStorage → uni.setStorageSync 等价封装。
// H5 端 uni 底层即 localStorage（字符串原样存），键名与旧 static/ 页保持一致
// （ut_guest / ut_token / ut_email / h5v），共用域名部署时旧页身份可无缝带过来；
// 小程序端落到本地缓存。模块顶层不触碰 uni，保证可被 node 直接 import 做冒烟。
export const storage = {
  get(key, fallback = '') {
    try {
      const v = uni.getStorageSync(key)
      return (v === '' || v === null || v === undefined) ? fallback : v
    } catch (e) {
      return fallback
    }
  },
  set(key, value) {
    try { uni.setStorageSync(key, value) } catch (e) { /* 存储满等异常忽略 */ }
  },
  remove(key) {
    try { uni.removeStorageSync(key) } catch (e) { /* 同上 */ }
  }
}
