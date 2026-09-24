// dsh-engine 商品插件 v2：微信入口 ⇄ 侧车 catalog-v2（预置分类 + Excel Sheet 动态分类）
// env: CATALOG_V2_URL（默认 http://127.0.0.1:8890）、CATALOG_V2_SERVICE_TOKEN

export const name = 'catalog-v2'
export const inject = ['tools']

const BASE = process.env.CATALOG_V2_URL || 'http://127.0.0.1:8890'
const PUBLIC = process.env.CATALOG_V2_PUBLIC_URL || 'http://127.0.0.1:8890'
const MANAGE = process.env.CATALOG_V2_MANAGE_URL || PUBLIC
const TOKEN = process.env.CATALOG_V2_SERVICE_TOKEN || ''
// Optional per-instance guard. In an independent merchant deployment this is
// set to the shop identity returned by /shop. It prevents a bot process from
// silently querying a shared/public catalog after a rebinding or bad env edit.
const EXPECTED_SHOP_ID = process.env.CATALOG_EXPECTED_SHOP_ID || ''

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

function requiredText(value, label) {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`${label}不能为空`)
  return value.trim()
}

function optionalText(value, label) {
  if (value !== undefined && (typeof value !== 'string' || !value.trim())) {
    throw new Error(`${label}必须是非空文本`)
  }
}

function finiteNumber(value, label) {
  if (value !== undefined && (typeof value !== 'number' || !Number.isFinite(value))) {
    throw new Error(`${label}必须是有效数字`)
  }
}

export async function apply(ctx, _config = {}) {
  ctx.tools.register({
    name: 'catalog_import',
    description: '两阶段导入商家商品 Excel。phase=template：本次只识别并建立分类模板，不读取商品行入库；模板审批通过后必须提醒商家再次上传同一份商品 Excel。phase=products：必须使用模板阶段返回的 templateDocId，并且只按已审批模板解析商品。mode=new 时按“一个 Sheet=一个分类、识别到的表头=分类规格模板”；mode=existing 时按指定已有动态分类模板映射。模板和商品分开审批，商品不会在模板阶段入库。'
      + '模板阶段通常调用即已完成（status=ticketed，含审批入口，当条回复发给商家）；若返回仍在解析（status=parsing），等商家下次来问时再用 catalog_check 查询进度，不要承诺具体时长。template 阶段完成后明确提醒“请再次上传商品 Excel”，products 阶段 ticketed 后发商品审批入口。'
      + '收到 Excel 后必须先让商家选择“新建分类”或“并入已有分类”，得到明确答复后再调用 phase=template；不要把第一次上传直接当商品导入。同一阶段、同一文件和同一模板会被服务端幂等去重。'
      + '通常不要传 category；只有商家明确说这是旧版剃须刀或卷发棒固定模板时才传对应旧品类。',
    parameters: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '服务器上的 xlsx 文件绝对路径' },
        phase: { type: 'string', enum: ['template', 'products'], description: '必填：template=第一次上传，只建立并审批分类模板；products=模板审批后再次上传，导入商品' },
        mode: { type: 'string', enum: ['new', 'existing'], description: '必填：new=每个 Sheet 新建分类；existing=并入 categoryKey 指定的已有动态分类' },
        categoryKey: { type: 'string', description: 'mode=existing 时必填：目标已有动态分类 key' },
        templateDocId: { type: 'number', description: 'phase=products 时必填：phase=template 返回且已审批通过的 docId' },
        sourceKey: { type: 'string', description: '供应商/商品表的稳定唯一来源标识；重导沿用，不同供应商不可复用。未传按文件名区分。' },
        category: { type: 'string', enum: ['razor', 'curler'], description: '可选：仅旧版固定模板使用' },
      },
      required: ['path', 'phase', 'mode'],
    },
    output: OUT,
    async execute({ path, phase, mode, categoryKey, templateDocId, category, sourceKey }) {
      requiredText(path, 'Excel 文件路径')
      if (!['template', 'products'].includes(phase)) throw new Error('phase 必须是 template 或 products')
      if (!['new', 'existing'].includes(mode)) throw new Error('mode 必须是 new 或 existing')
      if (mode === 'existing') requiredText(categoryKey, 'categoryKey')
      if (mode === 'new' && categoryKey !== undefined) throw new Error('new 模式不能传 categoryKey')
      if (phase === 'products' && (!Number.isInteger(templateDocId) || templateDocId <= 0)) {
        throw new Error('products 阶段必须传 templateDocId（模板审批通过后返回的 docId）')
      }
      if (phase === 'template' && templateDocId !== undefined) {
        throw new Error('template 阶段不能传 templateDocId')
      }
      optionalText(sourceKey, 'sourceKey')
      if (category !== undefined && !['razor', 'curler'].includes(category)) {
        throw new Error('category 仅支持旧版固定分类 razor 或 curler')
      }
      const body = { path, source_key: sourceKey, mode, phase }
      if (categoryKey) body.category_key = categoryKey
      if (templateDocId !== undefined) body.template_doc_id = templateDocId
      if (category) body.category = category
      // 两阶段解析实测都只要几秒：原地等结果，一条回复把结果和审批入口
      // 说清，避免“完成推送”抢在对话回复前落地造成消息倒序。
      body.wait = true
      const r = await call('/import', 'POST', body)
      let note
      if (r.status === 'ticketed' && r.phase === 'template') {
        const cats = ((r.stats && r.stats.categories) || []).join('、') || '新分类'
        note = `模板识别完成：${cats}。把审批入口发给商家，请商家确认字段和客户可见性；模板审批通过后必须提醒商家再次上传同一份商品 Excel（用 templateDocId=${r.doc_id} 进入 products 阶段导入商品）。审批入口：${MANAGE}/?t=${TOKEN}`
      } else if (r.status === 'ticketed') {
        const s = r.stats || {}
        note = `商品解析完成：新增${s.new || 0} / 更新${s.update || 0} / 下架${s.delist || 0}。把审批入口发给商家核对，批准后商品才入库。审批入口：${MANAGE}/?t=${TOKEN}`
      } else if (r.status === 'failed') {
        note = `模板识别失败：${r.error || '未知错误'}；如实告知商家，不要编造原因`
      } else {
        note = phase === 'template'
          ? `模板识别已在后台启动，完成后审批入口会自动推送到本对话；模板审批通过后必须提醒商家再次上传商品 Excel，并用 templateDocId=${r.doc_id} 进入 products 阶段`
          : '商品解析已在后台启动，量大时需要一些时间；完成后商品审批入口会自动推送，不要向商家承诺具体分钟数'
      }
      return JSON.stringify({ docId: r.doc_id, phase, status: r.status || 'parsing',
        templateDocId: phase === 'template' ? r.doc_id : templateDocId,
        approveUrl: r.status === 'ticketed' ? `${MANAGE}/?t=${TOKEN}` : undefined,
        note })
    },
  })

  ctx.tools.register({
    name: 'catalog_check',
    description: '查询导入进度。status=ticketed=解析完成已生成审批工单（返回含 approveUrl，发给用户点开即审）；'
      + 'status=failed=把 error 告知用户。',
    parameters: { type: 'object', properties: { docId: { type: 'number' } }, required: ['docId'] },
    output: OUT,
    async execute({ docId }) {
      if (!Number.isInteger(docId) || docId <= 0) throw new Error('docId 必须是正整数')
      const s = await call(`/import/${docId}`)
      if (s.status === 'ticketed') {
        const tks = await call('/tickets')
        tks.tickets.find(t => t.status === 'pending' && ['import', 'template_import', 'product_import'].includes(t.ticket_type))
        s.approveUrl = `${MANAGE}/?t=${TOKEN}`
      }
      if (s.status === 'template_approved') {
        s.nextAction = '请再次上传同一份商品 Excel，并调用 catalog_import phase=products，传 templateDocId=docId'
      }
      return JSON.stringify(s)
    },
  })

  ctx.tools.register({
    name: 'catalog_search',
    description: '以图找货：客户图片路径 → Top3-5 候选（品类模板字段）。'
      + '用户想看其他候选（如"换一批/还有吗"）→ 把已展示的 productId 放进 excludeIds 重查；'
      + '候选不代表已确认同款；用户确认商品并明确数量后，才可按商家要求调 catalog_quote，不能仅凭"没问题"自动出单。',
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
      requiredText(imagePath, '图片路径')
      if (topK !== undefined && (!Number.isInteger(topK) || topK < 1 || topK > 20)) {
        throw new Error('topK 必须是 1 到 20 的整数')
      }
      if (excludeIds !== undefined && (!Array.isArray(excludeIds)
          || excludeIds.some(id => typeof id !== 'string' || !id.trim())
          || excludeIds.length > 100)) {
        throw new Error('excludeIds 必须是不超过 100 项的商品 ID 数组')
      }
      const r = await call('/search', 'POST', {
        image_path: imagePath, top_k: topK ?? 5, exclude_ids: excludeIds ?? [] })
      return JSON.stringify(r)
    },
  })


  ctx.tools.register({
    name: 'catalog_stats',
    description: '每次回答商品、分类、型号、颜色或库存问题前必须调用本工具，不能凭历史对话回答。实时查询商品数据：总数指款数，不是库存件数；审批状态和客户可见性以本次工具数据为准；模板导入批准后商品默认可见，手工新增仍以可观测字段为准。返回 categories/category_keys，动态 Excel Sheet 分类也必须照此使用，禁止只列固定的剃须刀和卷发棒。full=true 时返回该品类全部在售商品（全字段）。'
      + '问"有多少款产品""卷发棒有几个"→ 不传 full；'
      + '问"都有哪些型号/什么颜色/某款什么配置"→ full=true（品类≤200款，全量直接看）；'
      + '清单里查不到的型号=已下架或不存在，如实告诉用户。'
      + '报数量一律用返回里的 total，不要自己数行数。'
      + '预置剃须刀/卷发棒出正式报价单：full=true 拿到全部 id → 数量向用户确认 → catalog_quote。动态分类未配置报价字段映射时不能生成正式报价单。',
    parameters: {
      type: 'object',
      properties: {
        full: { type: 'boolean', description: 'true=返回全部在售商品全字段（默认 false 只报数）' },
        category: { type: 'string', description: '只查一个品类时传分类 key；可先从商品管理页或导入结果获取' },
      },
    },
    output: OUT,
    async execute({ full, category }) {
      if (full !== undefined && typeof full !== 'boolean') throw new Error('full 必须是布尔值')
      optionalText(category, 'category')
      const q = []
      if (full) q.push('full=true')
      if (category) q.push(`category=${category}`)
      const result = await call('/stats' + (q.length ? '?' + q.join('&') : ''))
      if (EXPECTED_SHOP_ID && result.shop_id && result.shop_id !== EXPECTED_SHOP_ID) {
        throw new Error(`商品服务档口身份不匹配（期望 ${EXPECTED_SHOP_ID}，实际 ${result.shop_id}），已阻止跨档口查询`)
      }
      return JSON.stringify(result)
    },
  })

  ctx.tools.register({
    name: 'catalog_quote',
    description: '生成正式报价单 Excel（ELETRO BELEZA 全字段模板：14列含装箱物流+合计+定金，完成后系统自动推送文件）。'
      + '支持预置剃须刀/卷发棒，以及已配置报价字段映射的动态分类（quotable=true，用 quote_map_get 查）；未配置映射的动态分类会返回明确错误，按提示用 quote_map_set 配置后即可出单。'
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
              category: { type: 'string', description: '分类 key：razor、curler 或 catalog_stats 返回的动态分类 key' },
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
      if (!Array.isArray(items) || items.length === 0) throw new Error('至少选择一款商品')
      if (items.length > 200) throw new Error('单次报价最多 200 款商品')
      for (const item of items) {
        if (!item) throw new Error('商品项无效')
        requiredText(item.category, 'category')
        requiredText(item.product_id, 'product_id')
        if (item.quantity !== undefined && (!Number.isInteger(item.quantity) || item.quantity <= 0)) {
          throw new Error('quantity 必须是正整数')
        }
      }
      finiteNumber(price_adjustment_pct, 'price_adjustment_pct')
      if (price_adjustment_pct !== undefined && price_adjustment_pct < -100) {
        throw new Error('price_adjustment_pct 不能小于 -100')
      }
      finiteNumber(depositPercent, 'depositPercent')
      if (depositPercent !== undefined && (depositPercent < 0 || depositPercent > 100)) {
        throw new Error('depositPercent 必须在 0 到 100 之间')
      }
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
    name: 'quote_map_get',
    description: '查看动态分类的报价字段映射：哪个字段当报价列/型号列/箱规列/颜色列，以及是否可直接出正式报价单（quotable）。'
      + '未配置时返回 suggestion（按表头自动推荐）和全部字段，向商家复述让其确认。',
    parameters: {
      type: 'object',
      properties: { categoryKey: { type: 'string', description: '动态分类 key' } },
      required: ['categoryKey'],
    },
    output: OUT,
    async execute({ categoryKey }) {
      requiredText(categoryKey, 'categoryKey')
      return JSON.stringify(await call(`/categories/${encodeURIComponent(categoryKey)}/quote-map`))
    },
  })

  ctx.tools.register({
    name: 'quote_map_set',
    description: '为动态分类配置报价字段映射：商家说"报价用『报价（不含税不含运）』这列、箱规用『箱规』列"时调用。'
      + 'priceField 和 modelField 必填，ctnField/colorField 可选；传中文字段名（表头）或字段 key 均可。'
      + '配置立即生效，之后 catalog_quote 即可对该分类出正式报价单。多个价格列时必须让商家明示用哪列，不得自行猜测。',
    parameters: {
      type: 'object',
      properties: {
        categoryKey: { type: 'string', description: '动态分类 key' },
        priceField: { type: 'string', description: '报价列（表头名或字段 key）' },
        modelField: { type: 'string', description: '型号列（表头名或字段 key）' },
        ctnField: { type: 'string', description: '可选：箱规列' },
        colorField: { type: 'string', description: '可选：颜色列' },
      },
      required: ['categoryKey', 'priceField', 'modelField'],
    },
    output: OUT,
    async execute({ categoryKey, priceField, modelField, ctnField, colorField }) {
      requiredText(categoryKey, 'categoryKey')
      requiredText(priceField, 'priceField')
      requiredText(modelField, 'modelField')
      optionalText(ctnField, 'ctnField')
      optionalText(colorField, 'colorField')
      const body = { price_field: priceField, model_field: modelField }
      if (ctnField) body.ctn_field = ctnField
      if (colorField) body.color_field = colorField
      const r = await call(`/categories/${encodeURIComponent(categoryKey)}/quote-map`, 'PUT', body)
      return JSON.stringify({ quotable: r.quotable, quote_map: r.quote_map,
        note: '报价映射已保存，现在可以出正式报价单了' })
    },
  })

  ctx.tools.register({
    name: 'category_rename',
    description: '给动态分类改名（key 不变，商品不动）。Sheet 名是默认名（如 Sheet1/工作表1）或商家想换更好懂的分类名时调用，'
      + '改名后向商家复述确认。预置剃须刀/卷发棒分类名称固定，不能改。',
    parameters: {
      type: 'object',
      properties: {
        categoryKey: { type: 'string', description: '动态分类 key' },
        newName: { type: 'string', description: '新分类名（给商家看的名称）' },
      },
      required: ['categoryKey', 'newName'],
    },
    output: OUT,
    async execute({ categoryKey, newName }) {
      requiredText(categoryKey, 'categoryKey')
      requiredText(newName, 'newName')
      const r = await call(`/categories/${encodeURIComponent(categoryKey)}`, 'PATCH', { name: newName })
      return JSON.stringify({ key: r.key, name: r.name, note: '分类已改名' })
    },
  })

  ctx.tools.register({
    name: 'category_create',
    description: '手工创建商品分类（不经过 Excel 导入）。商家说"帮我建个分类/品类叫X，字段有A、B、C"或'
      + '不想导 Excel 想直接加商品时使用。fields 每项 {label, visibility}，visibility 可选 public（客户可见）/internal（仅商家可见），默认 public。'
      + '创建后生成模板审批工单，商家在审批入口批准后分类即上商品管理页；之后可在页面"新增商品"、对话里自然语言加商品（catalog_mutate），'
      + '再传 Excel 也会按该分类模板由 AI 解析入库。字段名要从商家原话提取，不要自作主张增删字段。',
    parameters: {
      type: 'object',
      properties: {
        name: { type: 'string', description: '分类名，如：吹风机现货' },
        fields: {
          type: 'array',
          description: '字段清单',
          items: {
            type: 'object',
            properties: {
              label: { type: 'string', description: '字段名（表头），如：型号' },
              visibility: { type: 'string', enum: ['public', 'internal'] },
            },
            required: ['label'],
          },
        },
      },
      required: ['name', 'fields'],
    },
    output: OUT,
    async execute({ name, fields }) {
      requiredText(name, '分类名称')
      if (!Array.isArray(fields) || !fields.length) throw new Error('fields 必须是至少一项的字段数组')
      if (fields.length > 40) throw new Error('字段数超过 40，请和商家确认精简')
      const clean = fields.map(f => ({ label: String(f?.label || '').trim().slice(0, 40),
        visibility: f?.visibility === 'internal' ? 'internal' : 'public' }))
        .filter(f => f.label)
      if (!clean.length) throw new Error('fields 里没有有效字段名')
      const r = await call('/categories', 'POST', { name: name.trim(), fields: clean })
      return JSON.stringify({ ticketId: r.ticket_id, name: r.name, fields: r.fields,
        note: `分类「${r.name}」创建工单已生成（${r.fields} 个字段）。请商家打开审批入口批准，批准后分类即上商品管理页，然后就能在页面或对话里加商品。审批入口：${MANAGE}/?t=${TOKEN}` })
    },
  })

  ctx.tools.register({
    name: 'category_supplier_set',
    description: '设置分类的供应商（厂家）。商家说“这个分类是华悦的货/供应商改成XX”时用；'
      + '供应商用于按厂家聚合查询（“华悦还有多少货”“多少款在推广属于哪些商家”）。传空字符串清除。',
    parameters: {
      type: 'object',
      properties: {
        categoryKey: { type: 'string', description: '目标动态分类 key' },
        supplier: { type: 'string', description: '供应商名，空字符串=清除' },
      },
      required: ['categoryKey', 'supplier'],
    },
    output: OUT,
    async execute({ categoryKey, supplier }) {
      requiredText(categoryKey, 'categoryKey')
      const r = await call(`/categories/${encodeURIComponent(categoryKey)}`, 'PATCH',
        { supplier: String(supplier || '').trim() })
      return JSON.stringify({ key: r.key, supplier: r.supplier,
        note: r.supplier ? `分类「${r.name}」的供应商已设为「${r.supplier}」`
          : `分类「${r.name}」的供应商已清除` })
    },
  })

  ctx.tools.register({
    name: 'catalog_stats',
    description: '在售商品统计。按供应商聚合（默认）：商家问“XX供应商还有多少货”“多少款在推广/对客户可见、属于哪些商家”时用；'
      + 'by=category 按分类聚合。推广口径=对客户可见（cs_visible）。数字必须来自本工具，不能靠记忆。',
    parameters: {
      type: 'object',
      properties: { by: { type: 'string', enum: ['supplier', 'category'] } },
    },
    output: OUT,
    async execute({ by }) {
      const r = await call('/stats/supplier' + (by === 'category' ? '?by=category' : ''))
      return JSON.stringify(r)
    },
  })

  ctx.tools.register({
    name: 'category_visibility',
    description: '设置整个分类对客户是否可见：一次批量设置该分类全部在售商品的「可观测」。'
      + '商家说"把XX分类整体隐藏/客户不可见"时传 visible=false；"恢复可见/上架给客户看"传 true。'
      + '单个商品的可见性仍用 catalog_mutate 改「可观测」字段。',
    parameters: {
      type: 'object',
      properties: {
        categoryKey: { type: 'string', description: '分类 key（预置 razor/curler 或动态分类 key）' },
        visible: { type: 'boolean', description: 'true=对客户可见，false=整体不可见' },
      },
      required: ['categoryKey', 'visible'],
    },
    output: OUT,
    async execute({ categoryKey, visible }) {
      requiredText(categoryKey, 'categoryKey')
      if (typeof visible !== 'boolean') throw new Error('visible 必须是布尔值')
      const r = await call(`/categories/${encodeURIComponent(categoryKey)}/visibility`, 'PATCH', { visible })
      return JSON.stringify({ key: r.key, updated: r.updated,
        note: r.visible ? `该分类 ${r.updated} 款商品已全部对客户可见` : `该分类 ${r.updated} 款商品已全部对客户不可见` })
    },
  })

  ctx.tools.register({
    name: 'catalog_mutate',
    description: 'AI 代操作商品（改字段/下架/新增）——只生成审批工单不落库，返回含 approveUrl 发给用户。'
      + '动态分类先调用 catalog_stats（full=true）读取 category_keys 和该分类实际表头；changes 只能使用返回的字段名或字段 key。'
      + 'changes 的字段名必须用该品类清单里的名字（中文名或括号里的列名，二选一；用别的名字会被打回）：'
      + '剃须刀：产品型号(model_no)/功能描述(description)/颜色(color)/产品尺寸(mm)(size_mm)/彩盒尺寸(mm)(giftbox_mm)/单套重量(g)(unit_weight_g)/箱规(ctn_spec)/报价(price)/备注(remark)；'
      + '卷发棒：ITEM.NO 型号(item_no)/装箱尺寸(ctn_size)/装箱数量(ctn_qty)/价格(price)/电压(voltage)/功率(power)/发热体(heater)/材质(material)/频率(frequency)/备注(remark)。'
      + '用户话里或图片上出现清单外的属性（如工作温度/净重/认证/包装尺寸）由你负责映射：同义的对上清单字段（"额定电压"→电压、"产品型号"→ITEM.NO 型号、"报价"对卷发棒是"价格"），'
      + '对不上的全部拼进"备注"，格式如"工作温度：160-220℃｜净重：355g｜认证：CE"——不要发明清单外的字段名。'
      + '用户随消息发了图片时，必须把 [MEDIA:image] 后面的路径放进 imagePaths，图片会关联到商品。不传图片就丢了。',
    parameters: {
      type: 'object',
      properties: {
        category: { type: 'string', description: '分类 key；支持固定分类和 Excel Sheet 创建的动态分类' },
        action: { type: 'string', enum: ['update', 'delete', 'create'] },
        productId: { type: 'string' },
        changes: { type: 'object', description: '字段名→新值（名字必须来自上方品类清单；'
          + '另有C端字段「可观测」（1=对客户可见，0=不可见）。阶梯报价已取消；客户是否转人工只按商家已审批的红线，未设置就不触发。清单外的信息拼进"备注"字段）' },
        imagePaths: { type: 'array', items: { type: 'string' },
          description: '用户发的图片的服务器绝对路径（[MEDIA:image] 后面的路径），新增/换图时传入' },
      },
      required: ['category', 'action'],
    },
    output: OUT,
    async execute({ category, action, productId, changes, imagePaths }) {
      requiredText(category, 'category')
      if (!['create', 'update', 'delete'].includes(action)) throw new Error('action 不合法')
      if (action !== 'create') requiredText(productId, 'productId')
      if (changes !== undefined && (changes === null || Array.isArray(changes)
          || typeof changes !== 'object')) throw new Error('changes 必须是字段对象')
      if (imagePaths !== undefined && (!Array.isArray(imagePaths)
          || imagePaths.some(p => typeof p !== 'string' || !p.trim()))) {
        throw new Error('imagePaths 必须是非空图片路径数组')
      }
      if (action === 'create' && Object.keys(changes || {}).length === 0
          && (imagePaths || []).length === 0) {
        throw new Error('新增商品至少需要一个字段或图片')
      }
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
          else throw new Error('upload rejected')
        } catch (e) {
          throw new Error('图片上传失败，未创建商品审批工单；请重新发送图片后重试。')
        }
      }
      const body = { changes: changes ?? {} }
      if (imageRels.length) body.images = imageRels
      let r
      if (action === 'create') r = await call(`/products/${category}`, 'POST', body)
      else if (action === 'update') r = await call(`/products/${category}/${productId}`, 'PATCH', body)
      else r = await call(`/products/${category}/${productId}`, 'DELETE')
      r.approveUrl = `${MANAGE}/?t=${TOKEN}`
      return JSON.stringify(r)
    },
  })

  ctx.tools.register({
    name: 'shop_contact_get', output: OUT,
    description: '查看本档口唯一ID、名称、档口号、绑定的TG bot和老板联系方式。',
    parameters: {type:'object', properties:{}},
    async execute(){ return JSON.stringify(await call('/shop')) },
  })
  ctx.tools.register({
    name: 'shop_contact_set', output: OUT,
    description: '商家通过对话补充档口名称、档口号、联系人，或修改老板联系方式、店铺地址、营业时间、发货物流说明和常见问答。仅生成审批工单，批准后 bot 对客发送。只传商家明确提供的字段，不编造。',
    parameters: {type:'object', properties:{shop_name:{type:'string'}, stall_no:{type:'string'}, contact_name:{type:'string'}, owner_tg_username:{type:'string'}, owner_wechat:{type:'string'}, address:{type:'string'}, business_hours:{type:'string'}, shipping_info:{type:'string'}, faq:{type:'string'}}},
    async execute(changes){
      if (!changes || typeof changes !== 'object' || Array.isArray(changes)
          || Object.keys(changes).length === 0) throw new Error('至少提供一项档口资料')
      const r = await call('/shop', 'PATCH', {changes})
      return JSON.stringify({...r, approveUrl:`${PUBLIC}/?t=${encodeURIComponent(TOKEN)}`, 提示:'请打开审批链接确认档口资料。'})
    },
  })

  // ---- C端：转人工红线（自然语言知识，微信对话式修改，审批生效）----
  ctx.tools.register({
    name: 'cs_redline_get',
    output: OUT,
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
        平台规则: r.platform_rule, 平台规则可修改: false,
        商家原文: r.text_raw, 注入版: r.text_summary,
      })
    },
  })

  ctx.tools.register({
    name: 'cs_redline_set',
    output: OUT,
    description: '修改商家自定义转人工红线（如账期、质量投诉、定制）。只启用商家明确填写并批准的转人工条件，不自行把引导案例设为规则。'
      + '记录商家的原文，保留全部数字阈值；传空文本可清空全店规则，商品规则清空后继承全店；只生成审批工单不直接生效，'
      + '审批卡已进入微信发送队列，批准后下一条询价即按新红线判定。改前先用 cs_redline_get 看当前值。',
    parameters: {
      type: 'object',
      properties: {
        textRaw: { type: 'string', description: '红线原文（优先商家原话；或忠实总结，保留全部数字）' },
        productId: { type: 'string', description: '只改某个商品时传商品ID；改全店默认不传' },
      },
      required: ['textRaw'],
    },
    async execute({ textRaw, productId }) {
      if (typeof textRaw !== 'string') throw new Error('textRaw 必须是文本')
      optionalText(productId, 'productId')
      const body = { text_raw: textRaw }
      if (productId) body.product_id = productId
      const r = await call('/cs/redline', 'POST', body)
      return JSON.stringify({
        已生成审批工单: true, 工单号: r.ticket_id,
        审批链接: `${MANAGE}/cs/redline.html?i=${r.ticket_id}&t=${encodeURIComponent(r.token)}&auth=${encodeURIComponent(TOKEN)}`,
        提示: '审批卡已进入发送队列，也可直接打开审批链接查看旧文和新文，批准后生效。',
      })
    },
  })
}
