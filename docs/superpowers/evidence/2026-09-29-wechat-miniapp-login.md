# 微信小程序登录接入验证

日期：2026-09-29。工作分支：`dangkou-v2`。隔离服务器：`134.175.135.102`，中央工具服务 `dangkou-h5-trial-tool`。

## 已验证

- 后端全量 `python -m pytest -q`：739 passed，4 skipped（阶段 C 修改后重跑）。
- 新增微信用例：7 passed，覆盖游客中央笔记合并、重复登录、已登录邮箱主动绑定、绑定冲突、微信账号经邮箱验证码合并、保留内部邮箱拒绝 OTP、服务端换码结果不暴露 `session_key`。
- 前端 `npm test`：36 条会话/微信/发布构建行为测试及 13 语言检查通过；其中微信 `uni.login` code 获取与 API 请求行为 3 条通过。
- uni-app `build:mp-weixin`、`build:h5` 均成功。
- 使用占位 AppID 和示例 HTTPS 域名演练正式构建：生成 33 个文件的体验包，包含示例域名与 AppID，不含测试 IP；缺 AppID 或使用 IP 时构建提前拒绝。该占位包仅验证构建机制，不能发布。
- 隔离服务器更新中央服务后 `systemctl is-active dangkou-h5-trial-tool` 返回 `active`。公开 `GET https://134.175.135.102:80/tool/` 返回 200；公开 `POST /tool/auth/wechat` 使用探测 code 返回 503，正文为 `微信小程序登录尚未配置`。该响应验证路由已接入且无凭据时明确拒绝。
- 阶段 C：中央 `/me` 返回旧账号别名，档口首次访问按别名合并旧账号清单与聊天；双待处理照片时暂缓合并、保持当前账号可用并返回 `account_merge_pending=true`，处理后重试通过 HTTP 路由测试。隔离服务器的中央服务、档口 API 均更新后为 `active`。公开测试账号经 `/tool/auth/code` → `/tool/auth/verify` → `/tool/me` → `/merchant/customer/.../cs/chat/.../session` 请求，四步均返回 200，档口响应 `account=true`；公开客服页 200，已包含暂缓合并提示。测试时未打印验证码、bearer 或客服令牌。
- 公开探测完成后，仅删除本轮 `wx-merge-probe-*` 测试账号、会话与验证码；该账号在档口无笔记、无聊天记录。原有测试/业务记录未清空。

## 尚未完成的外部联调

- 隔离服务器尚无 `WECHAT_MINIAPP_APP_ID` 与 `WECHAT_MINIAPP_APP_SECRET`，`frontend/src/manifest.json` 的微信 AppID 仍为空。没有进行真实微信换码或真机点击，因此不能声称微信登录已经端到端通过。
- 当前小程序 API 默认指向 IP 测试地址。正式体验版须配置微信后台允许的 HTTPS 域名、证书、AppID 与 AppSecret 后重建，并在真机验证登录、绑定、拍照及跨设备恢复。
- 微信独立账号和已有邮箱账号在同一档口各有历史的合并代码已完成并在 HTTP 路由测试验证；真实微信账号跨两家独立档口的端到端测试仍等小程序凭据与合法域名。
