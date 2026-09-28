import assert from 'node:assert/strict'
import { apply } from '../engine-plugin/catalog-v2.mjs'
const tools = new Map()
await apply({tools:{register(tool){tools.set(tool.name,tool)}}})
let payload
globalThis.fetch = async (url,options) => {payload=JSON.parse(options.body);return ({ok:true,status:200,text:async()=>JSON.stringify({
  path:'/tmp/quote.xlsx',items:[{supplier:'厂乙',requested_quantity:50,quoted_quantity:80}],
  quantity_adjustment_note:'需求50，按整箱报80'
})})}
const quote = JSON.parse(await tools.get('catalog_quote').execute({items:[{category:'cat_custom',product_id:'p1',quantity:50}]}))
assert.equal(quote.items[0].quoted_quantity,80)
assert.equal(quote.items[0].requested_quantity,50)
assert.match(quote.note,/整箱报80/)
assert.equal(payload.target_language,'zh')
await tools.get('catalog_quote').execute({items:[{category:'cat_custom',product_id:'p1',quantity:50}],target_language:'fr'})
assert.equal(payload.target_language,'fr')
assert.deepEqual(payload.items,[{category:'cat_custom',product_id:'p1',quantity:50}])
console.log('supplier quote plugin quantities, explanation and explicit/default target language passed')
