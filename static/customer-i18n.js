/* Fixed UI copy only. Customer/supplier data is never rewritten by DOM localization. */
(function(){
  const catalog=globalThis.CustomerCatalog, aliases=globalThis.CustomerSources;
  const normalize=value=>catalog.codes.includes(value)?value:Object.keys(catalog.nativeNames).find(k=>catalog.nativeNames[k]===value)||'zh';
  let language=normalize(localStorage.getItem('dk_lang')||'zh');
  function t(key,values={}){return (catalog.strings[key]?.[language]||key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''))}
  function display(value){if(language==='zh')return value;if(['未拍到','待补充'].includes(value))return t('notRecorded');if(['模糊','模糊（待确认）'].includes(value))return t('unclear');return typeof value==='string'?value.replaceAll('（照片识别，待确认）',' ('+t('statusDraft')+')'):value}
  function label(value){return aliases[value]?t(aliases[value]):value}
  function apply(){
    document.documentElement.lang=language;document.documentElement.dir=language==='ar'?'rtl':'ltr';
    for(const node of document.querySelectorAll('[data-i18n]'))node.textContent=(node.dataset.icon||'')+t(node.dataset.i18n);
    for(const node of document.querySelectorAll('[data-i18n-placeholder]'))node.placeholder=t(node.dataset.i18nPlaceholder);
    for(const node of document.querySelectorAll('[data-i18n-title]'))node.title=t(node.dataset.i18nTitle);
    for(const node of document.querySelectorAll('[aria-label],[alt]'))for(const attr of ['aria-label','alt']){
      const keyAttr=attr==='aria-label'?'i18nAriaLabel':'i18nAlt';
      const key=node.dataset[keyAttr]||aliases[node.getAttribute(attr)];
      if(key){node.dataset[keyAttr]=key;node.setAttribute(attr,t(key))}
    }
  }
  function setLanguage(value){language=normalize(value);localStorage.setItem('dk_lang',language);apply();const picker=document.getElementById('customer-language');if(picker)picker.value=language}
  function mount(change){
    const holder=document.createElement('label');holder.className='customer-language';
    const span=document.createElement('span');span.dataset.i18n='languageLabel';span.textContent=t('languageLabel');holder.append(span);
    const picker=document.createElement('select');picker.id='customer-language';picker.dataset.i18nAriaLabel='chooseLanguage';picker.setAttribute('aria-label',t('chooseLanguage'));
    for(const code of catalog.codes){const option=document.createElement('option');option.value=code;option.textContent=catalog.nativeNames[code];picker.append(option)}
    picker.value=language;picker.onchange=async()=>{language=normalize(picker.value);localStorage.setItem('dk_lang',language);apply();await change?.(language);location.reload()};holder.append(picker);
    document.body.prepend(holder);apply();
  }
  function request(path,options={}){return fetch(path,{...options,headers:{'X-Customer-Language':language,...options.headers}})}
  globalThis.CustomerI18n={t,label,display,mount,request,setLanguage,get language(){return language}};
})();
