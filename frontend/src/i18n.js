import catalog from './customer-catalog.js'
import sourceAliases from './customer-sources.js'
import { storage } from './storage.js'
export const languages=catalog.codes.map(code=>({code,name:catalog.nativeNames[code]}))
export function normalizeLanguage(value){return languages.find(x=>x.code===value||x.name===value)?.code||'zh'}
export function getLanguage(){return normalizeLanguage(storage.get('dk_lang','zh'))}
export function setLanguage(value){const code=normalizeLanguage(value);storage.set('dk_lang',code);return code}
export function direction(lang=getLanguage()){return lang==='ar'?'rtl':'ltr'}
export function translate(key, values={}, lang=getLanguage()){
  return (catalog.strings[key]?.[normalizeLanguage(lang)]||key).replace(/\{(\w+)\}/g,(_,name)=>String(values[name]??''))
}
const bySource={...sourceAliases,...Object.fromEntries(Object.entries(catalog.strings).map(([key,value])=>[value.zh,key]))}
export function fieldLabel(value,lang=getLanguage()){return bySource[value]?translate(bySource[value],{},lang):value}

export function displayValue(value,lang=getLanguage()){
  if(lang==='zh')return value
  if(['未拍到','待补充'].includes(value))return translate('notRecorded',{},lang)
  if(['模糊','模糊（待确认）'].includes(value))return translate('unclear',{},lang)
  return typeof value==='string'?value.replaceAll('（照片识别，待确认）',' ('+translate('statusDraft',{},lang)+')'):value
}
