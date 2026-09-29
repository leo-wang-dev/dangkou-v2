# 华岳缺失商品校正与 uni-app 小程序交接（2026-09-29）

## 原表取值

本次核对的原文件 SHA-256 为 `d8f112a9b6e0a2cbbb71ea06358c80a5db596bb24304bbb8c192f8d01cacd675`，与测试服务 doc4 的上传源一致。

- 8118：Sheet1 第 11 行 G 列为 `57*3646`，第 12 行为 `57*36*46`，两行其他字段一致。第 11 行少了尺寸分隔符；采用第 12 行 `57*36*46`，价格两行均为 `28`。
- 8210：Sheet1 第 27 行 K 列为数值 `22`，第 28 行为不合法的价格字符串 `18.52.`，其余字段一致。采用第 27 行数值 `22`。这是本次原表人工校正，不改变通用导入器对同型号多行冲突的拒绝规则。

通过独立测试服务的 `POST /upload` 与 `POST /products/cat_5536d726e729/direct` 创建两款，每款保留两行原图，并记下来源 doc4、Sheet1、起始行。随后真实公网 `GET /products/cat_5536d726e729` 返回 43 款，两款均对客户可见；四张原图经 `GET /img/...` 均为 HTTP 200，字节数分别为 120092、102145、110683、118321；`GET /stats?category=cat_5536d726e729&full=true` 返回 `total=43`，`GET /ready` 返回 200。上传暂存图已清理。

## uni-app 状态

`frontend/` 已有工具、档口客服和清单三页的 uni-app Vue3 代码。当前公网 `/tool/` 仍是旧静态 H5；小程序代码使用同一套后端 API，未发布成微信正式小程序。发现小程序默认 API 地址原指向不可达的 443 端口；改为当前测试服务可达的 `https://134.175.135.102:80`，编译产物已核对包含该地址。

用与 `frontend/package-lock.json` SHA-256 一致的已安装依赖在隔离构建目录运行 `npm test`、`npm run build:h5`、`npm run build:mp-weixin`，全部通过；小程序编译器输出 `DONE Build complete`。可导入微信开发者工具的本机产物在 `frontend/dist/build/mp-weixin/`（构建产物不纳入 Git）。`src/manifest.json` 仍无用户的小程序 AppID，产物 `project.config.json` 是 `touristappid`；可用于开发工具预览，真机和发布还需填 AppID、把开发测试 IP 换为已配置的 HTTPS 业务域名，并在小程序后台配置合法域名。
