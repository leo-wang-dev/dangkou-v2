// dsh-engine 商品插件 v2：微信入口 ⇄ 侧车 catalog-v2
// env: CATALOG_V2_URL（默认 http://127.0.0.1:8890）、CATALOG_V2_SERVICE_TOKEN

export const name = 'catalog-v2'

const BASE = process.env.CATALOG_V2_URL || 'http://127.0.0.1:8890'
const TOKEN = process.env.CATALOG_V2_SERVICE_TOKEN || ''

async function api(path, method = 'GET', body = null) {
  const r = await fetch(`${BASE}${path}`, {
    method,
    headers: { 'X-Service-Token': TOKEN, 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`catalog-v2 ${path} -> ${r.status}: ${await r.text()}`)
  return r.json()
}

export async function apply(ctx) {
  ctx.registerTool('catalog_import', {
    description: '导入商品 Excel（内部由 Sub Agent 异步解析，需人工审批落库）。' +
      '调用后立即返回 docId；你必须安排几分钟后用 catalog_check 查询，' +
      'status=ticketed 时把审批页链接（{BASE}/?t=<token>，token 从 catalog_check 返回取）' +
      '发给用户——这就是回调推送。调用前若不知道品类，先问用户：剃须刀还是卷发棒。',
    input: {
      type: 'object',
      properties: {
        path: { type: 'string', description: '服务器上的 xlsx 文件绝对路径' },
        category: { type: 'string', enum: ['razor', 'curler'] },
      },
      required: ['path', 'category'],
    },
    output: { schema: { type: 'object' } },
    handler: async (args) => {
      const r = await api('/import', 'POST', args)
      return { docId: r.doc_id, note: '异步解析已启动（Sub Agent），预计2-8分钟，稍后用 catalog_check 查询' }
    },
  })

  ctx.registerTool('catalog_check', {
    description: '查询导入进度。返回 status=ticketed 时解析完成并已生成审批工单，' +
      '把审批链接发给用户；status=failed 时把 error 告知用户。',
    input: { type: 'object', properties: { docId: { type: 'number' } }, required: ['docId'] },
    output: { schema: { type: 'object' } },
    handler: async (args) => {
      const s = await api(`/import/${args.docId}`)
      if (s.status === 'ticketed') {
        const tks = await api('/tickets')
        const tk = tks.tickets.find(t => t.status === 'pending' && t.ticket_type === 'import')
        if (tk) s.approveUrl = `${BASE}/?t=${tk.token}`
      }
      return s
    },
  })

  ctx.registerTool('catalog_search', {
    description: '以图找货：传客户图片的服务器路径，返回 Top3-5 候选（含品类模板字段）。' +
      '用户回复"换一批"时把已展示的 product_id 放进 excludeIds 重查；' +
      '回复"没问题/发报价单"时调 catalog_quote 生成报价单并把文件发给用户。',
    input: {
      type: 'object',
      properties: {
        imagePath: { type: 'string' },
        topK: { type: 'number', default: 5 },
        excludeIds: { type: 'array', items: { type: 'string' }, default: [] },
      },
      required: ['imagePath'],
    },
    output: { schema: { type: 'object' } },
    handler: async (args) =>
      api('/search', 'POST', { image_path: args.imagePath,
                               top_k: args.topK ?? 5,
                               exclude_ids: args.excludeIds ?? [] }),
  })

  ctx.registerTool('catalog_quote', {
    description: '按报价单模板生成 Excel，返回服务器文件路径（用于微信发文件给用户转发客户）。',
    input: {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['razor', 'curler'] },
        productIds: { type: 'array', items: { type: 'string' } },
      },
      required: ['category', 'productIds'],
    },
    output: { schema: { type: 'object' } },
    handler: async (args) => api('/quote', 'POST', args),
  })

  ctx.registerTool('catalog_mutate', {
    description: 'AI 代操作商品（改字段/下架/新增）——只生成审批工单，不直接落库。' +
      '返回工单 ticketId，把审批页链接（{BASE}/?t=<token>）发给用户。',
    input: {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['razor', 'curler'] },
        action: { type: 'string', enum: ['update', 'delete', 'create'] },
        productId: { type: 'string' },
        changes: { type: 'object' },
      },
      required: ['category', 'action'],
    },
    output: { schema: { type: 'object' } },
    handler: async (args) => {
      const c = { ...(args.changes ?? {}) }
      if (args.action === 'create')
        return api(`/products/${args.category}`, 'POST', { changes: c })
      if (args.action === 'update')
        return api(`/products/${args.category}/${args.productId}`, 'PATCH', { changes: c })
      return api(`/products/${args.category}/${args.productId}`, 'DELETE')
    },
  })
}
