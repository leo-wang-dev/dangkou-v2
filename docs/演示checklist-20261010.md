# 2026-10-10 9:40 演示 Runbook（106.55.20.61 第二家档口）

## 一到公司先跑（9:00-9:10）

```bash
ssh dangkou-leo 'bash ~/dangkou-v2/scripts/demo-morning-check.sh'
```
八项全 ✅ 即就绪（微信那行 ⚠️ 未绑定是正常的——下面第一步就是扫码）。

## 演示主线（9:10 开始）

1. **绑定微信（~1分钟）**：APP/门户进管理页 → 「绑定微信」→ 出二维码 → 手机微信扫码确认。
   - 扫完**不用点任何确认**——新引擎确认即自动落库（这是今晚更新的核心）。
   - 页面几秒内变"已绑定"。绑完可在页面发条消息给机器人验证回复。
   - ⚠️ 若扫码后提示"该微信已绑定其他机器人实例"：说明这个微信号还绑在别处（老服务器/其他实例），
     用**另一个没绑过的微信号**，或先在原来那边解绑。
2. **微信发报价表**：把 `科森报价表20260724(1).xlsx` 发给机器人 → 回复"解析已启动"。
   - **模板审批约 25 秒**出来；批准后商品解析 **约 8-16 分钟**（后台跑，不耽误演示别的）。
   - 所以：**9:15 左右就把表发出去**，解析期间演示下面的存量数据。
3. **存量商品演示（解析等待期间）**：库里已有昨晚导入好的同款 12 个商品（KZ-228 等，含图、含价格、
   供应商揭阳康泽电器）。可演示：商品管理页浏览、微信里问"一共有多少产品""KZ-228 有哪些规格"。
4. **正式报价单（~10秒）**：微信说"给 KZ-228 出报价单，数量 2000，定金 30%" → Excel 文件自动推回微信。
   （已验证：单价28.5、金额57000、定金17100、嵌商品图、14列通用格式。）
5. **C端中心客服 H5**：管理页复制"🎧客服链接"发给客户/自己手机打开 → 发消息（中文/English按钮）、
   拍照 → 8秒左右出"整理好了，请核对"清单 → 确认后可导出 Excel。
6. **APP 超级应用入口**：APP 发现页 → 档口管理助手 → WebView 自动登录进管理页
   （门户三条记录 secret 已逐一比对一致，昨晚票据流 18/18 通过）。

## 万一挂了怎么办

| 症状 | 处置 |
|---|---|
| 晨检某项 ❌ | `ssh dangkou-leo` 后 `sudo systemctl restart <服务名>` |
| 导入发出去没反应 | `sudo docker ps` 看有没有 dangkou-parser 容器在跑；解析慢是正常（8-16分钟）。卡死超20分钟：`sudo systemctl restart dangkou2` 后重发文件 |
| 机器人不回消息 | `journalctl -u dsh-engine -f` 看日志；确认 litellm 桥活着（晨检第4项） |
| 全清重来 | `sudo env RESET_DB=/home/ubuntu/dangkou-v2/data/catalog.db RESET_ENGINE_STATE=/home/ubuntu/dsh-engine/state RESET_ENGINE_SERVICE=dsh-engine ~/dangkou-v2/.venv/bin/python ~/dangkou-v2/scripts/reset_shop_data.py` |

## 现场架构一句话

APP(门户 139.199.210.39 出票) → WebView 登录管理页(106.55.20.61:443→8890)；
微信机器人 = 本机 dsh-engine(17605 管理口/17606 直发口, node22)；
LLM = 本机 litellm 桥(4000, glm-5.3→阿里dashscope) + BAILIAN(qwen 视觉/对话)；
解析 = docker 容器 dangkou-parser:2.1.236。
**注意：bigmodel(智谱) 全公司 key 余额耗尽（1113），全部流量已切到阿里云 dashscope；充值前别切回去。**

## 昨晚遗留的已知边界

- 商品阶段 agent 解析慢（8-16 分钟）且偶发字段键抄错——已加前缀回映射兜底 + 报价单价格文本容错。
- 同名 Sheet1 重导会合并进既有分类（干净库无此问题；重导同一张表是"更新"语义，属正常）。
- cs H5 `batches/confirm` 直接调会报 batch_not_found（页面内正常走查核对流不受影响）。
- userapp 的小程序登录 env（WECHAT_MINIAPP_APP_ID）未配置——APP 登录走门户侧，不受影响。
