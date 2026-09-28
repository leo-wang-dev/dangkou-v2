import assert from 'node:assert/strict'
const calls=[]
globalThis.fetch=async (url,opts)=>{calls.push([new URL(url,'https://example.test/tool/#/pages/chat/chat').href,opts]);return {ok:true,status:200,json:async()=>({})}}
const {csApi,toolApi}=await import('./src/api.js')
await csApi.newSession('shop')
assert.equal(calls.pop()[0],'https://example.test/cs/chat/shop/session')
await csApi.send('shop','hello','visitor')
assert.equal(calls.pop()[0],'https://example.test/cs/chat/shop/message')
await toolApi.newGuest()
assert.equal(calls.pop()[0],'https://example.test/tool/guest')
globalThis.location={href:'https://example.test/tool/#/pages/chat/chat'}
assert.equal(csApi.photoUrl('/cs/link/list/note/1/photo'),'https://example.test/cs/link/list/note/1/photo')
console.log('Customer URLs resolve correctly from /tool/')
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
const json=JSON.parse(readFileSync(new URL('./src/customer-languages.json',import.meta.url),'utf8'))
const aliases=JSON.parse(readFileSync(new URL('./src/customer-source-aliases.json',import.meta.url),'utf8'))
const durable=new Map(),session=new Map()
globalThis.localStorage={getItem:k=>durable.get(k),setItem:(k,v)=>durable.set(k,v),removeItem:k=>durable.delete(k)}
globalThis.sessionStorage={getItem:k=>session.get(k),setItem:(k,v)=>session.set(k,v),removeItem:k=>session.delete(k)}
const i18n=await import('./src/i18n.js'),{storage}=await import('./src/storage.js')
for(const code of json.codes){
  i18n.setLanguage(code)
  assert.equal(i18n.getLanguage(),code)
  for(const [key,values] of Object.entries(json.strings)){
    assert.equal(Object.keys(values).length,13)
    assert.equal(typeof values[code],'string');assert.ok(values[code].trim())
    assert.deepEqual([...values[code].matchAll(/\{(\w+)\}/g)].map(x=>x[1]).sort(),[...values.zh.matchAll(/\{(\w+)\}/g)].map(x=>x[1]).sort())
    if(!values[code].includes('{'))assert.equal(i18n.translate(key),values[code])
  }
  assert.equal(i18n.direction(),code==='ar'?'rtl':'ltr')
  assert.ok(i18n.translate('loginMerged',{email:'buyer@example.test'}).includes('buyer@example.test'))
  await csApi.send('shop','', 'visitor','contact_owner')
  const call=calls.pop();assert.equal(call[1].headers['X-Customer-Language'],code);assert.equal(JSON.parse(call[1].body).action,'contact_owner')
}
i18n.setLanguage('English');assert.equal(i18n.getLanguage(),'en')
i18n.setLanguage('中文');assert.equal(i18n.getLanguage(),'zh')
storage.set('ut_guest','temporary');storage.set('h5v:shop','temporary-shop');storage.set('dk_lang','ar')
session.clear();assert.equal(storage.get('ut_guest'),'');assert.equal(storage.get('h5v:shop'),'');assert.equal(storage.get('dk_lang'),'ar')
const classic=vm.createContext({});vm.runInContext(readFileSync(new URL('../static/customer-catalog.js',import.meta.url),'utf8'),classic)
assert.equal(JSON.stringify(classic.CustomerCatalog),JSON.stringify(json))
vm.runInContext(readFileSync(new URL('../static/customer-sources.js',import.meta.url),'utf8'),classic)
assert.equal(JSON.stringify(classic.CustomerSources),JSON.stringify(aliases))
console.log('13 locales, placeholders, language headers/actions, RTL and session-independent preference passed')

// Execute the shipped merchant quote handler, including invalid quantity, download and failure.
const html=readFileSync(new URL('../static/index.html',import.meta.url),'utf8')
const quoteCode=html.slice(html.indexOf('async function submitQuote'),html.indexOf('async function toggleProductVisible'))
const nodes={'quote-quantity':{value:'50'},'quote-language':{value:'ar'},'quote-submit':{},'quote-result':{replaceChildren(n){this.link=n},append(n){this.note=n}}}
let payload,downloads=0,fail=false
const quoteContext=vm.createContext({Number,JSON,Error,cur:{cat:'supplier-category'},MANAGE_PREFIX:'/merchant/manage/example',H:{},setTimeout(){},
 document:{getElementById:id=>nodes[id],createTextNode:s=>s,createElement:()=>({click(){downloads++}})},URL:{createObjectURL:()=> 'blob:local',revokeObjectURL(){}},
 api:async (path,options)=>{assert.equal(path,'/quote');payload=JSON.parse(options.body);if(fail)throw Error('明确错误');return {download_url:'/quotes/quote-12345678.xlsx',quantity_adjustment_note:'需求50，按整箱报80'}},
 fetch:async url=>{assert.equal(url,'/merchant/manage/example/quotes/quote-12345678.xlsx');return {ok:true,blob:async()=>({})}}
})
vm.runInContext(quoteCode,quoteContext)
await vm.runInContext("submitQuote('exact-product')",quoteContext)
assert.deepEqual(payload,{items:[{category:'supplier-category',product_id:'exact-product',quantity:50}],target_language:'ar'})
assert.equal(downloads,1);assert.equal(nodes['quote-result'].link.href,'blob:local');assert.match(nodes['quote-result'].note,/整箱报80/)
nodes['quote-quantity'].value='0';await vm.runInContext("submitQuote('exact-product')",quoteContext);assert.equal(nodes['quote-result'].textContent,'采购数量必须为正整数');assert.equal(downloads,1)
nodes['quote-quantity'].value='50';fail=true;await vm.runInContext("submitQuote('exact-product')",quoteContext);assert.equal(nodes['quote-result'].textContent,'明确错误');assert.equal(nodes['quote-submit'].disabled,false)
console.log('Merchant quote control identity, language, validation, download and failure passed')
