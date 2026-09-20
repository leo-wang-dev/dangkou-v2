# dangkou-v2

当前商家入口：微信 Bot / H5；客户入口：商家自建 TG Bot。人工开通独立档口后，在微信提交 Token 开通。以 [微信档口与TG客户Bot接入](docs/微信档口与TG客户Bot接入.md) 为准；下方早期价格公共红线和 TG 商家 Hub 说明属于历史模式，不适用于微信管理模式。

档口端通过引擎插件调用商品侧车；采购端使用独立 Telegram bot。Python 3.11+（本次复测 3.12.14）。

## 本地安装与检查

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt
.venv/bin/python -m playwright install chromium
cp .env.example .env
.venv/bin/python scripts/preflight.py
```

`requirements.txt` 为运行依赖，`requirements-dev.txt` 为测试依赖；`requirements-lock.txt` 记录本轮实际测试的完整版本。`.env` 不提交。报价模板由 `CATALOG_QUOTE_TEMPLATE` 指定。按本次用户授权，离线测试自动生成带“测试模板、不可付款”标记的 14 列模板，不自动写入生产 data。也可运行 `.venv/bin/python scripts/build_test_quote_template.py` 生成可查看样例；正式使用时换成商家确认的模板。

```bash
.venv/bin/python -m uvicorn catalog.main:app --host 127.0.0.1 --port 8890
CATALOG_NOTIFY_WORKER=1 .venv/bin/python scripts/run_cs_bot.py
.venv/bin/python scripts/run_notifications.py
.venv/bin/python scripts/rebuild_search_index.py
```

生产通过 HTTPS 反向代理开放侧车，配置 `CATALOG_V2_PUBLIC_URL`。`/health` 只表示进程活着，不能证明可上线；带 `X-Service-Token` 请求 `/ready` 查看缺失项。未配置服务令牌时管理接口拒绝访问。客户清单和审批卡使用各自凭证。客户链接是可转发的持有者凭证，有效期两天；旧库里没有到期时间的链接会被拒绝，需重新生成。

## 老板 TG 与微信

`shop_profile` 保存老板 TG、微信，以及 address（地址）、business_hours（营业时间）、shipping_info（物流）、faq（常见问答）。这些字段均经工单审批才生效；常见店址、时间、物流问题按已批准原文回答，缺资料则引导联系老板。插件已提供 `shop_contact_get`、`shop_contact_set`，商家可直接说“老板 TG 是 @xxx，微信是 xxx”。工具创建工单，打开商家页面确认后生效。bot 转人工时发出已配置的 TG Username/链接与微信号，客户自行添加老板；没有自动把老板加入 bot 原会话。

联系方式需要先由商家填写并审批，`/ready` 才可通过该项检查。不要用真实客户去验证空联系方式的店铺。红线是商家维护的自然语言规则：微信管理模式初始为空，只有商家通过微信提交并批准的店级或商品级规则才生效，也可以明确清空。平台不会从参考问题中自动生成红线。

阶梯报价计算、录入和界面已移除；可观测仅决定商品是否对客户可见，不要求价格档位。旧数据库的历史阶梯列只保留兼容数据，不读取参与报价、不展示、不接受新增或审批写入。TG 商品介绍、商品查询和采购笔记不对客户展示商家成本、价格或历史阶梯数据。

价格问题是否转人工只按商家已批准红线判断。例如商家只设置“讲价或数量大于 200 时问价转人工”，普通问价不会被扩大为红线；现有资料没有可公开答案时明确说明资料不足，不编造报价。客户主动回复“找老板”仍可取得老板联系方式。照片的原始价签只作为待核对采购记录，不能当作本店确认报价；拍照整理、非价格字段补改及出表继续保留。

## 导入范围与消息恢复

导入 `source_key` 是稳定的供应商/商品表来源标识，同一来源重导使用相同值；不同来源隔离。未提供时使用文件名，因此不同供应商同名文件必须显式区分来源。同型号多行按行匹配，不再覆盖成一行；仅换图片按文件内容识别。新导入工单保存来源商品快照，批准前校验；过期工单返回 409，需驳回后重新导入。逐行审批会更新本工单快照，不影响下一行操作。既有来源按 import_doc.filename 迁移；没有来源的手工商品不会被导入下架。

TG 批次先存 `cs_inbox` 再推进 offset，处理成功与回复入队一起提交。`cs_outbox` 保存完整回复和转人工/红线审批通知，以及 B 端导入提醒和报价文件任务，失败后重试；同客户后续回复不越过失败的前一条。仅启动一个 bot 进程（入口带文件锁）。发送后、标记成功前的极短崩溃窗口仍可能重复投递，不能宣称网络发送 exactly-once。消息目前串行处理，失败按客户顺序退避重试，读取积压采用有界批次；尚未做真实负载验收。设置 `CATALOG_NOTIFY_WORKER=1` 后由独立通知进程消费商家消息，bot 只发 TG；必须同时启动通知进程。导入通知只持久化任务数据，发送时拼装链接。独立索引进程重试失败嵌入，失败次数与下次执行时间持久化。

## 验证

```bash
.venv/bin/python -m pytest tests/ --ignore=tests/e2e -q
.venv/bin/python -m pytest audit/test_browser_round2.py audit/test_round3_pages.py audit/test_round4_pages.py tests/e2e/test_pages.py -q
node tests/test_plugin_syntax.mjs
.venv/bin/python harness/run_cs.py
```

本次按实拍图开发与验证见 [第四轮修复与样例验收](audit/第四轮修复与实拍样例验证-20260918.md)。此前的 42 项需求对照见 [完整测试与代码审计](audit/第三轮完整测试与代码审计-20260918.md)。历史修复记录见 [修复与复测报告](audit/修复与复测报告-20260918.md)。真实模型测试需要 BAILIAN_API_KEY 和原始 CS_TEST_PHOTO；真实 TG 测试还需要 TG_BOT_TOKEN、TG_TEST_CHAT_ID、RUN_REAL_TG=1，仅使用独立测试 bot，避免与常驻进程抢轮询。缺配置的测试标记 BLOCKED，Harness 返回非零。

## 部署与恢复

`DEPLOY_HOST=user@server bash deploy/deploy.sh` 使用 SSH key，保留 `.env`、`data`、`.venv`，预检通过后停写并备份默认 data 目录，安装 sidecar、bot、通知和索引修复四个服务。脚本不修改实际引擎的 host.mjs，需要按目标引擎配置注册本仓插件；本轮只验证了插件 apply 契约与部署脚本语法，没有在真实服务器执行。

推荐将数据库、图片、客户照片和模板外置到持久目录。外置目录需自行纳入备份，当前脚本归档范围仅默认 data。上线前必须演练旧库迁移和恢复：停止四个服务，保存失败现场，将选定备份恢复至原路径，恢复匹配代码版本，再运行预检及数据核对后启动。不要把“已生成备份”当作“恢复演练完成”。

## 实拍图回放与真实模型验证

四张用户实拍图的目视参考在 `tests/fixtures/customer_photos.json`。未标明用途的 96/144/192 不擅自记为箱数，模糊价格需确认。

```bash
.venv/bin/python scripts/verify_customer_photos.py --out /tmp/dangkou-photo-replay-new
# 使用已有 BAILIAN 配置验证真实模型；上面的默认模式是参考数据回放。
.venv/bin/python scripts/verify_customer_photos.py --real-model --out /tmp/dangkou-photo-model-new
```

可通过 `CS_SAMPLE_PHOTO_DIR` 指向素材目录，文件名与清单一致。每次使用新的输出目录，避免旧数据库混入。脚本启动本地 Telegram 协议服务，不对真实客户发送消息。真实 TG 测试另需指定获准的测试会话。

## Excel 解析隔离

解析改为容器执行，输入单个 xlsx 只读挂载、独立工作目录可写、只传解析专用模型凭证；没有镜像配置会明确拒绝，不能回退到服务用户直接执行。模型网络访问仍需配置，容器不是模型正确性的保证。

```bash
docker build --build-arg CLAUDE_CODE_VERSION=2.1.236 -f deploy/Dockerfile.agent -t dangkou-parser:2.1.236 .
# 写入自己的 .env：CATALOG_AGENT_CONTAINER_IMAGE=dangkou-parser:2.1.236
```

本机已构建该镜像并做只读/非 root/输出目录冒烟测试。服务器也需构建或拉取经确认的镜像并赋予受控运行权限；不应开放任意用户访问 Docker socket。

最新真实百炼四图测试、实际 Excel 和修复记录见 [真实四图验证](audit/百炼真实四图识别与修复-20260918.md)。图片处理含主体/价签复核，两遍价格不一致或有模糊时不输出确定值；模型持续空响应交由收件队列重试。

## 档口端与 TG 客户端共用服务

当前采用**一家档口一个数据库、一套服务、一个 TG bot**。`shop_profile.shop_id` 是生成后不再改变的档口身份。两类商品均关联该 ID，历史商品迁移时保留内容和状态，新商品自动归属本库档口。不能把不同档口的数据和 bot 共用一个数据库。

通过商家插件 `shop_contact_set` 补充 `shop_name`（档口名称）、`stall_no`（档口号）、`contact_name`、老板 TG/微信等资料，并提交 `tg_bot_id`（机器人数字 ID）和可选 `tg_bot_username`。所有修改仍需商家审批；`tg_bot_id` 不是老板账号，也不是 Token。已经绑定的 bot ID 不可直接替换，避免复用另一机器人的消息 offset。两个并行工单修改同一字段时，过期工单不能覆盖新值。

商家 API 与 `scripts/run_cs_bot.py` 必须设置同一个绝对路径 `CATALOG_V2_DB`；服务鉴权与 bot Token 留在本机 `.env`，不写入店铺资料。bot 启动会调用 `getMe` 核对审批过的 bot ID；档口名称/绑定缺失或 ID 不一致时拒绝收发。商家 API 不依赖这项启动校验，仍可先启动补资料、审批。

服务器客户端还必须设置 `CATALOG_CS_API_URL` 指向原档口 API，并与 API 共用 `CATALOG_CS_SERVICE_TOKEN`（独立客户只读密钥）。`GET /cs/catalog` 和 `POST /cs/catalog/search` 仅提供已审批、对客可见的商品与匹配候选，过滤价格、历史阶梯价和内部备注；该密钥不能访问商家管理接口。商品目录、型号查询、采购笔记补全和照片找货走此接口，启动时再次核验接口返回的档口身份。接口失败不能回退到旧副本，也不能编造商品资料。

当前微信手测实例部署在 `/home/ubuntu/dangkou-wechat-test`，商家 API、客户 Bot、通知服务和微信引擎分别由 `dangkou-wechat-test-*` 服务运行。客户 Bot 通过私有接口实时读本档口商品；采购笔记有自己的客户数据表，商品库只负责补全，不限制未知商品进入笔记或 Excel。最终审计与实测结果见 [业务闭环综合审计](audit/round22/业务闭环综合审计-20260918.md)。

接入顺序：先在隔离本地库运行新版 API → 商家提交资料/绑定工单 → 审批 → 检查两端身份 → 再启动 bot。在配置好 `CATALOG_V2_DB` 和 `CATALOG_V2_SERVICE_TOKEN` 后，可执行：

```sh
.venv/bin/python scripts/check_shop_linkage.py --base-url http://127.0.0.1:8890
```

该命令只读对比本地 bot 数据库与商家 API 的档口身份/绑定，不迁移、不部署、不修改服务器。`GET /shop/linkage` 需服务鉴权，返回档口 ID、bot ID 和商品数量；生产仍需单独完成实际环境验收。

清单条目分别记录 `received_shop_id`（接待档口）、`source_shop_id`（本库档口来源引用）、`source_basis`（依据）。经绑定 bot 接收且没有外部供应商信息的新照片，自动带出本档口资料，依据标为“接待档口（供货关系待确认）”；这不代表照片就是本店在售商品，询价仍须匹配已审批、对客可见商品。照片里明确有外部供应商时保留外部信息。历史无依据条目不追溯猜填。

客户可在网页补改来源，或发送 `清单第1、2条 档口：本店` 明确确认本店来源，发送 `清单第1、2条 档口：外部供应商名称` 切换来源。切换到外部档口会解除来源 ID 并清除旧联系人/联系方式，避免串店；接待档口 ID 保留。页面与新导出会读取已审批的最新本店资料；已经进入发件箱的 Excel 使用请求时快照。外部档口目前以清单来源字段记录，并非跨商户商品库或供应商主数据平台。
