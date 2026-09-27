# frontend —— C 端 uni-app（Vue3）双端前端

`static/` 下三个 H5 页面（工具 / 客服 / 清单）的 uni-app 迁移版：**同一套代码编译 H5 与微信小程序**。旧 `static/` 页不动、后端继续伺服，双轨过渡。

## 页面与后端接口对照

| 页面 | 旧页 | 后端 | 接口 |
|---|---|---|---|
| `pages/tool/tool` 工具+邮箱验证码登录 | static/tool/index.html | user-app :19100 | POST `/guest` `/photo` `/notes` `/auth/code` `/auth/verify`、GET `/notes/{id}/photo` `/export.xlsx` `/me` |
| `pages/chat/chat` 档口客服 | static/cs/chat.html | 商家运行时 | POST `/cs/chat/{token}/{message,photo,lang}`、GET `/cs/chat/{token}/list-token` |
| `pages/list/list` + `components/note-table.vue` 清单表格 | static/cs/list.html | 商家运行时 | GET `/cs/link/{k}`、PATCH `/cs/link/{k}/note/{id}`、GET `/cs/link/{k}/export.xlsx` |

清单抽屉：H5 与小程序统一用页内抽屉 + `note-table` 组件承接（小程序没有 iframe）。导出 Excel：H5 走 fetch blob + `<a download>`；小程序走 `uni.downloadFile` + `uni.openDocument`。图片上传统一 `uni.uploadFile`。

## 编译命令

```bash
cd frontend
npm install
npm run dev:h5            # 本地开发（H5）
npm run build:h5          # 产出 dist/build/h5（资源为 ./ 相对路径，可挂任意子路径）
npm run dev:mp-weixin     # 小程序开发编译（dist/dev/mp-weixin）
npm run build:mp-weixin   # 产出 dist/build/mp-weixin，微信开发者工具导入该目录
npm test                  # node 级冒烟（结构+API 层加载+引用一致性，无网络依赖）
```

## baseURL 配置

`src/api.js` 顶部常量，按条件编译分端默认值：

- **H5**：`tool`/`cs` base 均为空串 = 同源部署——工具页接口用相对路径（nginx `/tool/` 剥前缀后落 user-app 根路由，同旧页），客服/清单接口用 `/cs/...` 绝对路径。
- **小程序**：`tool = https://134.175.135.102/tool`、`cs = https://134.175.135.102`（开发期测试机）。上线前改成正式域名。

**运行时覆盖**（不用重编译）：工具页登录条内「服务器：… · 点击修改」可改两个 base，存 storage key `dk_bases`（JSON `{tool, cs}`）；直接 `uni.setStorageSync('dk_bases', ...)` 也行。留空即恢复默认。

## H5 部署注意

- 构建产物资源是相对路径（manifest `h5.router.base = "./"`），放任意子路径都能用；比如整个 `dist/build/h5` 挂到 user-app 的 `/tool/` 下。
- 入口/带参链接（hash 路由）：工具 `/#/pages/tool/tool`、客服 `/#/pages/chat/chat?token=<chat_token>`、清单 `/#/pages/list/list?k=<link_token>`（chat/list 页对旧路径 `/cs/chat/{token}`、`?k=` 做了 H5 兼容解析）。
- 本地 `npm run dev:h5` 直连远程后端时，跨域已由后端 CORS 适配放行（见下）。

## 小程序注意（遗留）

1. **appid**：`src/manifest.json` 的 `mp-weixin.appid` 为空，导入开发者工具后填入。
2. **合法域名**：正式包要求 request/uploadFile/downloadFile 域名为 HTTPS + 已备案域名（**IP 不行**），在 mp 后台配置；开发期已设 `urlCheck: false` 绕过。`https://134.175.135.102` 仅开发可用，上线必须换域名。
3. **PATCH**：清单单元格保存用 `wx.request PATCH`，需较新基础库（2.10.1+，2020 年后版本均可）。
4. **登录合规**：邮箱验证码登录收集邮箱，正式提审前需在小程序后台完成「用户隐私保护指引」声明（收集邮箱/照片），否则审核可能被拒；体验版/开发版不受影响。
5. **外链**：客服回复里的清单 URL 在小程序里不可直接打开（点击复制到剪贴板），清单请走页内「📋 我的清单」抽屉。

## 后端配套（已做的唯一适配）

C 端接口无 Cookie（身份=游客参数/链接 token），在 `catalog/userapp.py` 与 `catalog/api.py(register_routes)` 加了 `CORSMiddleware`（`allow_origins=['*']`、不带凭据），用于 uni-app H5 开发期跨域直连；同源旧页与商家管理页行为不变。其余后端零改动。

## 冒烟测试

`npm test`（`test.mjs`）：校验 pages/manifest 可解析、页面文件存在、`api.js`/`storage.js` 可被 node 直接加载且导出齐全（条件编译块在 node 下安全退化）、模板与 API 引用一致。真正的端到端验证走两端编译 + 开发者工具/浏览器。
