# 删除工单 · 2026-09-27（老板批准六项全删）

## 全局约束（每个子代理必须遵守）
1. 仓库 `/Users/elias/code/eryuan/dangkou-v2`，分支 dangkou-v2，**不许动 .env / data/ / workspace/**；
2. 只删自己工单范围的文件/分支，**不越界**（其他流的文件只读）；
3. 每步删完：`python3 -m pytest tests/ -q --ignore=tests/e2e` 必须全绿；被删功能的测试同步删除/改写，不留跳过壳；
4. 删功能=连提示词、注释、文档引用、死 import 一起清，不留"暂时保留"；
5. 提交信息中文、说明删了什么、为何安全；
6. 铁律不破：写操作全过审批；不写死分类；解析只用子代理。

## 删A：qwen 回落 + 语言砍中英
- **qwen 回落**：`catalog/dynamic_import.py` 的 `_map_rows`/`_map_rows_to_template` 兜底链拆除——商品阶段子代理失败（`_agent_rows` 返回 None）时**抛 ValueError('解析服务暂不可用，请稍后重试')**，不再静默回落；`catalog/ai_extract.py` 删 `map_rows`/`_ask_mapping`/`_CHUNK`（`infer_field_attributes`/`guess_supplier` 保留——模板阶段仍用 qwen 推断字段属性，这条不删）。build_ticket_payload 旧一阶段的 discover 行直落也改为子代理失败即报错。相关测试：改 agent-down 用例为断言 ValueError。
- **语言**：`catalog/cs_i18n.py` 的 `_LANGUAGE_ALIASES` 只留 中文/English 两组；LANGUAGE_PROMPT 只列这两个；detect/翻译机制保留（英文还用）；`static/cs/chat.html` 语言栏两个按钮不变；`tests` 里语言相关断言对齐。
- 不碰：csbot.py 的语言门逻辑（结构不变，只是候选语言变两个）。

## 删B：固定品类 razor/卷发棒
- `catalog/templates.py`：删 RAZOR/CURLER 模板与提示词，TEMPLATES 置空后相关引用处改为显式"无预置品类"；`catalog/agent.py` 删 build_prompt/parse 固定品类入口（保留 parse_dynamic 与 _run_container）；`catalog/ingest.py` 删 category 固定分支（phase=legacy 一并移除，/import 拒绝 legacy）；api.py 删 legacy 路由与 TEMPLATES 计数引用；前端 index.html 预置相关文案；schema 里 product_razor/product_curler 表**保留不 drop**（老数据），但代码不再引用；测试：razor/curler 专属用例删除，公共用例改动态分类夹具。
- 服务器库里有 legacy 分类行（剃须刀/卷发棒 status approved）——代码删除后这些行自然不被列出（storage=legacy 不再出现在任何模板列表），无需数据迁移。

## 删C：TG 客服全套
- 删文件：`catalog/tg.py`、`scripts/run_cs_bot.py`（TG 轮询器；其 flush_outbox 逻辑并入 notifications worker——见删D，本流先只删 TG 分支）、`catalog/wechat_customer.py`（TG bot 绑定/监督）；
- `catalog/csbot.py`：删 flush_outbox 的 tg/tg_document/tg_photo 分支、voice 提示分支、handle_update 的 TG 结构（保留 _on_text/_on_photo/_ensure_customer 等内核，测试假件 FakeApi 改为直调内核而非 handle_update）；
- `catalog/cs_chat.py` H5Bot._enqueue 的 tg 丢弃覆写可删（渠道没了）；api.py 的 wechat_customer.register 移除；
- 引擎侧 persona（服务器 ~/dsh-wechat-test/plugins/lib/catalog-plugin.mjs）BotFather/TG绑定段落改为"客服入口=管理页🎧链接"；engine-plugin/catalog-v2.mjs 无 TG 引用则不动；
- 服务器：`dangkou-wechat-test-customer`、`dangkou-cs-bot`（B端旧 bot，8908）已 disable，本轮 `systemctl disable --now` 确认+单元文件移除备案；`dangkou-vpn`（TG 代理）停用；
- 注意：**merchant_binding/merchant_onboarding（同事的多店开通）里的 TG token 绑定流程保留不动**——那是一店一实例架构的一部分，动它要 Henry 同意（老板已确认架构保留）。

## 删D：通知合一 + 清单页并入 H5
- **合一**：run_notifications.py 成为唯一出站投递进程（消费 cs_outbox 全部剩余渠道：notify/notify_file/notify_import）；csbot.flush_outbox 整体迁走或只留通知回调；引擎 catalog-notify HTTP 桥保留为唯一微信直发通道；CATALOG_NOTIFY_WORKER 分区开关删除；
- **清单并入 H5**：`static/cs/chat.html` 加"我的清单"面板（拉 /cs/link 数据需 token——方案：chat 会话的出表回复已给清单链接；在 H5 页内嵌 iframe 该链接或做同页清单视图+导出按钮，以现有 /cs/link/{token} 接口为后端），独立 `static/cs/list.html` 页退役（路由 /cs/link/{token} 的 GET HTML 改为重定向到 H5？——保留 JSON/export 接口，仅前端页合并）；api.py 相应调整；
- 测试：通知流、清单合并的用例改写；全量绿后部署。

## 执行顺序
A → B → C → D（同一工作树串行，每流一提交+推送）；全部完成后统一部署生产+冒烟+晨报式收尾报告。

## 追加工单（2026-09-27 夜，老板拍板：全做完）
- 状态说明：删B 曾被中断但已落盘大量半成品（templates.py 已删等），工作树 89 败/23 错——收编续完至全绿再提交。
- 删E：标签打印删除——static/index.html 的按钮/JS/打印样式全清（commit 2e66463 的内容整体回退该特性部分，保留 shareChat 修复）。
- 新F：通用报价单——纯代码生成（弃模板文件）：第1行=14列头（ITEM NO./PHOTO/DESCRIPTION/COLORS/PRICE/QUANTITY/TOTAL AMOUNT/PCS-CTN/CTNS/G.W-CTN/N.W-CTN/MEAS/T.G.W/T-CBM）→数据行（PHOTO 嵌图保留）→TOTAL/DEPOSIT/BALANCE 三行（预付款=总额×定金%、尾款=差额，写值不写公式）。复用 parse_ctn_spec/整箱计算/_add_photo/quote_map；入口仅微信 bot（catalog_quote→POST /quote 契约不变）；CATALOG_QUOTE_TEMPLATE 依赖移除；测试用动态分类夹具重写（旧 quote 测试全换）。
- 新G：中央用户服务+独立拍照工具——平台级新服务（非档口库）：users/邮箱验证码（发送=桩，明日接真渠道；验证码哈希+有效期入库）/游客ID/登录时 visitor→user 合并；工具=照片→AI抽取→清单→导出Excel，纯整理不连商品库（复用 csbot 的 EXTRACT_PROMPT/解析/复审/图框裁剪，但独立存储）；新 H5 页（原生栈）；新 systemd 服务+nginx 路径 /tool/。
- 客服按档口隔离：现有 /cs/chat/<店token> 已满足，仅核对管理页入口文案。
- 执行顺序：删B续完 → 删C → 删D → 新F → 新G → 全量部署+晨报。删E 由主线先做。
