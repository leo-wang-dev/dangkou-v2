// A fresh one-use code is requested for every attempt. H5 keeps email login.
export function wechatLoginCode() {
  return new Promise((resolve, reject) => {
    if (typeof uni === 'undefined' || !uni.login) {
      reject(new Error('WeChat login is unavailable'))
      return
    }
    uni.login({ provider: 'weixin',
      success: result => result?.code ? resolve(result.code) : reject(new Error('Missing WeChat code')),
      fail: reject })
  })
}
