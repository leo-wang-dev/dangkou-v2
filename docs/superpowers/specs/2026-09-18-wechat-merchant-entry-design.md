# 微信商家入口与独立TG客户Bot

用户授权：先总体调查、subagent交叉审计，方案无阻塞后修改、全流程测试并真实启动供手测。

## 唯一配置来源
人工确认并独立部署的档口数据库负责商品、店铺资料、红线。微信Bot与其H5管理；客户只访问商家自己注册的TG Bot。不通过TG商家hub创建身份或复制配置。

## 分工
1. 微信连接器在allowlist之后、recall/LLM/session之前识别Token并直接调用侧车绑定接口。错误不回显Token。
2. 侧车绑定接口认证服务令牌及人工登记owner_id；getMe验证身份、拒绝已有webhook和不同Bot覆盖；密钥原子0600文件保存。状态接口不返回密钥。
3. 单店supervisor读取绑定密钥启动run_cs_bot，沿用当前数据库/图片，Token更新重启worker；禁hub回写。
4. 商品CRUD不改；商品H5新增BotFather图文教程。微信原资料/红线工具继续走审批。TG每次读取当前批准的红线，清空支持、旧工单冲突拒绝；默认seed不算商家自填。
5. 配置/绑定与真正运行就绪分开显示，进程未就绪不声称已开通。

## 审计结果
两位subagent确认：当前微信cs_redline不被merchant_policy读取、hub runtime覆盖微信资料、原文Token会进recall和LLM、空allowlist默认open。因此新模式必须在明确授权owner与独立运行入口启用，其他原商品消息保持原样。

## 验收
后端身份/绑定/换Token/错误脱敏；微信Token不进模型，普通商品消息照旧；H5教程和商品CRUD；红线批准/清空/拒绝/并发冲突；客户图片+规格数量+红线+联系方式+Excel；完整pytest和Node测试；实际启动微信登录及TG supervisor，缺真实商家TG Token时明确待绑定，不冒充真实TG收发验收。
