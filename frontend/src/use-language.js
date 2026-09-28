import { ref, computed, watch } from 'vue'
import { languages, getLanguage, setLanguage, translate, fieldLabel, direction, displayValue } from './i18n.js'
const locale=ref(getLanguage())
export function useCustomerLanguage(titleKey){
  watch(locale,value=>{
    if(typeof document!=='undefined'){document.documentElement.lang=value;document.documentElement.dir=direction(value)}
    if(titleKey && typeof uni!=='undefined')uni.setNavigationBarTitle({title:translate(titleKey,{},value)})
  },{immediate:true})
  return {locale,languages,dir:computed(()=>direction(locale.value)),
    display:value=>displayValue(value,locale.value),
    t:(key,values)=>translate(key,values,locale.value),label:value=>fieldLabel(value,locale.value),
    changeLanguage:value=>{locale.value=setLanguage(value);if(typeof document!=='undefined'){document.documentElement.lang=locale.value;document.documentElement.dir=direction(locale.value)}}}
}
