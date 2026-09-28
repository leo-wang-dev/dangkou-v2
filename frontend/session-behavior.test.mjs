// Execute the actual four shipped reset handlers; only DOM/platform/network seams are faked.
import { readFileSync } from 'node:fs'
import vm from 'node:vm'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { translate, fieldLabel } from './src/i18n.js'
import { fileURLToPath } from 'node:url'
const root = fileURLToPath(new URL('../', import.meta.url))

function harness(surface, endStatus) {
  const isStatic=surface.startsWith('static'), tool=surface.endsWith('tool')
  const path=isStatic ? `static/${tool?'tool/index':'cs/chat'}.html` : `frontend/src/pages/${tool?'tool/tool':'chat/chat'}.vue`
  let code=readFileSync(root+path,'utf8').match(/<script(?: setup)?>([\s\S]*?)<\/script>/)[1]
  code=code.replace(/^import .*$/gm,'')
  if(isStatic)code=code.slice(0,code.indexOf(tool?'(async function init(){':'ready=(async function init(){'))
  const values=new Map(), nodes=new Map(), calls=[], tips=[]
  const store={get:k=>values.get(k)||'',set:(k,v)=>values.set(k,v),remove:k=>values.delete(k),getItem:k=>values.get(k)||'',setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)}
  const node=()=>({textContent:'',style:{},hidden:false,replaceChildren(){this.cleared=true},append(){},appendChild(){},className:''})
  async function end(){calls.push('end');if(endStatus==='network')throw Error('offline');return {ok:endStatus===200,status:endStatus}}
  async function issue(){calls.push('issue');return {ok:true,status:200,data:{guest:'fresh',visitor:'fresh'}}}
  async function state(){return {ok:true,status:200,data:{notes:[],batches:[],photo_mode:'',intent_required:false}}}
  const context=vm.createContext({console,Promise,Map,ref:value=>({value}),nextTick:fn=>fn(),onLoad(){},storage:store,sessionStorage:store,localStorage:store,
    location:{pathname:'/cs/chat/shop'},setTimeout(){},clearTimeout(){},encodeURIComponent,
    document:{getElementById:id=>{if(!nodes.has(id))nodes.set(id,node());return nodes.get(id)},createElement:node,createTextNode:t=>t,querySelectorAll:()=>[]},
    uni:{showToast:x=>tips.push(x.title)},getBases:()=>({tool:'',cs:''}),
    toolApi:{endSession:end,newGuest:issue,notes:state},csApi:{endSession:end,newSession:issue,session:state},
    fetch:async url=>{let r;if(url.includes('/end'))r=await end();else if(url==='guest'||url.endsWith('/session'))r=await issue();else r=await state();return {...r,json:async()=>r.data||{}}}
  })
  context.useCustomerLanguage=()=>({locale:{value:'zh'},dir:{value:'ltr'},t:(key,values)=>translate(key,values,'zh'),label:value=>fieldLabel(value,'zh'),changeLanguage(){}})
  context.CustomerI18n={...context.useCustomerLanguage(),mount(){},request:(...args)=>context.fetch(...args)}
  vm.runInContext(code,context)
  const setup=isStatic ? (tool?"TOKEN=''; GUEST='stale'; sessionStorage.setItem('ut_guest','stale')":"visitor='stale'; ready=Promise.resolve(); sessionStorage.setItem('h5v:shop','stale')") : (tool?"token.value='';guest.value='stale';storage.set('ut_guest','stale')":"token.value='shop';visitor.value='stale';storage.set('h5v:shop','stale')")
  vm.runInContext(setup,context)
  return {calls,tips,values,nodes,context,run:()=>vm.runInContext(!isStatic&&tool?'endSession()':'newSession(true)',context),identity:()=>vm.runInContext(isStatic?(tool?'GUEST':'visitor'):(tool?'guest.value':'visitor.value'),context)}
}

for(const surface of ['static-tool','static-chat','uni-tool','uni-chat']) {
  for(const status of [401,410])test(`${surface} resets swept/expired guest (${status})`,async()=>{
    const h=harness(surface,status);await h.run();assert.deepEqual(h.calls,['end','issue']);assert.equal(h.identity(),'fresh');assert.equal([...h.values.values()].includes('stale'),false)
  })
  for(const status of [500,0,'network'])test(`${surface} preserves session on real end failure (${status})`,async()=>{
    const h=harness(surface,status);await h.run();assert.deepEqual(h.calls,['end']);assert.equal(h.identity(),'stale')
  })
}

for(const surface of ['static-chat','uni-chat'])test(`${surface} exposes retained failure then retries or discards explicitly`,async()=>{
  const h=harness(surface,200), isStatic=surface.startsWith('static')
  let pending=true,fail=true
  const state=async()=>({ok:true,status:200,data:{photo_mode:'notes',intent_required:pending,notes:[],batches:[]}})
  const mode=async()=>{h.calls.push('mode');if(fail)return {ok:false,status:500,data:null};pending=false;return {ok:true,status:200,data:{reply:'Recovered'}}}
  const discard=async()=>{h.calls.push('discard');pending=false;return {ok:true,status:200,data:{discarded:true}}}
  h.context.csApi.session=state;h.context.csApi.setMode=mode;h.context.csApi.discardPhoto=discard
  h.context.fetch=async url=>{const r=await (url.endsWith('/mode')?mode():url.endsWith('/discard')?discard():state());return {...r,json:async()=>r.data||{}}}
  const visible=()=>isStatic?!h.nodes.get('pending-photo').hidden:vm.runInContext('hasPending.value',h.context)
  await vm.runInContext('refreshSession()',h.context);assert.equal(visible(),true)
  await vm.runInContext("setMode('notes')",h.context);assert.equal(visible(),true)
  fail=false;await vm.runInContext('retryPhoto()',h.context);assert.equal(visible(),false)
  pending=true;await vm.runInContext('refreshSession()',h.context);assert.equal(visible(),true)
  await vm.runInContext('discardPhoto()',h.context);assert.equal(visible(),false)
  assert.deepEqual(h.calls,['mode','mode','discard'])
})
