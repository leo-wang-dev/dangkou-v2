import assert from 'node:assert/strict'
import { apply } from '../engine-plugin/catalog-v2.mjs'

const tools = new Map()
await apply({tools:{register(tool){ tools.set(tool.name, tool) }}})

let calls = []
globalThis.fetch = async (url, options = {}) => {
  calls.push({url:String(url), options})
  const path = new URL(String(url)).pathname
  let body = {}
  if (path === '/import') body = {doc_id:7, est_sec:120}
  else if (path === '/import/7') body = {id:7, status:'ticketed'}
  else if (path === '/tickets') body = {tickets:[{id:9,status:'pending',ticket_type:'template_import'}]}
  else if (path === '/stats') body = {
    total: 2,
    categories: [{key: 'cat_custom', name: '跨端测试类目', total: 2, customer_visible: 2}],
    category_keys: {'跨端测试类目': 'cat_custom'},
    by_category: {'跨端测试类目': 2},
  }
  else if (path === '/search') body = {hits:[{product_id:'p1'}]}
  else if (path === '/quote') body = {path:'/tmp/quote.xlsx'}
  else if (path === '/categories/cat_custom/quote-map') body = options.method === 'PUT'
    ? {key:'cat_custom', quote_map:{price_field:'price', model_field:'model'}, quotable:true}
    : {key:'cat_custom', name:'跨端测试类目', quote_map:{}, quotable:false,
       suggestion:{model_field:'model'}, fields:[{key:'model', label:'产品型号'}]}
  else if (path === '/categories/cat_custom/visibility')
    body = {key:'cat_custom', visible:JSON.parse(options.body||'{}').visible, updated:2}
  else if (path === '/categories/cat_custom' && options.method === 'PATCH')
    body = {key:'cat_custom', name:'跨端测试类目-改名'}
  else if (path === '/shop') body = options.method === 'PATCH' ? {ticket_id:3} : {shop_name:'测试档口'}
  else body = {ticket_id:4, token:'approval-token'}
  return {ok:true, status:200, text:async()=>JSON.stringify(body)}
}

const imported = JSON.parse(await tools.get('catalog_import').execute({path:'/tmp/products.xlsx', phase:'template', mode:'new', sourceKey:'supplier-a'}))
assert.equal(imported.docId, 7)
assert.equal(imported.phase, 'template')
await tools.get('catalog_import').execute({path:'/tmp/products.xlsx', phase:'products', mode:'new', templateDocId:7, sourceKey:'supplier-a'})
const productImportBody = JSON.parse(calls.at(-1).options.body)
assert.equal(productImportBody.phase, 'products')
assert.equal(productImportBody.template_doc_id, 7)
await tools.get('catalog_import').execute({path:'/tmp/products.xlsx', phase:'template', mode:'existing', categoryKey:'cat_custom', sourceKey:'supplier-a'})
const existingImportBody = JSON.parse(calls.at(-1).options.body)
assert.equal(existingImportBody.mode, 'existing')
assert.equal(existingImportBody.category_key, 'cat_custom')
assert.match(JSON.parse(await tools.get('catalog_check').execute({docId:7})).approveUrl, /^http/)
assert.equal(JSON.parse(await tools.get('catalog_stats').execute({category:'cat_custom'})).total, 2)
assert.equal(JSON.parse(await tools.get('catalog_search').execute({imagePath:'/tmp/product.jpg',topK:3})).hits[0].product_id, 'p1')
assert.match(JSON.parse(await tools.get('catalog_quote').execute({
  items:[{category:'curler',product_id:'p1',quantity:40}],depositPercent:30})).path, /quote\.xlsx$/)
assert.equal(JSON.parse(await tools.get('shop_contact_get').execute({})).shop_name, '测试档口')
assert.equal(JSON.parse(await tools.get('shop_contact_set').execute({shop_name:'新档口'})).ticket_id, 3)

// 动态分类报价链路：quote-map 查询/配置 + 分类改名 + 动态分类出单
const qmap = JSON.parse(await tools.get('quote_map_get').execute({categoryKey:'cat_custom'}))
assert.equal(qmap.quotable, false)
assert.equal(qmap.suggestion.model_field, 'model')
const qmapSet = JSON.parse(await tools.get('quote_map_set').execute({
  categoryKey:'cat_custom', priceField:'报价（不含税不含运）', modelField:'产品型号', ctnField:'箱规'}))
assert.equal(qmapSet.quotable, true)
const renamed = JSON.parse(await tools.get('category_rename').execute({categoryKey:'cat_custom', newName:'跨端测试类目-改名'}))
assert.equal(renamed.name, '跨端测试类目-改名')
assert.match(JSON.parse(await tools.get('catalog_quote').execute({
  items:[{category:'cat_custom',product_id:'p1',quantity:40}],depositPercent:30})).path, /quote\.xlsx$/)
const renameCall = calls.find(c => c.options.method === 'PATCH' && !c.url.includes('/visibility')
  && c.url.endsWith('/categories/cat_custom'))
assert.equal(JSON.parse(renameCall.options.body).name, '跨端测试类目-改名')
const hidden = JSON.parse(await tools.get('category_visibility').execute({categoryKey:'cat_custom', visible:false}))
assert.equal(hidden.updated, 2)
assert.match(hidden.note, /不可见/)

const before = calls.length
await assert.rejects(tools.get('catalog_import').execute({path:'', phase:'template', mode:'new'}), /Excel.*路径/)
await assert.rejects(tools.get('catalog_import').execute({path:'/tmp/a.xlsx', phase:'template', mode:'existing'}), /categoryKey/)
await assert.rejects(tools.get('catalog_import').execute({path:'/tmp/a.xlsx', phase:'products', mode:'new'}), /templateDocId/)
await assert.rejects(tools.get('catalog_check').execute({docId:0}), /docId/)
await assert.rejects(tools.get('catalog_search').execute({imagePath:'',topK:3}), /图片路径/)
await assert.rejects(tools.get('catalog_search').execute({imagePath:'/tmp/a.jpg',topK:0}), /topK/)
await assert.rejects(tools.get('catalog_quote').execute({items:[]}), /至少选择一款/)
await assert.rejects(tools.get('quote_map_get').execute({categoryKey:''}), /categoryKey/)
await assert.rejects(tools.get('quote_map_set').execute({categoryKey:'cat_custom', priceField:'x'}), /modelField/)
await assert.rejects(tools.get('category_rename').execute({categoryKey:'cat_custom', newName:' '}), /newName/)
await assert.rejects(tools.get('category_visibility').execute({categoryKey:'cat_custom'}), /visible/)
await assert.rejects(tools.get('catalog_mutate').execute({category:'curler',action:'update',changes:{颜色:'蓝'}}), /productId/)
await assert.rejects(tools.get('catalog_mutate').execute({category:'curler',action:'create',changes:{},imagePaths:[]}), /字段或图片/)
await assert.rejects(tools.get('shop_contact_set').execute({}), /至少提供一项/)
assert.equal(calls.length, before, 'invalid tool arguments must not reach the catalog service')

console.log('wechat plugin capability matrix: 13 positive + 13 negative cases passed')
