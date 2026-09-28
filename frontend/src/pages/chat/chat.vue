<template>
  <view class="page" :style="{ direction: dir }">
    <language-picker @change="pickLang" />
    <!-- 页内操作条（标题栏由 pages.json 提供；token 从链接带入） -->
    <view class="header">
      <button class="hbtn ghost" @click="openList">📋 {{ t('myShortList') }}</button>
      <button class="hbtn ghost" @click="photo">📷</button>
      <button class="hbtn boss" @click="sendAction('contact_owner')">{{ t('contactOwner') }}</button>
    </view>

    <!-- 首访语言选择 -->

    <!-- 消息区 -->
    <view class="session-controls">
      <text>{{ t('activeMode', { mode: photoMode === 'search' ? t('findProduct') : photoMode === 'notes' ? t('takeNotes') : t('choosePhotoIntentAgain') }) }}</text>
      <button @click="setMode('search')">{{ t('findProduct') }}</button><button @click="setMode('notes')">{{ t('takeNotes') }}</button>
      <button @click="newSession(true)">{{ t('endSession') }}</button><button @click="manualBatch">{{ t('manualSwitchShop') }}</button>
      <label v-for="n in unassigned" :key="n.id"><checkbox :checked="selected.includes(n.id)" @click="toggleNote(n.id)" />{{ t('attachNotes') }} {{ n.id }} {{ n.fields['型号或品名'] }}</label>
      <button v-for="b in batches.filter(x=>['pending','confirmed'].includes(x.state))" :key="b.id" @click="confirmBatch(b.id)">{{ b.active ? t('currentShop') : t('confirmSwitch') }}{{ b.fields['档口名称'] || t('shopPending') }}</button>
      <button v-for="b in batches.filter(x=>x.state==='pending')" :key="'decline'+b.id" @click="confirmBatch(b.id,null,'decline')">{{ t('declineCard') }}: {{ b.fields['档口名称'] }}</button>
      <view v-if="hasPending"><text>{{ t('pendingPhoto') }}</text><button @click="retryPhoto">{{ t('retryPhoto') }}</button><button @click="discardPhoto">{{ t('discardPhoto') }}</button></view>
    </view>
    <scroll-view class="log" scroll-y :scroll-top="tail" scroll-with-animation>
      <view v-for="(m, i) in messages" :key="i" class="msg" :class="m.role === 'me' ? 'me' : 'bot'">
        <text v-for="(seg, j) in segments(m.text)" :key="j"
              :class="{ link: seg.link }" @click="onSegment(seg)">{{ seg.v }}</text>
      </view>
    </scroll-view>

    <!-- 输入区 -->
    <view class="form">
      <input v-model="input" class="text" :placeholder="t('chatInputHint')" confirm-type="send" @confirm="send()" />
      <button class="send" @click="send()">➤</button>
    </view>

    <!-- 我的清单抽屉：内嵌 note-table 组件承接（小程序无 iframe） -->
    <view v-if="drawer" class="drawer">
      <view class="mask" @click="drawer = false" />
      <view class="aside">
        <view class="dhead">
          <text class="dtitle">📋 {{ t('myShortList') }}</text>
          <button class="hbtn" @click="drawer = false">✕ {{ t('close') }}</button>
        </view>
        <view v-if="listEmpty" class="dempty">{{ t('chatListEmpty') }}</view>
        <scroll-view v-else class="dbody" scroll-y>
          <note-table v-if="listK" :k="listK" />
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { useCustomerLanguage } from '../../use-language.js'
import LanguagePicker from '../../components/language-picker.vue'
const { locale, dir, t, label, display, changeLanguage } = useCustomerLanguage('chatTitle')

import { ref, nextTick } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { csApi, choosePhoto } from '../../api.js'
import { storage } from '../../storage.js'
import NoteTable from '../../components/note-table.vue'

const token = ref('')
const visitor = ref('')
let ready = Promise.resolve()
const hasPending=ref(false)
const photoMode=ref(''), batches=ref([]),unassigned=ref([]),selected=ref([])
const input = ref('')
const messages = ref([])
const tail = ref(0)
const langBar = ref(false)
const drawer = ref(false)
const listK = ref('')
const listEmpty = ref(false)

function bubble(role, text) {
  messages.value.push({ role, text })
  nextTick(() => { tail.value += 10000 })
}

function tip(t) { uni.showToast({ title: t, icon: 'none' }) }

// 回复里的清单链接可点：H5 直接打开；小程序无法外链，点击复制
function segments(text) {
  const out = []
  String(text || '').split(/(https?:\/\/[^\s）)]+)/).forEach((part) => {
    if (part) out.push({ v: part, link: /^https?:\/\//.test(part) })
  })
  return out
}

function onSegment(seg) {
  if (!seg.link) return
  // #ifdef H5
  window.open(seg.v, '_blank', 'noopener')
  // #endif
  // #ifdef MP-WEIXIN
  uni.setClipboardData({ data: seg.v, success: () => tip(t('linkCopied')) })
  // #endif
}

async function send(override) {
  await ready
  const text = override || (input.value || '').trim()
  if (!text) return
  if (!override) input.value = ''
  bubble('me', text)
  const r = await csApi.send(token.value, text, visitor.value)
  if (r.ok) {
    bubble('bot', (r.data && r.data.reply) || '…')
    await refreshSession()
  } else {
    tip((r.data && r.data.detail) || t('networkError'))
  }
}

async function photo() {
  await ready
  let filePath = ''
  try { filePath = await choosePhoto() } catch (e) { return }
  if (!filePath) return
  bubble('me',t('photoMessage',{filename:t('photo')}))
  tip(t('recognizing'))
  const r = await csApi.uploadPhoto(token.value, visitor.value, filePath)
  if (r.ok) {
    bubble('bot', (r.data && r.data.reply) || '…')
    await refreshSession()
  } else {
    tip((r.data && r.data.detail) || t('uploadError'))
  }
}

async function pickLang(lang) {
  changeLanguage(lang)
  await ready
  const r=await csApi.setLang(token.value,lang,visitor.value)
  if(!r.ok){tip(r.data?.detail||t('networkError'));return}
  storage.set('dk_lang_done','1')
  bubble('bot',t('chatWelcome'))
}
async function sendAction(action){await ready;const r=await csApi.send(token.value,'',visitor.value,action);bubble('bot',r.data?.reply||r.data?.detail||t('networkError'));await refreshSession()}

// 我的清单抽屉：按访客取最近 cs_link token，交给 note-table 渲染/编辑/导出
async function openList() {
  await ready
  drawer.value = true
  listEmpty.value = false
  listK.value = ''
  const r = await csApi.listToken(token.value, visitor.value)
  if (r.ok && r.data && r.data.token) {
    listK.value = r.data.token
  } else {
    listEmpty.value = true
  }
}

function toggleNote(id){selected.value=selected.value.includes(id)?selected.value.filter(x=>x!==id):[...selected.value,id]}
async function refreshSession(){const r=await csApi.session(token.value,visitor.value);if(!r.ok){tip(r.status===410?t('guestSessionExpired'):t('loadError'));return}photoMode.value=r.data.photo_mode;hasPending.value=!!r.data.intent_required;batches.value=r.data.batches||[];unassigned.value=(r.data.notes||[]).filter(n=>['unassigned','legacy_unassigned'].includes(n.batch_state))}
async function newSession(end=false){
  try{
    if(end&&visitor.value){
      const r=await csApi.endSession(token.value,visitor.value)
      if(!r.ok&&![401,410].includes(r.status)){tip(t('networkError'));return}
      visitor.value='';storage.remove('h5v:'+token.value)
    }
    const r=await csApi.newSession(token.value)
    if(!r.ok||!r.data?.visitor){tip(t('initError'));return}
    visitor.value=r.data.visitor;storage.set('h5v:'+token.value,visitor.value);messages.value=[];drawer.value=false;selected.value=[];await refreshSession()
  }catch(e){tip(t('networkError'))}
}
async function setMode(mode){
  await ready
  try{const r=await csApi.setMode(token.value,visitor.value,mode);if(r.data?.reply||r.data?.detail)bubble('bot',r.data.reply||r.data.detail);if(!r.ok)tip(t('pendingPhoto'))}
  catch(e){tip(t('networkError'))}
  finally{await refreshSession()}
}
async function retryPhoto(){if(!photoMode.value){tip(t('photoIntentPrompt'));return}await setMode(photoMode.value)}
async function discardPhoto(){
  await ready
  try{const r=await csApi.discardPhoto(token.value,visitor.value);if(!r.ok)tip(t('networkError'))}
  catch(e){tip(t('networkError'))}
  finally{await refreshSession()}
}
async function confirmBatch(id,fields=null,action='confirm'){const r=await csApi.confirmBatch(token.value,visitor.value,id,selected.value,fields,action);if(!r.ok){tip(r.data?.detail||t('networkError'));return}selected.value=[];await refreshSession()}
function manualBatch(){uni.showModal({title:t('newShopName'),editable:true,success:r=>{if(r.confirm&&r.content)confirmBatch(null,{'档口名称':r.content})}})}

onLoad((options) => {
  options = options || {}
  token.value = options.token || ''
  // #ifdef H5
  if (!token.value) {
    // 兼容旧链接 /cs/chat/{token}（H5 直接落在该路径时从 pathname 取末段）
    const m = window.location.pathname.match(/\/cs\/chat\/([A-Za-z0-9_-]+)/)
    if (m) token.value = m[1]
  }
  // #endif
  if (!token.value) token.value = 'invalid-token'
  visitor.value = storage.get('h5v:'+token.value)
  ready = visitor.value ? refreshSession() : newSession()
  // 首访先选语言；老访客直接欢迎（旧页用 sessionStorage，小程序无此能力，改持久标记）
  if (!storage.get('dk_lang_done')) {
    langBar.value = true
  } else {
    bubble('bot', t('chatReturnWelcome'))
  }
})
</script>

<style scoped>
.page,.nt{text-align:start}.cell-input,.nf-text{unicode-bidi:plaintext}.msg{unicode-bidi:plaintext}

.session-controls { display:flex; flex-wrap:wrap; gap:6px; padding:8px 12px; background:#fff; max-height:180px; overflow:auto; flex-shrink:0; font-size:13px; }
.session-controls button { margin:0; font-size:13px; line-height:2; padding:0 10px; color:#245b9c; }
.session-controls label { width:100%; }
.page { display: flex; flex-direction: column; height: 100vh; background: #f5f6f8; }
.header { background: #fff; border-bottom: 1px solid #eee; padding: 8px 12px; display: flex; gap: 8px; align-items: center; }
.hbtn { margin: 0; border: 0; border-radius: 6px; padding: 0 12px; font-size: 13px; line-height: 2; background: #1677ff; color: #fff; }
.hbtn::after { border: 0; }
.hbtn.ghost { background: #fff; color: #1677ff; border: 1px solid #1677ff; }
.hbtn.boss { background: #fff; color: #1677ff; border: 1px solid #1677ff; border-radius: 20px; padding: 0 14px; }
.langbar { display: flex; gap: 8px; padding: 8px 12px; background: #fff; border-bottom: 1px solid #eee; }
.langtip { align-self: center; color: #888; font-size: 13px; margin-right: 4px; }
.langbtn { margin: 0; border: 1px solid #ddd; background: #fff; border-radius: 14px; font-size: 13px; line-height: 1.8; padding: 0 14px; }
.langbtn::after { border: 0; }
.log { flex: 1; overflow-y: auto; padding: 14px; box-sizing: border-box; }
.msg { max-width: 82%; padding: 9px 12px; border-radius: 12px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; margin-bottom: 10px; }
.bot { background: #fff; align-self: flex-start; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
.me { background: #1677ff; color: #fff; align-self: flex-end; }
.link { color: #1677ff; text-decoration: underline; }
.form { display: flex; gap: 8px; padding: 10px; background: #fff; border-top: 1px solid #eee; align-items: center; }
.text { flex: 1; border: 1px solid #ddd; border-radius: 20px; padding: 8px 14px; font-size: 15px; background: #fff; }
.send { margin: 0; border: 0; border-radius: 50%; width: 40px; height: 40px; font-size: 18px; line-height: 40px; padding: 0; background: #1677ff; color: #fff; }
.send::after { border: 0; }
.drawer { position: fixed; left: 0; right: 0; top: 0; bottom: 0; z-index: 20; }
.mask { position: absolute; left: 0; right: 0; top: 0; bottom: 0; background: rgba(0, 0, 0, 0.35); }
.aside { position: absolute; left: 0; right: 0; top: 10%; bottom: 0; background: #f5f6f8; border-radius: 12px 12px 0 0; display: flex; flex-direction: column; overflow: hidden; }
.dhead { display: flex; align-items: center; gap: 8px; padding: 10px 12px; background: #fff; border-bottom: 1px solid #eee; }
.dtitle { flex: 1; font-size: 15px; font-weight: 600; }
.dempty { margin: 24px 16px; padding: 32px 16px; text-align: center; color: #999; background: #fff; border-radius: 8px; }
.dbody { flex: 1; }
</style>
