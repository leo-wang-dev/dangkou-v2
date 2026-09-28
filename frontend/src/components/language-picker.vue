<template><picker :range="languages" range-key="name" :value="index" @change="choose"><view class="language-picker">{{ t('languageLabel') }}: {{ languages[index].name }} ▾</view></picker></template>
<script setup>
import { computed } from 'vue'
import { useCustomerLanguage } from '../use-language.js'
const emit=defineEmits(['change'])
const {locale,languages,t,changeLanguage}=useCustomerLanguage()
const index=computed(()=>languages.findIndex(x=>x.code===locale.value))
function choose(event){const code=languages[Number(event.detail.value)].code;changeLanguage(code);emit('change',code)}
</script>
<style scoped>.language-picker{padding:10px;border:1px solid #ccc;border-radius:8px;background:white;font-size:14px;text-align:start}</style>
