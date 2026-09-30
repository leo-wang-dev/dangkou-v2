import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

function page(search='', values={}) {
  const calls=[]; const storage=new Map(Object.entries(values));
  const win={location:{search, pathname:'/', origin:'https://shop.example', href:'https://shop.example/'+search},
    sessionStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},
    history:{replaceState(){}},
    fetch:async(url,opt)=>{calls.push({url,opt}); return {ok:true,status:200,json:async()=>({csrfToken:'csrf',actorId:'u'})};}};
  vm.runInNewContext(fs.readFileSync(new URL('../static/app-auth.js',import.meta.url),'utf8'),{window:win,URL,URLSearchParams,Headers});
  return {auth:win.createCatalogAuth(''),win,calls,storage};
}

test('legacy links keep their service token and do not request APP session',async()=>{
  const {auth,calls}=page('?t=old-token');
  await auth.fetch('/categories');
  assert.equal(calls.length,1);
  assert.equal(new Headers(calls[0].opt.headers).get('X-Service-Token'),'old-token');
  assert.equal(auth.token,'old-token');
});

test('APP mode ignores stale credentials and loads one CSRF session for concurrent requests',async()=>{
  const {auth,calls,storage}=page('?app_session=1',{'catalog-service-token:/':'old-token'});
  await Promise.all([auth.fetch('/categories'),auth.fetch('/tickets/1/decision',{method:'POST',headers:{'X-Service-Token':'stale'},body:'{}'})]);
  assert.equal(calls.filter(x=>x.url==='/app-entry/session').length,1);
  const write=calls.find(x=>x.url==='/tickets/1/decision');
  const h=new Headers(write.opt.headers);
  assert.equal(h.get('X-Service-Token'),null);
  assert.equal(h.get('X-Catalog-App'),'1');
  assert.equal(h.get('X-CSRF-Token'),'csrf');
  assert.equal(auth.token,'');
  assert.equal(storage.has('catalog-service-token:/'),false);
  assert.equal(auth.url('/ticketimg/1/a.png?t=raw-ticket-token'),'/ticketimg/1/a.png');
  assert.equal(auth.url('/img/a.png?token='),'/img/a.png');
});

test('APP mode persists on reload; a fresh explicit legacy link can select legacy mode',()=>{
  const {auth}=page('',{'catalog-app-mode:/':'1','catalog-service-token:/':'stale'});
  assert.equal(auth.appMode,true);
  const next=page('?t=legacy',{'catalog-app-mode:/':'1'});
  assert.equal(next.auth.appMode,false);
  assert.equal(next.auth.token,'legacy');
});

test('failed APP session cannot send writes using stale token',async()=>{
  const {auth,win,calls}=page('?app_session=1',{'catalog-service-token:/':'old'});
  win.fetch=async()=>({ok:false,status:401});
  await assert.rejects(()=>auth.fetch('/categories',{method:'POST'}),/APP/);
  assert.equal(calls.length,0);
});

test('credentials are never sent to another origin',async()=>{
  const {auth,calls}=page('?app_session=1');
  await assert.rejects(()=>auth.fetch('https://evil.example/api'),/地址/);
  assert.equal(calls.length,0);
});
