// dsh-engine 商品插件 v2：微信入口 ⇄ 侧车 catalog-v2（模板制：剃须刀/卷发棒）
// env: CATALOG_V2_URL（默认 http://127.0.0.1:8890）、CATALOG_V2_SERVICE_TOKEN

export const name = 'catalog-v2'
export const inject = ['tools']

const BASE = process.env.CATALOG_V2_URL || 'http://127.0.0.1:8890'
const PUBLIC = process.env.CATALOG_V2_PUBLIC_URL || 'http://134.175.135.102:8890'
const TOKEN = process.env.CATALOG_V2_SERVICE_TOKEN || ''

async function call(path, method = 'GET', body = null) {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'X-Service-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const text = await r.text()
  if (!r.ok) throw new Error(`catalog-v2 ${path} -> ${r.status}: ${text}`)
  try { return JSON.parse(text) } catch { return text }
}

const OUT = { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] }

export async function apply(ctx, _config = {}) {
  ctx.tools.register({
    name: 'catalog_import',
    description: '导入商品 Excel（剃须刀/卷发棒品类模板，内部由 Sub Agent 异步解析，需人工审批落库）。'
      + '返回 docId 后你必须安排几分钟后用 catalog_check 查询并告知用户进度（ticketed 时发审批链接）——这就是回调推送。'
      + '不知道品类时先问用户：剃须刀还是卷发棒。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '服务器上的 xlsx 文件绝对路径' },
        category: { type: 'string', enum: ['razor', 'curler'] },
      },
      required: ['path', 'category'],
    },
    output: OUT,
    async execute({ path, category }) {
      const r = await call('/import', 'POST', { path, category })
      const mins = Math.max(2, Math.round((r.est_sec || 480) / 60))
      return JSON.stringify({ docId: r.doc_id, note: `解析已启动，预计约${mins}分钟，完成后会自动推送` })
    },
  })

  ctx.tools.register({
    name: 'catalog_check',
    description: '查询导入进度。status=ticketed=解析完成已生成审批工单（返回含 approveUrl，发给用户点开即审）；'
      + 'status=failed=把 error 告知用户。',
    parameters: { type: 'object', properties: { docId: { type: 'number' } }, required: ['docId'] },
    output: OUT,
    async execute({ docId }) {
      const s = await call(`/import/${docId}`)
      if (s.status === 'ticketed') {
        const tks = await call('/tickets')
        const tk = tks.tickets.find(t => t.status === 'pending' && t.ticket_type === 'import')
        s.approveUrl = `${PUBLIC}/?t=${TOKEN}`
      }
      return JSON.stringify(s)
    },
  })

  ctx.tools.register({
    name: 'catalog_search',
    description: '以图找货：客户图片路径 → Top3-5 候选（品类模板字段）。'
      + '用户想看其他候选（如"换一批/还有吗"）→ 把已展示的 productId 放进 excludeIds 重查；'
      + '用户确认结果或想要报价单（如"没问题/可以/发我报价单"）→ 调 catalog_quote（异步，系统自动推送文件）。',
    parameters: {
      type: 'object',
      properties: {
        imagePath: { type: 'string' },
        topK: { type: 'number' },
        excludeIds: { type: 'array', items: { type: 'string' } },
      },
      required: ['imagePath'],
    },
    output: OUT,
    async execute({ imagePath, topK, excludeIds }) {
      const r = await call('/search', 'POST', {
        image_path: imagePath, top_k: topK ?? 5, exclude_ids: excludeIds ?? [] })
      return JSON.stringify(r)
    },
  })


  ctx.tools.register({
    name: 'catalog_stats',
    description: '查询商品数据：总数、按品类数量；full=true 时返回该品类全部在售商品（全字段）。'
      + '问"有多少款产品""卷发棒有几个"→ 不传 full；'
      + '问"都有哪些型号/什么颜色/某款什么配置"→ full=true（品类≤200款，全量直接看）；'
      + '清单里查不到的型号=已下架或不存在，如实告诉用户。'
      + '报数量一律用返回里的 total，不要自己数行数。'
      + '整品类出报价单：full=true 拿到全部 id → 数量向用户确认 → catalog_quote。',
    parameters: {
      type: 'object',
      properties: {
        full: { type: 'boolean', description: 'true=返回全部在售商品全字段（默认 false 只报数）' },
        category: { type: 'string', enum: ['razor', 'curler'], description: '只查一个品类时传' },
      },
    },
    output: OUT,
    async execute({ full, category }) {
      const q = []
      if (full) q.push('full=true')
      if (category) q.push(`category=${category}`)
      return JSON.stringify(await call('/stats' + (q.length ? '?' + q.join('&') : '')))
    },
  })

  ctx.tools.register({
    name: 'catalog_quote',
    description: '生成报价单 Excel（ELETRO BELEZA 全字段模板：14列含装箱物流+合计+定金，完成后系统自动推送文件）。'
      + 'items 每项含 product_id 和 quantity；price_adjustment_pct 正=上浮负=下浮（如 3=+3%, -5=下浮5%）。'
      + '用户说"出厂价加3个点"→ pct=3；"销售价下浮5%"→ pct=-5；"加3%佣金"→ pct=3。'
      + 'depositPercent=定金百分比：用户说"30%定金"传30、"两成定金"传20，不传默认30。'
      + '数量按整箱向上取整（QUANTITY=每箱数×箱数，如要100台每箱60→按2箱120台），回复时主动向用户说明实际按整箱计的数量。',
    parameters: {
      type: 'object',
      properties: {
        items: {
          type: 'array',
          items: {
            type: 'object',
            properties: {
              category: { type: 'string', enum: ['razor', 'curler'] },
              product_id: { type: 'string' },
              quantity: { type: 'number', description: '数量（台/个）' },
            },
            required: ['category', 'product_id'],
          },
        },
        price_adjustment_pct: { type: 'number', description: '价格调整百分比，正=上浮负=下浮' },
        depositPercent: { type: 'number', description: '定金百分比（30=30%），用户说了定金比例才传，默认30' },
      },
      required: ['items'],
    },
    output: OUT,
    async execute({ items, price_adjustment_pct, depositPercent }) {
      const r = await call('/quote', 'POST', {
        items: (items || []).map(i => ({
          category: i.category, product_id: i.product_id,
          quantity: i.quantity ?? 1,
        })),
        price_adjustment_pct: price_adjustment_pct ?? 0,
        deposit_pct: depositPercent ?? 30,
      })
      return JSON.stringify({ path: r.path,
        note: `报价单已生成（${items?.length || 0} 款，调整 ${price_adjustment_pct ?? 0}%，定金 ${depositPercent ?? 30}%），系统自动推送` })
    },
  })

  ctx.tools.register({
    name: 'catalog_mutate',
    description: 'AI 代操作商品（改字段/下架/新增）——只生成审批工单不落库，返回含 approveUrl 发给用户。'
      + 'changes 的字段名必须用该品类清单里的名字（中文名或括号里的列名，二选一；用别的名字会被打回）：'
      + '剃须刀：产品型号(model_no)/功能描述(description)/颜色(color)/产品尺寸(mm)(size_mm)/彩盒尺寸(mm)(giftbox_mm)/单套重量(g)(unit_weight_g)/箱规(ctn_spec)/报价(price)/备注(remark)；'
      + '卷发棒：ITEM.NO 型号(item_no)/装箱尺寸(ctn_size)/装箱数量(ctn_qty)/价格(price)/电压(voltage)/功率(power)/发热体(heater)/材质(material)/频率(frequency)/备注(remark)。'
      + '用户话里或图片上出现清单外的属性（如工作温度/净重/认证/包装尺寸）由你负责映射：同义的对上清单字段（"额定电压"→电压、"产品型号"→ITEM.NO 型号、"报价"对卷发棒是"价格"），'
      + '对不上的全部拼进"备注"，格式如"工作温度：160-220℃｜净重：355g｜认证：CE"——不要发明清单外的字段名。'
      + '用户随消息发了图片时，必须把 [MEDIA:image] 后面的路径放进 imagePaths，图片会关联到商品。不传图片就丢了。',
    parameters: {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['razor', 'curler'] },
        action: { type: 'string', enum: ['update', 'delete', 'create'] },
        productId: { type: 'string' },
        changes: { type: 'object', description: '字段名→新值（名字必须来自上方品类清单；'
          + '另有两个C端字段可用：「阶梯价」（格式 20:12;50:11，数量:单价，升序，至少一档，利润烤进价里）'
          + '和「可观测」（1=对客户可见，0=不可见；开1必须同时给合法阶梯价）。清单外的信息拼进"备注"字段）' },
        imagePaths: { type: 'array', items: { type: 'string' },
          description: '用户发的图片的服务器绝对路径（[MEDIA:image] 后面的路径），新增/换图时传入' },
      },
      required: ['category', 'action'],
    },
    output: OUT,
    async execute({ category, action, productId, changes, imagePaths }) {
      // 先把图片上传到侧车暂存区，拿到 staging 路径
      const imageRels = []
      for (const p of (imagePaths || [])) {
        try {
          const fs = await import('node:fs')
          const buf = fs.readFileSync(p)
          // 微信图片存为 .bin——从文件头检测真实格式
          let ext = '.jpg'
          if (buf[0] === 0x89 && buf[1] === 0x50) ext = '.png'
          else if (buf[0] === 0xFF && buf[1] === 0xD8) ext = '.jpg'
          else if (buf[0] === 0x49 && buf[1] === 0x49) ext = '.tif'
          else if (buf[0] === 0x42 && buf[1] === 0x4D) ext = '.bmp'
          const fname = (p.split('/').pop() || 'img').replace(/\.bin$/, '') + ext
          const fd = new FormData()
          fd.append('file', new Blob([buf]), fname)
          const up = await fetch(`${BASE}/upload`, {
            method: 'POST',
            headers: { 'X-Service-Token': TOKEN },
            body: fd,
          })
          if (up.ok) imageRels.push((await up.json()).path)
          else console.error(`[catalog-v2] upload ${p}: ${up.status} ${await up.text()}`)
        } catch (e) {
          console.error(`[catalog-v2] 图片上传失败 ${p}: ${e.message}`)
        }
      }
      const body = { changes: changes ?? {} }
      if (imageRels.length) body.images = imageRels
      let r
      if (action === 'create') r = await call(`/products/${category}`, 'POST', body)
      else if (action === 'update') r = await call(`/products/${category}/${productId}`, 'PATCH', body)
      else r = await call(`/products/${category}/${productId}`, 'DELETE')
      r.approveUrl = `${PUBLIC}/?t=${TOKEN}`
      return JSON.stringify(r)
    },
  })

  // ---- C端：转人工红线（自然语言知识，微信对话式修改，审批生效）----
  registry.register({
    name: 'cs_redline_get',
    description: '查看当前转人工红线（商家问"现在的红线是什么"时用）。product_id 传商品ID查单个商品的红线（未单独设置=继承全店默认）。',
    parameters: {
      type: 'object',
      properties: {
        productId: { type: 'string', description: '商品ID（查全店红线时不传）' },
      },
    },
    async execute({ productId }) {
      const q = productId ? `?product_id=${encodeURIComponent(productId)}` : ''
      const r = await call(`/cs/redline${q}`, 'GET')
      return JSON.stringify({
        作用域: productId ? `商品 ${productId}` : '全店',
        商家原文: r.text_raw, 注入版: r.text_summary,
      })
    },
  })

  registry.register({
    name: 'cs_redline_set',
    description: '修改转人工红线（商家说人话即可，如"数量少于50的转人工""这款低于9块不谈"）。'
      + '记录商家的原文（或你的简明总结，保留全部数字阈值）；只生成审批工单不直接生效，'
      + '审批卡已自动推送到商家微信，批准后下一条询价即按新红线判定。改前先用 cs_redline_get 看当前值。',
    parameters: {
      type: 'object',
      properties: {
        textRaw: { type: 'string', description: '红线原文（优先商家原话；或忠实总结，保留全部数字）' },
        productId: { type: 'string', description: '只改某个商品时传商品ID；改全店默认不传' },
      },
      required: ['textRaw'],
    },
    async execute({ textRaw, productId }) {
      const body = { text_raw: textRaw }
      if (productId) body.product_id = productId
      const r = await call('/cs/redline', 'POST', body)
      return JSON.stringify({
        已生成审批工单: true, 工单号: r.ticket_id,
        提示: '审批卡已推送商家微信（显示旧文→新文），批准即生效。请提醒商家点开确认。',
      })
    },
  })
}
