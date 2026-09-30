# APP 登录接入现有档口网页

本次只增加档口网页登录方式。商品管理、修改、审核、图片、导入业务以及后台队列沿用现有实现；未改动 APP 的两个项目。

## 部署配置

在档口进程环境设置以下字段并重启服务：

```dotenv
CATALOG_APP_ACCESS_ENABLED=1
CATALOG_APP_ENTRY_SECRET=<独立生成的至少32字符随机凭证>
CATALOG_APP_PUBLIC_BASE_URL=https://shop.example.com:8443
CATALOG_APP_SHOP_ID=<当前档口稳定标识>
```

保留原 `CATALOG_V2_SERVICE_TOKEN`。接入凭证不得与服务总凭证相同，亦不得交给原生客户端或网页；只在可信 APP 后台与档口服务器之间配置。不同档口使用不同凭证。建议 SHOP_ID 使用 `shop_profile.shop_id` 的既有值，部署后保持稳定。

PUBLIC_BASE_URL 只接受 HTTPS origin，不接受路径、用户密码、查询串或片段，默认端口 443 和主机大小写会规范化。对外端口由反向代理转发到档口内部应用，无需公开内部进程端口。必须有可信、匹配域名的 TLS 证书。

此版 APP 接入仅支持独立域名根路径，不能直接挂到旧 `/merchant/manage/<id>/` 代理路径：旧代理仍按原凭证运作，新增 APP 会话未改成代理通用凭证。每档口优先独立域名，不以同域不同端口作为 Cookie 隔离边界。

新增表在 `db.init_db()` 中自动创建；迁移可重复执行，不修改商品或工单表。

## 后台申请进入链接

`POST /app-entry/tickets`

Header：`Authorization: Bearer <CATALOG_APP_ENTRY_SECRET>`

```json
{
  "actorId": "user-123",
  "upstreamSessionId": "app-session-456",
  "requestId": "unique-request-id"
}
```

三个字段均为 1–128 字符。接口只允许可信后台调用，拒绝携带浏览器 Origin / Sec-Fetch-Site 的请求；这不是替代凭证的网络隔离策略。可信后台必须先检查 APP 用户是否有权管理该档口。本接口不验证 APP 登录 Token，也不查询 APP 用户库。

成功响应：

```json
{
  "loginUrl": "https://shop.example.com:8443/app-entry/#code=<一次性随机票据>",
  "expiresIn": 60,
  "shopId": "shop-123"
}
```

`requestId` 用于关联此次签发，不代表接口会返回同一票据；重试会创建新的短时票据，每张只能兑换一次。返回链接不能写入访问日志、分析事件或长期存储。

## APP 打开网页

APP WebView 打开 `loginUrl`。档口引导页清理地址栏片段后调用同源 `POST /app-entry/exchange`，提交 `{ "code": "..." }`。此调用要求匹配 PUBLIC_BASE_URL 的 Origin。

兑换成功设置 `__Host-dangkou_app_session` Cookie：Secure、HttpOnly、SameSite=Lax、Path=/，有效期 8 小时。随后跳转现有 `/?app_session=1` 页面。页面保留原菜单与业务流程，只改变凭证使用方式，并在当前浏览器会话中记录 APP 模式。

页面在 APP 模式中忽略旧管理 Token，加载 `/app-entry/session` 获取 CSRF Token，所有管理请求携带 `X-Catalog-App: 1`，写请求额外携带 `X-CSRF-Token`。图片和下载走 Cookie，不拼接管理总 Token。普通微信链接 `?t=...` 仍可明确切回原凭证模式。

进入票据和会话仅以哈希保存。数据库写事务保证同一票据并发兑换只有一次成功。新增鉴权只覆盖管理网页使用的明确方法和路径，不授权 `/ready`、`/shop/linkage`、客户目录接口、导入入口或开通/运维接口。原服务凭证的访问能力不受影响。

## 会话与退出

- `GET /app-entry/session`：网页读取 `actorId`、`shopId`、`expiresAt` 和 `csrfToken`；仅用于当前 Cookie 会话。
- `POST /app-entry/logout`：Cookie + 匹配 Origin + X-CSRF-Token，撤销当前会话并清 Cookie。
- `POST /app-entry/revoke`：后台接入凭证鉴权，JSON 为 `{ "upstreamSessionId": "app-session-456" }`，撤销当前接入下该上游会话签发的所有票据和会话；重复调用安全。

APP 退出或用户被取消档口权限时，可信后台应调用 revoke。仅关闭 WebView 不会自动撤销服务器会话。如果对接方未通知，网页会话最多继续有效至 8 小时期限。

禁用接入或更换接入凭证、PUBLIC_BASE_URL、SHOP_ID 后，原票据和会话无法再被验证；需重启相关进程使新环境生效。禁用后不要重新使用曾泄露的旧凭证。

历史管理链接和历史工单 Token 沿用原有生命周期，本功能不会把它们改为用户会话绑定的凭证。拥有原有管理权限的人仍然拥有相应管理能力。

## 响应与失败处理

沿用项目现有 FastAPI 错误格式 `{ "detail": "说明" }`：401 为缺失或失效身份；403 为来源/CSRF/接口权限不匹配；422 为字段错误；503 为接入关闭或配置不完整。

进入票据过期、网络不确定或会话失效时，从 APP 重新申请链接。不要在前端重试兑换同一个已消费票据，也不要回退到旧服务总 Token。

反向代理应对登录接口配置合理的请求体上限和访问限流，并对 Authorization、Cookie 及敏感请求体脱敏。应用响应禁止缓存；票据只位于 URL 片段，仍不应由客户端日志记录。

## 上线与回退

1. 在测试档口配置独立凭证、HTTPS 域名和真实 SHOP_ID。
2. 对接方可信后台申请链接，在 Android/iOS WebView 验证进入、查看商品、修改提交、审核、上传、图片、下载、返回与后台恢复。
3. 验证原微信入口以及商品、审批、导入、通知均正常，再逐档口启用。
4. 回退可将功能开关关闭并重启；原微信鉴权继续使用。已完成的商品修改与审核是正常业务数据，不随关闭登录入口撤销。

自动化覆盖接口生命周期、并发消费、跨独立数据库隔离、Cookie/CSRF、旧凭证共存，以及真实 Chromium 中的新旧页面加载。移动端 WebView 和线上反向代理仍需对接验收；本次没有部署线上。
