# 微信档口管理与TG客户Bot

当前入口以本文件为准。商家身份由人工确认并部署独立档口服务器；商家继续使用微信Bot和原H5商品管理。历史Telegram商家Hub不参与本模式配置、绑定和资料同步。

## 商家流程

1. 在微信告诉档口助手老板微信号或TG用户名，打开原H5审批卡确认。至少有一种联系方式，客户转人工才有去处。
2. 商品管理→开通客户Telegram Bot，查看图文教程。在官方@BotFather发送/newbot，创建商家自己的客户Bot。
3. 把完整Token粘贴到已授权的档口微信会话。代码验证Bot身份、是否已有绑定或其他Webhook；保存后由单店守护进程启动。
4. 商品页查看“运行中”，客户扫描该Bot链接/二维码。绑定保存与实际运行分开显示。
5. 商品添加、Excel导入、修改、下架沿用原微信工具与H5审批。红线通过微信对话提出并审批；未填写不生效，可清空，旧工单不能覆盖新规则。

客户咨询、发照片/规格/数量→本档口商品匹配与采购笔记→按该店已批准规则回答；触线发老板联系方式；确认/出表生成采购Excel。

## 配置

侧车：WECHAT_CUSTOMER_BOT_ENABLED=1、WECHAT_MERCHANT_OWNER_IDS=人工确认微信ID、WECHAT_CUSTOMER_STATE=独立目录、WECHAT_RESERVED_TG_BOT_IDS=其它服务正在使用的Bot ID。设置本店数据库/图片/通知服务；关闭MERCHANT_HUB_ENABLED。

引擎：DSH_WECHAT_DM_ALLOWLIST明确登记同一微信ID；WECHAT_CUSTOMER_BOT_ENABLED=1、CATALOG_V2_URL、CATALOG_V2_SERVICE_TOKEN、CATALOG_V2_PLUGIN_PATH绝对路径。新Token先于模型/检索/会话存储拦截，不作为普通工具参数。CATALOG_NOTIFY_TOKEN/PORT启用受限微信审批通知。

客户守护：`scripts/run_wechat_customer.py`，校验凭据身份与已提交档口一致，只启动本店客户worker，不回写商家资料。Token更新后重启worker；换Bot身份需要显式迁移旧消息记录。

## 本次隔离手测

服务器134.175.135.102，目录/home/ubuntu/dangkou-wechat-test与/home/ubuntu/dsh-wechat-test。四个dangkou-wechat-test-*服务已启动。数据库仅两款带图测试商品；不会混入原档口客户会话。

微信管理服务仅监听127.0.0.1:17615，登录需实际微信扫码。二维码是微信授权登录码，不是普通好友二维码。未登录前allowlist为拒绝占位身份；登录确认后，由开通人员将返回的微信身份登记到引擎与侧车并重启测试实例。

手测顺序：扫码授权→登记管理员→微信发“看一下商品”→补老板联系方式并审批→按教程创建TG Bot并把Token发微信→商品页运行中→客户TG /start→发图+规格数量→微信配置加急转人工并审批→客户询问加急收到联系方式→出表检查商品行和图片。

真实TG网络验收需要商家Bot Token及客户先发/start建立会话；未完成前不能把自动测试通过等同真实渠道全链路完成。
