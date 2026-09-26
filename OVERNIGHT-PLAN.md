# 通宵任务单 2026-09-24（老板睡觉，自主完成）

## 环境速查
- 仓库：`/Users/elias/code/eryuan/dangkou-v2`（分支 dangkou-v2，每特性一提交并 push origin）
- 服务器：`sshpass -p temp123 ssh ubuntu@134.175.135.102`（连接过频锁 30-75s，等）
- 部署目录 `~/dangkou-wechat-test`；服务 `dangkou-wechat-test-api`(:19010)/`-engine`/`-customer`/`-notifications`
- 部署法：`tar cf - <files> | ssh 'cd dangkou-wechat-test && tar xf - && sudo systemctl restart dangkou-wechat-test-api(及涉及服务)'`
- 引擎插件：`engine-plugin/catalog-v2.mjs`（随 api 目录部署）+ persona 在 `~/dsh-wechat-test/plugins/lib/catalog-plugin.mjs`（服务器直改）
- 验收底线：`python3 -m pytest tests/ -q --ignore=tests/e2e` 全绿；生产 curl 验证；不 reset 生产数据
- 纪律（老板原话）：不写死分类代码、不做关键词匹配（AI 判断）、写操作全过审批、H5 本版只做中英文

## 剩余任务（按序）
### B. C端 TG→H5（最重）
1. 读 `catalog/wechat_customer.py`、`static/cs/list.html`、`csbot.py` 复用面
2. 新 H5 对话页 `static/cs/chat.html`：文字+拍照上传、清单卡（整理好请核对/改价/确认/出表）、语言选择(中/英)、找老板入口（老板联系方式+淘肯链接提示）
3. 后端：H5 会话 API（复用 CsBot 内核，一次性 token 鉴权同 cs_link；POST /cs/chat/{token}）
4. 管理页绑定入口改 H5 链接；TG 相关服务停用不删（dangkou-wechat-test-customer 停）
5. 测试+部署+生产验证
### A2. 名片识别+导出列
- 照片流里检测名片（qwen3-vl-plus 已有 chat_vision）→ 抽取档口名/联系人/电话/地址 → 存 cs_note 批次 shop_info → `cs_export.render_notes` 用它替换硬填的店铺资料；导出删"确认状态"列；"起订量"→"装箱数"
### C1. 一图多商品分割
- `csbot` 拍照抽取提示词让 qwen3-vl-plus 同时返回每商品 bounding box（归一化坐标）→ PIL 裁剪子图存 `img_dir` → 每条笔记挂子图；无坐标回落整图
### 收尾
- 轮询日志带 str(exc)（scripts/run_cs_bot.py:78）
- 全部提交推送；晨报：完成项/验证结果/遗留

## 已完成（今日）
- A1 子代理解析 `0231329`（吹风机18/18）；D2/D3 供应商+统计 `0223d40`（429绿）
- 吹风机脏数据已清；待批工单 #31(mutate)#38(手工建分类)是老板的

## B 的实现设计（已调研）
- cs_link 令牌体系复用：`/cs/link/{token}` 已按 customer 解析（api.py:1363 起 GET/PATCH/export 全套）
- 新增 `GET /cs/chat/{token}` → static/cs/chat.html（对话式 UI：气泡消息、照片上传、清单卡、语言选择中/英、找老板按钮）
- 新增 `POST /cs/chat/{token}/message` {text} → 解析 customer 行 → 走 CsBot._on_text 等价流程（merchant_policy.answer + 拍照抽取）→ 返回 {reply}
- 新增 `POST /cs/chat/{token}/photo` (multipart) → bytes 走 chat_vision 抽取 → 回执文本
- CsBot 实例化 api=None（回复直接 return，不 enqueue tg）；_enqueue('tg'...) 的路径要注意——H5 模式下 reply 直接返回，出站不进 tg 队列（给 CsBot 加个 reply_mode='http' 或直接用 merchant_policy.answer + csbot 抽取函数）
- 页面静态资源直接放 static/cs/，nginx 已放行 /cs/
- TG 服务 dangkou-wechat-test-customer 停用（sudo systemctl disable --now），管理页绑定入口文案改 H5 链接

## B 落地细节（已定稿）
- csbot._prepare_photo(cust, msg, data=None)：data 直传字节（H5 用），msg 仍走 api.download_photo
- catalog/cs_chat.py：class H5Bot(CsBot) 覆写 _enqueue——tg/tg_document/tg_photo 直接丢弃（H5 出表走页面 export.xlsx 接口），notify 渠道照旧（商家微信收通知）
- 会话身份：一个店一个稳定入口 token——shop_profile 加列 chat_token（迁移仿 supplier 列）；管理页按钮生成/复制 `https://<PUBLIC>/cs/chat/<token>`；访客开页生成 visitor id（localStorage）→ _ensure_customer({'id':'h5-<hex>'}) → tg_id 唯一
- api.py 路由：POST /cs/chat-token（服务令牌鉴权，生成/返回）；GET /cs/chat/{token} 出 chat.html；POST /cs/chat/{token}/lang {lang}；POST .../message {text,visitor}→_on_text；POST .../photo (multipart file+visitor)→_prepare_photo(data=bytes)+_on_photo(prepared=)
- 页面：气泡对话+拍照上传+清单卡链接（/cs/link 由出表回复里带）+语言按钮(中文/English)+找老板按钮(发"找老板")
- 服务器：sudo systemctl disable --now dangkou-wechat-test-customer（TG 下线）；nginx 无需动（/cs/ 由 FastAPI 同前缀出）

## A2 落地点（已查）
- 起订量：csbot.py 3处提示词（EXTRACT/REVIEW 字段清单）删"起订量"（7→6字段）；cs_export.render_notes 过滤 key='起订量'（历史数据兜底）
- 确认状态：render_notes 的 include_status 块删掉（列不再出现，参数保留兼容）
- 名片：EXTRACT_PROMPT 加"若照片含名片，额外输出一条 {"名片":{档口名称/联系人/电话/地址}}"；_on_photo 检测到名片项→不建笔记，UPSERT 新表 cs_card_info(customer_id,fields_json)（迁移仿 supplier）；customer_fields/render_notes 的档口列优先取名片信息（shop_link.customer_fields 处注入或 render 层覆盖）
## C1 落地点
- EXTRACT_PROMPT 要求每商品带 bbox [x1,y1,x2,y2] 归一化0-1000；_prepare_photo 解析后 PIL 裁剪存 <fname>_i.jpg；_on_photo 每条笔记挂对应子图路径，无 bbox 挂整图（现状）
