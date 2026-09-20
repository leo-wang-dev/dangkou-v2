import assert from 'node:assert/strict'
const mod = await import('../engine-plugin/catalog-v2.mjs')
assert.equal(typeof mod.apply, 'function')
assert.equal(mod.name, 'catalog-v2')
const tools = new Map()
await mod.apply({tools: {register(tool) {
  assert(!tools.has(tool.name), `duplicate tool ${tool.name}`)
  assert.equal(typeof tool.execute, 'function')
  assert(tool.parameters)
  assert(tool.output?.schema && typeof tool.output.render === 'function', `missing output contract: ${tool.name}`)
  tools.set(tool.name, tool)
}}})
for (const name of ['catalog_import', 'cs_redline_get', 'cs_redline_set', 'shop_contact_get', 'shop_contact_set']) {
  assert(tools.has(name), `missing ${name}`)
}
const calls = []
globalThis.fetch = async (url, options) => {
  calls.push({url, options})
  return {ok:true, text:async()=>JSON.stringify({ticket_id:42})}
}
await tools.get('shop_contact_set').execute({owner_tg_username:'owner123', owner_wechat:'wx-owner'})
assert.equal(JSON.parse(calls.at(-1).options.body).changes.owner_wechat, 'wx-owner')
await tools.get('cs_redline_set').execute({textRaw:'20个以下转人工', productId:'p1'})
assert.equal(JSON.parse(calls.at(-1).options.body).product_id, 'p1')
await tools.get('shop_contact_set').execute({shop_name:'TEST SHOP',stall_no:'A123',tg_bot_id:'12345',tg_bot_username:'test_shop_bot'})
assert.equal(JSON.parse(calls.at(-1).options.body).changes.shop_name,'TEST SHOP')
assert.equal(JSON.parse(calls.at(-1).options.body).changes.tg_bot_id,'12345')
assert(tools.get('shop_contact_set').parameters.properties.shop_name)
assert(!tools.get('catalog_import').parameters.required.includes('category'), 'Sheet import must not require a preset category')
await tools.get('catalog_import').execute({path:'/tmp/merchant.xlsx',sourceKey:'merchant-a'})
const importBody = JSON.parse(calls.at(-1).options.body)
assert.equal(importBody.path, '/tmp/merchant.xlsx')
assert.equal(importBody.source_key, 'merchant-a')
assert.equal(importBody.category, undefined)
assert.equal(tools.get('catalog_stats').parameters.properties.category.enum, undefined)
assert.equal(tools.get('catalog_mutate').parameters.properties.category.enum, undefined)
const mutateDescription = tools.get('catalog_mutate').parameters.properties.changes.description
assert(!mutateDescription.includes('所有价格问题为平台公共红线'),
  'WeChat merchant tool must not invent a default customer handoff rule')
assert(mutateDescription.includes('只按商家已审批的红线'),
  'WeChat merchant tool must describe merchant-owned redlines')
console.log(`plugin apply + ${tools.size} registration contracts + contact/redline calls passed`)
const beforeFailure = calls.length
const oldFetch = globalThis.fetch
globalThis.fetch = async () => ({ok:false,status:500,text:async()=> 'upload failed'})
await assert.rejects(tools.get('catalog_mutate').execute({category:'razor',action:'create',changes:{model_no:'FAIL-IMAGE'},imagePaths:['/does-not-exist/photo.jpg']}), /图片上传失败/)
globalThis.fetch = oldFetch
assert.equal(calls.length,beforeFailure,'failed photo must not create a product ticket')
