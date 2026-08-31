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
    name: 'catalog_quote',
    description: '生成报价单 Excel（异步：返回 jobId+预计秒数，完成后系统自动把文件推送给用户，你不要自己发文件、也不要声称已发送）。'
      + '**productIds 默认只传一个**：检索场景=用户确认的第一名（或用户回复的序号对应那款）；'
      + '只有用户明确说"都要/全部/这几款"才传多个。',
    parameters: {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['razor', 'curler'] },
        productIds: { type: 'array', items: { type: 'string' } },
      },
      required: ['category', 'productIds'],
    },
    output: OUT,
    async execute({ category, productIds }) {
      const r = await call('/quote', 'POST', { category, product_ids: productIds })
      return JSON.stringify({ jobId: r.job_id, estSec: r.est_sec,
        note: `报价单生成中，预计${Math.max(1, Math.round(r.est_sec / 60))}分钟内自动推送给用户` })
    },
  })

  ctx.tools.register({
    name: 'catalog_mutate',
    description: 'AI 代操作商品（改字段/下架/新增）——只生成审批工单不落库，返回含 approveUrl 发给用户。'
      + '用户随消息发了图片时，把图片路径放进 imagePaths，图片会关联到商品。',
    parameters: {
      type: 'object',
      properties: {
        category: { type: 'string', enum: ['razor', 'curler'] },
        action: { type: 'string', enum: ['update', 'delete', 'create'] },
        productId: { type: 'string' },
        changes: { type: 'object', description: '列名→新值' },
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
          const fd = new FormData()
          fd.append('file', new Blob([buf]), p.split('/').pop() || 'img.png')
          const up = await fetch(`${BASE}/upload`, {
            method: 'POST',
            headers: { 'X-Service-Token': TOKEN },
            body: fd,
          })
          if (up.ok) imageRels.push((await up.json()).path)
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
}
