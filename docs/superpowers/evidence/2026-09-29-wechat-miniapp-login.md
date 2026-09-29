# 微信小程序登录接入验证

日期：2026-09-29。工作分支：`dangkou-v2`。隔离服务器：`134.175.135.102`，中央工具服务 `dangkou-h5-trial-tool`。

## 已验证

- 后端全量 `python -m pytest -q`：737 passed，4 skipped（新增交换凭证单元测试随后以聚焦测试通过）。
- 新增微信用例：7 passed，覆盖游客中央笔记合并、重复登录、已登录邮箱主动绑定、绑定冲突、微信账号经邮箱验证码合并、保留内部邮箱拒绝 OTP、服务端换码结果不暴露 `session_key`。
- 前端 `npm test`：原 31 条会话行为测试和多语言检查通过；微信 `uni.login` code 获取与 API 请求行为 3 条通过。
- uni-app `build:mp-weixin`、`build:h5` 均成功。
- 隔离服务器更新中央服务后 `systemctl is-active dangkou-h5-trial-tool` 返回 `active`。公开 `GET https://134.175.135.102:80/tool/` 返回 200；公开 `POST /tool/auth/wechat` 使用探测 code 返回 503，正文为 `微信小程序登录尚未配置`。该响应验证路由已接入且无凭据时明确拒绝。

## 尚未完成的外部联调

- 隔离服务器尚无 `WECHAT_MINIAPP_APP_ID` 与 `WECHAT_MINIAPP_APP_SECRET`，`frontend/src/manifest.json` 的微信 AppID 仍为空。没有进行真实微信换码或真机点击，因此不能声称微信登录已经端到端通过。
- 当前小程序 API 默认指向 IP 测试地址。正式体验版须配置微信后台允许的 HTTPS 域名、证书、AppID 与 AppSecret 后重建，并在真机验证登录、绑定、拍照及跨设备恢复。
- 微信独立账号和已有邮箱账号在同一档口各有历史时，中央笔记已合并，档口历史的双账号合并待开发方案阶段 C 完成。常规游客→微信、邮箱→绑定微信使用同一 `account_id`。
