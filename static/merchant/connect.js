'use strict';
const key=new URLSearchParams(location.search).get('k');
history.replaceState(null,'',location.pathname);
let confirmed=null;
const form=document.querySelector('#form'), input=document.querySelector('#token'), status=document.querySelector('#status'), button=document.querySelector('#submit');
input.addEventListener('input',()=>{confirmed=null;button.textContent='验证 bot 身份';});
form.addEventListener('submit',async e=>{
 e.preventDefault();button.disabled=true;
 try{
  const response=await fetch('/merchant/binding',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({key,token:input.value,confirm_bot_id:confirmed})});
  const result=await response.json();
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'请求无效，请重新打开接入链接。');
  if(result.bound){input.value='';form.hidden=true;status.textContent='已绑定 @'+result.username+'。请回商家助手回复“启用客服”，再查看进度并打开客服 bot 测试。';}
  else{confirmed=result.bot_id;status.textContent='档口：'+result.shop+'\n客服：@'+result.username+'\nbot ID：'+result.bot_id+'\n请核对无误后确认。';button.textContent='确认绑定此客服 bot';}
 }catch(e){status.textContent=e.message;}finally{button.disabled=false;}
});
