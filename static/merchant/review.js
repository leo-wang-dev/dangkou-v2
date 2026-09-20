'use strict';
let credential='';
const message=document.querySelector('#message'),list=document.querySelector('#requests');
const inherited=new URLSearchParams(location.hash.slice(1)).get('k');
if(inherited){document.querySelector('#credential').value=inherited;history.replaceState(null,'',location.pathname);}
async function api(path,options={}){
 const r=await fetch(path,{...options,headers:{'Content-Type':'application/json','X-Service-Token':credential}});
 const result=await r.json();
 if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'请求格式不正确');
 return result;
}
function input(parent,label,value='',type='text'){
 const el=document.createElement('label');el.append(document.createTextNode(label));
 const field=document.createElement('input');field.type=type;if(type==='checkbox')field.checked=Boolean(value);else field.value=value;el.append(field);parent.append(el);return field;
}
async function load(){
 try{
  const result=await api('/merchant/ownership-requests');list.replaceChildren();
  document.querySelector('#refresh').hidden=false;
  message.textContent=result.requests.length?'':'暂无认领申请';
  for(const row of result.requests){
   const card=document.createElement('article');list.append(card);
   const summary=document.createElement('pre');summary.textContent='档口：'+(row.shop?.shop_name||'未命名')+'\n档口编号：'+(row.shop?.stall_no||row.shop?.shop_id||'待核实')+'\n申请：'+row.id+'\nTG 用户 ID：'+row.owner+'\n状态：'+row.status;card.append(summary);
   if(row.status!=='pending')continue;
   const old=row.historical_contact;
   const select=document.createElement('select');select.setAttribute('aria-label','原登记联系方式类型');
   for(const [v,t] of [['phone','原登记手机号'],['wechat','原登记微信号']]){const o=document.createElement('option');o.value=v;o.textContent=t;select.append(o);}
   select.value=old?.kind||'phone';card.append(select);
   const value=input(card,'原登记联系方式',old?.value||'');
   const source=input(card,'原始资料来源或凭据编号',old?.source_reference||'');
   const operator=input(card,'审核人',old?.recorded_by||'');
   const save=document.createElement('button');save.textContent='保存原始登记资料';card.append(save);
   save.onclick=async()=>{save.disabled=true;try{await api('/merchant/ownership-requests/'+row.id+'/contact',{method:'PUT',body:JSON.stringify({kind:select.value,value:value.value,source_reference:source.value,recorded_by:operator.value})});await load();message.textContent='已保存。请申请人在 bot 发送“核对联系方式 原登记号码或微信号”；保存新底册会使旧匹配失效。';}catch(e){message.textContent=e.message;}finally{save.disabled=false;}};
   const status=document.createElement('p');status.textContent=old&&row.contact_revision===old.revision?'登记资料已匹配，仍需联系本人确认。':'申请人尚未完成当前底册匹配。';card.append(status);
   const confirm=input(card,'已通过上述原登记电话或微信确认申请人本人',false,'checkbox');
   const evidence=input(card,'本次核验记录编号或说明');
   for(const [approved,label] of [[true,'批准认领'],[false,'拒绝认领']]){
    const b=document.createElement('button');b.textContent=label;card.append(b);
    b.onclick=async()=>{b.disabled=true;try{await api('/merchant/ownership-requests/'+row.id+'/review',{method:'POST',body:JSON.stringify({approved,reviewer:operator.value,evidence_reference:evidence.value,holder_confirmed:confirm.checked,contact_revision:old?.revision??null})});await load();message.textContent=approved?'已批准。申请人回复“认证进度”即可继续接入。':'已拒绝。';}catch(e){message.textContent=e.message;}finally{b.disabled=false;}};
   }
  }
 }catch(e){message.textContent=e.message;}
}
document.querySelector('#login').onsubmit=e=>{e.preventDefault();credential=document.querySelector('#credential').value;document.querySelector('#credential').value='';load();};
document.querySelector('#refresh').onclick=load;
