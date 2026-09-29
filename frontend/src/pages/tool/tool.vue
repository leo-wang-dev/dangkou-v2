<template>
  <view class="page" :class="{ 'compact-viewport': compactViewport }" :style="{ direction: dir, height: viewportHeight ? viewportHeight + 'px' : '' }">
    <language-picker @change="pickLang" />
    <!-- 页头：身份 + 登录/退出（标题栏由 pages.json 提供） -->
    <view class="header">
      <text class="who">{{ token ? (email || t('wechatUser')) : t('guestMode') }}</text>
      <!-- #ifdef MP-WEIXIN -->
      <button v-if="loginMethod !== 'wechat'" class="hbtn" :disabled="loggingIn" @click="doWechatLogin">{{ token ? t('bindWechat') : t('wechatLogin') }}</button>
      <!-- #endif -->
      <button v-if="token && loginMethod === 'wechat'" class="hbtn" @click="loginBar = !loginBar">{{ t('bindEmail') }}</button>
      <button class="hbtn" @click="toggleLogin">{{ token ? t('logoutShort') : t('login') }}</button>
    </view>

    <!-- 登录条（邮箱验证码） -->
    <view v-if="loginBar" class="loginbar">
      <view class="row">
        <input v-model="email" class="inp" type="text" :placeholder="t('email')" />
        <button class="btn" :disabled="sending" @click="sendCode">{{ t('sendCodeShort') }}</button>
      </view>
      <view class="row">
        <input v-model="code" class="inp" type="number" :placeholder="t('verificationCode')" />
        <button class="btn" :disabled="!codeSent || loggingIn" @click="doLogin">{{ loginMethod === 'wechat' ? t('bindEmail') : t('login') }}</button>
      </view>
      <view class="hint">{{ t('loginHint') }}</view>
      <view class="cfg" @click="cfgOpen = true">{{ t('server') }}: {{ bases.tool || t('sameOrigin') }} / {{ bases.cs || t('sameOrigin') }} · {{ t('tapToEdit') }}</view>
    </view>

    <!-- 消息区 -->
    <view class="session-controls">
      <button @click="endSession">{{ t('endSession') }}</button><button @click="manualBatch">{{ t('manualSwitchShop') }}</button>
      <label v-for="n in unassigned" :key="n.id"><checkbox :checked="selected.includes(n.id)" @click="toggleNote(n.id)" />{{ t('attachNotes') }} {{ n.id }} {{ n.fields['型号或品名'] }}</label>
      <button v-for="b in batches.filter(x=>['pending','confirmed'].includes(x.state))" :key="b.id" @click="confirmBatch(b.id)">{{ b.active ? t('currentShop') : t('confirmSwitch') }}{{ b.fields['档口名称'] || t('shopPending') }}</button>
      <button v-for="b in batches.filter(x=>x.state==='pending')" :key="'decline'+b.id" @click="confirmBatch(b.id,null,'decline')">{{ t('declineCard') }}: {{ b.fields['档口名称'] }}</button>
    </view>
    <scroll-view class="log" scroll-y :scroll-top="tail" scroll-with-animation>
      <view v-for="(m, i) in messages" :key="i" class="msg" :class="m.role === 'me' ? 'me' : 'bot'">{{ m.text }}</view>
    </scroll-view>

    <!-- 底部操作 -->
    <view class="nav">
      <button class="navbtn" @click="photo">📷 {{ t('takePhoto') }}</button>
      <button class="navbtn ghost" @click="openList">📋 {{ t('listShort') }}</button>
      <button class="navbtn ghost" @click="exportXlsx">⬇ {{ t('exportExcel') }}</button>
    </view>

    <!-- 清单抽屉（H5 与小程序共用组件实现；小程序无 iframe，直接内嵌表格/列表） -->
    <view v-if="drawer" class="drawer" :style="{ height: viewportHeight ? viewportHeight + 'px' : '' }">
      <view class="mask" @click="drawer = false" />
      <view class="aside">
        <view class="dhead">
          <text class="dtitle">📋 {{ t('myShortList') }}</text>
          <button class="hbtn ghost" @click="openList">↻ {{ t('refresh') }}</button>
          <button class="hbtn" @click="drawer = false">✕ {{ t('close') }}</button>
        </view>
        <view v-if="!notes.length" class="dempty">{{ t('toolListEmpty') }}</view>
        <scroll-view v-else class="dlist" scroll-y>
          <view v-for="(n, i) in notes" :key="n.id" class="note">
            <image v-if="n.photo" class="thumb" :src="photoUrl(n)" mode="aspectFill" />
            <view class="nf">
              <text class="nf-text">【{{ i + 1 }}】
{{ noteText(n) }}</text>
              <text class="nt-time">{{ n.created_at || '' }}</text>
            </view>
          </view>
        </scroll-view>
      </view>
    </view>

    <!-- baseURL 运行时配置（开发期切服务器用） -->
    <view v-if="cfgOpen" class="drawer">
      <view class="mask" @click="cfgOpen = false" />
      <view class="cfgbox">
        <view class="dhead"><text class="dtitle">{{ t('serverAddress') }}</text><button class="hbtn" @click="cfgOpen = false">✕ {{ t('close') }}</button></view>
        <view class="cfgbody">
          <text class="cfglabel">{{ t('toolBase') }} ({{ t('h5EmptySameOrigin') }})</text>
          <input v-model="cfgTool" class="inp" placeholder="https://host/tool" />
          <text class="cfglabel">{{ t('chatBase') }} ({{ t('h5EmptySameOrigin') }})</text>
          <input v-model="cfgCs" class="inp" placeholder="https://host" />
          <button class="btn" style="margin-top: 10px;" @click="saveBases">{{ t('saveServerSettings') }}</button>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { useCustomerLanguage } from '../../use-language.js'
import LanguagePicker from '../../components/language-picker.vue'
const { locale, dir, t, label, display, changeLanguage } = useCustomerLanguage('photoTool')

import { ref, nextTick } from 'vue'
import { onLoad, onUnload } from '@dcloudio/uni-app'
import { toolApi, getBases, setBases, choosePhoto } from '../../api.js'
import { storage } from '../../storage.js'
import { wechatLoginCode } from '../../wechat-login.js'

async function pickLang(lang){changeLanguage(lang);const r=await toolApi.setLang(lang);if(!r.ok)tip(r.data?.detail||t('networkError'));if(drawer.value)await openList()}

const guest = ref('')
const viewportHeight = ref(0), compactViewport = ref(false)
function updateViewport(){
  if(typeof window==='undefined')return
  const height=window.visualViewport?.height||window.innerHeight
  viewportHeight.value=height>=100?height:0
  compactViewport.value=height<400
}
function attachViewport(){
  if(typeof window==='undefined')return
  updateViewport();window.visualViewport?.addEventListener('resize',updateViewport)
  window.visualViewport?.addEventListener('scroll',updateViewport);window.addEventListener('resize',updateViewport)
}
function detachViewport(){
  if(typeof window==='undefined')return
  window.visualViewport?.removeEventListener('resize',updateViewport)
  window.visualViewport?.removeEventListener('scroll',updateViewport);window.removeEventListener('resize',updateViewport)
}
const token = ref('')
const loginMethod = ref('email')
const email = ref('')
const code = ref('')
const codeSent = ref(null)      // null=未发码（登录禁用）；true=已发
const sending = ref(false)
const loggingIn = ref(false)
const loginBar = ref(false)
const messages = ref([])
const tail = ref(0)
const drawer = ref(false)
const notes = ref([])
const cfgOpen = ref(false)
const cfgTool = ref('')
const cfgCs = ref('')
const bases = ref({ tool: '', cs: '' })

function bubble(role, text) {
  messages.value.push({ role, text })
  nextTick(() => { tail.value += 10000 })
}

function tip(t) { uni.showToast({ title: t, icon: 'none' }) }

function photoUrl(n) { return toolApi.photoUrl(n.id) }

function noteText(n) {
  return Object.entries(n.fields || {})
    .filter(([k]) => k !== '__图框__')
    .map(([k, v]) => (n.display_labels?.[k] ?? label(k)) + '=' + (n.display_fields?.[k] ?? display(v)))
    .join('\n')
}

const batches = ref([]), unassigned = ref([]), selected = ref([])
function toggleNote(id) { selected.value = selected.value.includes(id) ? selected.value.filter(x=>x!==id) : [...selected.value,id] }
async function refreshBatches() {
  const r=await toolApi.notes(); if(!r.ok)return
  batches.value=r.data.batches||[]; unassigned.value=(r.data.notes||[]).filter(n=>['unassigned','legacy_unassigned'].includes(n.batch_state))
}
async function confirmBatch(id,fields=null,action='confirm') { const r=await toolApi.confirmBatch(id,selected.value,fields,action); if(!r.ok){tip(r.data?.detail||t('networkError'));return} selected.value=[];await refreshBatches() }
function manualBatch() { uni.showModal({title:t('newShopName'),editable:true,success:r=>{if(r.confirm&&r.content)confirmBatch(null,{'档口名称':r.content})}}) }
async function endSession() {
  if(token.value){tip(t('logout'));return}
  try {
    const r=await toolApi.endSession()
    if(!r.ok&&![401,410].includes(r.status)){tip(t('networkError'));return}
    guest.value='';storage.remove('ut_guest');messages.value=[];notes.value=[];batches.value=[];unassigned.value=[];selected.value=[];drawer.value=false
    await init()
  }catch(e){tip(t('networkError'))}
}
async function init() {
  if(token.value){const r=await toolApi.me();if(r.ok){if(r.data.lang)changeLanguage(r.data.lang);email.value=r.data.email||'';loginMethod.value=r.data.login_method||'email';storage.set('ut_email',email.value);storage.set('ut_login_method',loginMethod.value)}}
  if (!guest.value) {
    const r = await toolApi.newGuest()
    if (r.ok && r.data && r.data.guest) {
      guest.value = r.data.guest
      storage.set('ut_guest', guest.value)
    } else {
      tip(t('initError'))
    }
  }
  await refreshBatches()
  bubble('bot', t('toolWelcome'))
}

async function toggleLogin() {
  if (token.value) {
    const ended = await toolApi.endSession()
    if (!ended.ok) { tip(t('networkError')); return }
    token.value = ''
    email.value = ''
    storage.remove('ut_token')
    storage.remove('ut_email')
    storage.remove('ut_login_method');loginMethod.value='email'
    guest.value = ''; storage.remove('ut_guest'); await init()
    loginBar.value = false
    bubble('bot', t('signedOutGuest'))
    return
  }
  loginBar.value = !loginBar.value
}

async function sendCode() {
  const addr = (email.value || '').trim()
  if (!addr) { tip(t('enterEmail')); return }
  sending.value = true
  const r = await toolApi.sendCode(addr)
  sending.value = false
  if (!r.ok) { tip((r.data && r.data.detail) || t('sendError')); return }
  codeSent.value = true
  tip(t('codeSent'))
}

async function doLogin() {
  const addr = (email.value || '').trim()
  const c = (code.value || '').trim()
  if (!addr || !c) { tip(t('enterEmailCode')); return }
  loggingIn.value = true
  const r = await toolApi.verify(addr, c, token.value ? '' : guest.value, loginMethod.value === 'wechat' ? token.value : '')
  loggingIn.value = false
  if (!r.ok) { tip((r.data && r.data.detail) || t('loginError')); return }
  guest.value = ''; storage.remove('ut_guest')
  token.value = r.data.token
  email.value = r.data.email
  loginMethod.value = 'email';storage.set('ut_login_method','email')
  storage.set('ut_token', token.value)
  storage.set('ut_email', email.value)
  loginBar.value = false
  bubble('bot', t('loginMergedDetailed',{email:email.value}))
  await refreshBatches()
}

async function doWechatLogin() {
  loggingIn.value=true
  try {
    const wxCode=await wechatLoginCode()
    const r=await toolApi.wechat(wxCode, token.value ? '' : guest.value, token.value)
    if(!r.ok){tip(r.data?.detail||t('wechatLoginFailed'));return}
    token.value=r.data.token;email.value=r.data.email||'';loginMethod.value=r.data.login_method||'wechat'
    storage.set('ut_token',token.value);storage.set('ut_email',email.value);storage.set('ut_login_method',loginMethod.value)
    guest.value='';storage.remove('ut_guest');loginBar.value=false
    await refreshBatches();tip(t('wechatBound'))
  }catch(e){tip(t('wechatLoginFailed'))}
  finally{loggingIn.value=false}
}

async function photo() {
  let filePath = ''
  try { filePath = await choosePhoto() } catch (e) { return }
  if (!filePath) return
  bubble('me',t('photoMessage',{filename:t('photo')}))
  tip(t('recognizing'))
  const r = await toolApi.uploadPhoto(filePath)
  bubble('bot', (r.data && (r.data.reply || r.data.detail)) || '…')
  if (!r.ok && !(r.data && r.data.reply)) tip(t('uploadError'))
  await refreshBatches()
}

async function openList() {
  drawer.value = true
  const r = await toolApi.notes()
  if (!r.ok) { tip((r.data && r.data.detail) || t('loadError')); notes.value = []; return }
  notes.value = (r.data && r.data.notes) || []
}

async function exportXlsx() {
  const r = await toolApi.exportXlsx()
  if (!r.ok) tip(r.error || t('exportError'))
}

function saveBases() {
  setBases({ tool: (cfgTool.value || '').trim(), cs: (cfgCs.value || '').trim() })
  bases.value = getBases()
  cfgOpen.value = false
  tip(t('serverSettingsSaved'))
}

onLoad(() => {
  attachViewport()
  guest.value = storage.get('ut_guest')
  token.value = storage.get('ut_token')
  email.value = storage.get('ut_email')
  loginMethod.value = storage.get('ut_login_method','email')
  bases.value = getBases()
  cfgTool.value = bases.value.tool
  cfgCs.value = bases.value.cs
  init()
})
onUnload(detachViewport)
</script>

<style scoped>
.page,.nt{text-align:start}.cell-input,.nf-text{unicode-bidi:plaintext}.msg{unicode-bidi:plaintext}

.session-controls { display:flex; flex-wrap:wrap; gap:6px; padding:8px 12px; background:#fff; max-height:180px; overflow:auto; flex-shrink:0; font-size:13px; }
.session-controls button { margin:0; font-size:13px; line-height:2; padding:0 10px; color:#245b9c; }
.session-controls label { width:100%; }
.page { display: flex; flex-direction: column; height: 100dvh; min-height:0; overflow:hidden; background: #f5f6f8; }
.compact-viewport .session-controls,.compact-viewport :deep(.language-picker){display:none}
.header { background: #fff; border-bottom: 1px solid #eee; padding: 8px 12px; display: flex; align-items: center; gap: 8px; }
.who { flex: 1; font-size: 13px; color: #666; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.hbtn { margin: 0; border: 0; border-radius: 6px; padding: 0 12px; font-size: 13px; line-height: 2; background: #1677ff; color: #fff; }
.hbtn::after { border: 0; }
.hbtn.ghost { background: #fff; color: #1677ff; border: 1px solid #1677ff; }
.loginbar { background: #fff; border-bottom: 1px solid #eee; padding: 10px 12px; display: flex; flex-direction: column; gap: 8px; }
.row { display: flex; gap: 8px; align-items: center; }
.inp { flex: 1; border: 1px solid #ddd; border-radius: 8px; padding: 8px 12px; font-size: 15px; background: #fff; }
.btn { margin: 0; border: 0; border-radius: 8px; padding: 0 14px; font-size: 14px; line-height: 2.3; background: #1677ff; color: #fff; }
.btn::after { border: 0; }
.btn[disabled] { background: #9ec4ff; color: #fff; }
.hint { font-size: 12px; color: #888; }
.cfg { font-size: 12px; color: #1677ff; padding: 2px 0; }
.log { flex: 1; min-height:0; overflow-y: auto; padding: 14px; box-sizing: border-box; }
.msg { max-width: 86%; padding: 9px 12px; border-radius: 12px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; margin-bottom: 10px; }
.bot { background: #fff; align-self: flex-start; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
.me { background: #1677ff; color: #fff; align-self: flex-end; }
.nav { display: flex; gap: 8px; padding: 10px; padding-bottom:calc(10px + env(safe-area-inset-bottom,0px)); background: #fff; border-top: 1px solid #eee; flex-shrink:0; }
.navbtn { margin: 0; flex: 1; border: 0; border-radius: 8px; font-size: 15px; line-height: 2.4; background: #1677ff; color: #fff; }
.navbtn::after { border: 0; }
.navbtn.ghost { background: #fff; color: #1677ff; border: 1px solid #1677ff; }
.drawer { position: fixed; left: 0; right: 0; top: 0; bottom: auto; height:100dvh; z-index: 20; }
.mask { position: absolute; left: 0; right: 0; top: 0; bottom: 0; background: rgba(0, 0, 0, 0.35); }
.aside { position: absolute; left: 0; right: 0; top: 8%; bottom: 0; background: #f5f6f8; border-radius: 12px 12px 0 0; display: flex; flex-direction: column; overflow: hidden; }
.dhead { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #fff; border-bottom: 1px solid #eee; }
.dtitle { flex: 1; font-size: 15px; font-weight: 600; }
.dempty { margin: 24px 16px; padding: 32px 16px; text-align: center; color: #999; background: #fff; border-radius: 8px; }
.dlist { flex: 1; padding: 10px; box-sizing: border-box; }
.note { background: #fff; border-radius: 8px; padding: 10px; display: flex; gap: 10px; font-size: 14px; line-height: 1.5; margin-bottom: 8px; }
.thumb { width: 64px; height: 64px; border-radius: 6px; flex-shrink: 0; background: #f0f0f0; }
.nf { flex: 1; overflow: hidden; }
.nf-text { white-space: pre-wrap; word-break: break-word; font-size: 14px; }
.nt-time { display: block; color: #999; font-size: 12px; margin-top: 4px; }
.cfgbox { position: absolute; left: 16px; right: 16px; top: 20%; background: #fff; border-radius: 12px; display: flex; flex-direction: column; overflow: hidden; }
.cfgbody { padding: 12px 16px 16px; display: flex; flex-direction: column; gap: 6px; }
.cfglabel { font-size: 12px; color: #888; }
</style>
