import assert from 'node:assert/strict'
import { apply } from '../engine-plugin/catalog-v2.mjs'

const tools = new Map()
await apply({tools:{register(tool){ tools.set(tool.name, tool) }}})

let calls = []
let imageHintMapped = true
globalThis.fetch = async (url, options = {}) => {
  calls.push({url:String(url), options})
  const path = new URL(String(url)).pathname
  let body = {}
  if (path === '/import') body = {doc_id:7, status:'ticketed', phase:'template', stats:{
    categories:['Sheet1'], review_hints:[{kind:'unlabeled_image_column', source_sheet:'Sheet1', column:2, image_count:58, mapped:imageHintMapped}],
  }}
  else if (path === '/import/7') body = {id:7, status:'ticketed', phase:'template', stats:{
    review_hints:[{kind:'unlabeled_image_column', source_sheet:'Sheet1', column:2, image_count:58, mapped:imageHintMapped}],
  }}
  else if (path === '/tickets') body = {tickets:[{id:9,status:'pending',ticket_type:'template_import'}]}
  else if (path === '/stats') body = {
    total: 2,
    categories: [{key: 'cat_custom', name: '跨端测试类目', total: 2, customer_visible: 2}],
    category_keys: {'跨端测试类目': 'cat_custom'},
    by_category: {'跨端测试类目': 2},
  }
  else if (path === '/search') body = {hits:[{product_id:'p1'}]}
  else if (path === '/quote') body = {path:'/tmp/quote.xlsx'}
  else if (path === '/shop') body = options.method === 'PATCH' ? {ticket_id:3} : {shop_name:'测试档口'}
  else body = {ticket_id:4, token:'approval-token'}
  return {ok:true, status:200, text:async()=>JSON.stringify(body)}
}

const imported = JSON.parse(await tools.get('catalog_import').execute({path:'/tmp/products.xlsx', sourceKey:'supplier-a', phase:'template', mode:'new'}))
assert.equal(imported.docId, 7)
assert.match(imported.note, /Sheet1.*B 列.*没有文字表头/)
assert.match(imported.note, /核对字段类型为“图片”/)
const checked = JSON.parse(await tools.get('catalog_check').execute({docId:7}))
assert.match(checked.approveUrl, /^http/)
assert.match(checked.note, /Sheet1.*B 列.*没有文字表头/)
imageHintMapped = false
const missingField = JSON.parse(await tools.get('catalog_import').execute({path:'/tmp/products.xlsx', phase:'template', mode:'new'}))
assert.match(missingField.note, /补上“图片”字段/)
assert.match(JSON.parse(await tools.get('catalog_check').execute({docId:7})).note, /补上“图片”字段/)
assert.equal(JSON.parse(await tools.get('catalog_stats').execute({category:'cat_custom'})).total, 2)
assert.equal(JSON.parse(await tools.get('catalog_search').execute({imagePath:'/tmp/product.jpg',topK:3})).hits[0].product_id, 'p1')
assert.match(JSON.parse(await tools.get('catalog_quote').execute({
  items:[{category:'curler',product_id:'p1',quantity:40}],depositPercent:30})).path, /quote\.xlsx$/)
assert.equal(JSON.parse(await tools.get('shop_contact_get').execute({})).shop_name, '测试档口')
assert.equal(JSON.parse(await tools.get('shop_contact_set').execute({shop_name:'新档口'})).ticket_id, 3)

const before = calls.length
await assert.rejects(tools.get('catalog_import').execute({path:''}), /Excel.*路径/)
await assert.rejects(tools.get('catalog_check').execute({docId:0}), /docId/)
await assert.rejects(tools.get('catalog_search').execute({imagePath:'',topK:3}), /图片路径/)
await assert.rejects(tools.get('catalog_search').execute({imagePath:'/tmp/a.jpg',topK:0}), /topK/)
await assert.rejects(tools.get('catalog_quote').execute({items:[]}), /至少选择一款/)
await assert.rejects(tools.get('catalog_mutate').execute({category:'curler',action:'update',changes:{颜色:'蓝'}}), /productId/)
await assert.rejects(tools.get('catalog_mutate').execute({category:'curler',action:'create',changes:{},imagePaths:[]}), /字段或图片/)
await assert.rejects(tools.get('shop_contact_set').execute({}), /至少提供一项/)
assert.equal(calls.length, before, 'invalid tool arguments must not reach the catalog service')

console.log('wechat plugin capability matrix: 7 positive + 8 negative cases passed')
