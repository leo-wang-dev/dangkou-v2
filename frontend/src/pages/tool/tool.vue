<template>
  <view class="page">
    <!-- 页头：身份 + 登录/退出（标题栏由 pages.json 提供） -->
    <view class="header">
      <text class="who">{{ token ? email : '游客模式' }}</text>
      <button class="hbtn" @click="toggleLogin">{{ token ? '退出' : '登录' }}</button>
    </view>

    <!-- 登录条（邮箱验证码） -->
    <view v-if="loginBar" class="loginbar">
      <view class="row">
        <input v-model="email" class="inp" type="text" placeholder="邮箱" />
        <button class="btn" :disabled="sending" @click="sendCode">发验证码</button>
      </view>
      <view class="row">
        <input v-model="code" class="inp" type="number" placeholder="6位验证码" />
        <button class="btn" :disabled="!codeSent || loggingIn" @click="doLogin">登录</button>
      </view>
      <view class="hint">登录后清单会挂到您的账号；未登录也可直接用（游客模式）。</view>
      <view class="cfg" @click="cfgOpen = true">服务器：{{ bases.tool || '(同源)' }} / {{ bases.cs || '(同源)' }} · 点击修改</view>
    </view>

    <!-- 消息区 -->
    <view class="session-controls">
      <button @click="endSession">结束当前会话</button><button @click="manualBatch">手动切换档口</button>
      <label v-for="n in unassigned" :key="n.id"><checkbox :checked="selected.includes(n.id)" @click="toggleNote(n.id)" />关联待归属条目 {{ n.id }} {{ n.fields['型号或品名'] }}</label>
      <button v-for="b in batches.filter(x=>x.state!=='unassigned')" :key="b.id" @click="confirmBatch(b.id)">{{ b.active ? '当前档口：' : '确认切换：' }}{{ b.fields['档口名称'] || '未命名档口' }}</button>
    </view>
    <scroll-view class="log" scroll-y :scroll-top="tail" scroll-with-animation>
      <view v-for="(m, i) in messages" :key="i" class="msg" :class="m.role === 'me' ? 'me' : 'bot'">{{ m.text }}</view>
    </scroll-view>

    <!-- 底部操作 -->
    <view class="nav">
      <button class="navbtn" @click="photo">📷 拍照</button>
      <button class="navbtn ghost" @click="openList">📋 清单</button>
      <button class="navbtn ghost" @click="exportXlsx">⬇ Excel</button>
    </view>

    <!-- 清单抽屉（H5 与小程序共用组件实现；小程序无 iframe，直接内嵌表格/列表） -->
    <view v-if="drawer" class="drawer">
      <view class="mask" @click="drawer = false" />
      <view class="aside">
        <view class="dhead">
          <text class="dtitle">📋 我的清单</text>
          <button class="hbtn ghost" @click="openList">↻ 刷新</button>
          <button class="hbtn" @click="drawer = false">✕ 关闭</button>
        </view>
        <view v-if="!notes.length" class="dempty">还没有条目——拍几张照片，识别后会自动进清单。</view>
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
        <view class="dhead"><text class="dtitle">服务器地址</text><button class="hbtn" @click="cfgOpen = false">✕ 关闭</button></view>
        <view class="cfgbody">
          <text class="cfglabel">工具端 base（H5 留空=同源）</text>
          <input v-model="cfgTool" class="inp" placeholder="https://host/tool" />
          <text class="cfglabel">客服/清单 base（H5 留空=同源）</text>
          <input v-model="cfgCs" class="inp" placeholder="https://host" />
          <button class="btn" style="margin-top: 10px;" @click="saveBases">保存（重启页面生效一部分，无需重编译）</button>
        </view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { toolApi, getBases, setBases, choosePhoto } from '../../api.js'
import { storage } from '../../storage.js'

const guest = ref('')
const token = ref('')
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
    .map(([k, v]) => k + '=' + v)
    .join('\n')
}

const batches = ref([]), unassigned = ref([]), selected = ref([])
function toggleNote(id) { selected.value = selected.value.includes(id) ? selected.value.filter(x=>x!==id) : [...selected.value,id] }
async function refreshBatches() {
  const r=await toolApi.notes(); if(!r.ok)return
  batches.value=r.data.batches||[]; unassigned.value=(r.data.notes||[]).filter(n=>['unassigned','legacy_unassigned'].includes(n.batch_state))
}
async function confirmBatch(id,fields=null) { const r=await toolApi.confirmBatch(id,selected.value,fields); if(!r.ok){tip(r.data?.detail||'切换失败');return} selected.value=[];await refreshBatches() }
function manualBatch() { uni.showModal({title:'新档口名称',editable:true,success:r=>{if(r.confirm&&r.content)confirmBatch(null,{'档口名称':r.content})}}) }
async function endSession() {
  if(token.value){tip('请先退出登录');return}
  const r=await toolApi.endSession();if(!r.ok&&r.status!==410){tip('结束会话失败');return}
  guest.value='';storage.remove('ut_guest');messages.value=[];notes.value=[];batches.value=[];unassigned.value=[];drawer.value=false;await init()
}
async function init() {
  if (!guest.value) {
    const r = await toolApi.newGuest()
    if (r.ok && r.data && r.data.guest) {
      guest.value = r.data.guest
      storage.set('ut_guest', guest.value)
    } else {
      tip('初始化失败，请刷新重试')
    }
  }
  await refreshBatches()
  bubble('bot', '您好！拍商品/名片照片，我帮您整理成清单，随时可导出 Excel。登录邮箱后清单挂账号，不登录也能用。')
}

async function toggleLogin() {
  if (token.value) {
    const ended = await toolApi.endSession()
    if (!ended.ok) { tip('退出失败，请重试'); return }
    token.value = ''
    email.value = ''
    storage.remove('ut_token')
    storage.remove('ut_email')
    guest.value = ''; storage.remove('ut_guest'); await init()
    loginBar.value = false
    bubble('bot', '已退出登录，回到游客模式。')
    return
  }
  loginBar.value = !loginBar.value
}

async function sendCode() {
  const addr = (email.value || '').trim()
  if (!addr) { tip('请输入邮箱'); return }
  sending.value = true
  const r = await toolApi.sendCode(addr)
  sending.value = false
  if (!r.ok) { tip((r.data && r.data.detail) || '发送失败'); return }
  codeSent.value = true
  tip('验证码已发送，请查收邮箱（10分钟内有效）')
}

async function doLogin() {
  const addr = (email.value || '').trim()
  const c = (code.value || '').trim()
  if (!addr || !c) { tip('请输入邮箱和验证码'); return }
  loggingIn.value = true
  const r = await toolApi.verify(addr, c, token.value ? '' : guest.value)
  loggingIn.value = false
  if (!r.ok) { tip((r.data && r.data.detail) || '登录失败'); return }
  guest.value = ''; storage.remove('ut_guest')
  token.value = r.data.token
  email.value = r.data.email
  storage.set('ut_token', token.value)
  storage.set('ut_email', email.value)
  loginBar.value = false
  bubble('bot', '登录成功，邮箱 ' + email.value + '；刚才游客模式下的记录已合并到您的账号。')
}

async function photo() {
  let filePath = ''
  try { filePath = await choosePhoto() } catch (e) { return }
  if (!filePath) return
  bubble('me', '📷 照片')
  tip('识别中…')
  const r = await toolApi.uploadPhoto(filePath)
  bubble('bot', (r.data && (r.data.reply || r.data.detail)) || '…')
  if (!r.ok && !(r.data && r.data.reply)) tip('上传失败，请重试')
  await refreshBatches()
}

async function openList() {
  drawer.value = true
  const r = await toolApi.notes()
  if (!r.ok) { tip((r.data && r.data.detail) || '加载失败'); notes.value = []; return }
  notes.value = (r.data && r.data.notes) || []
}

async function exportXlsx() {
  const r = await toolApi.exportXlsx()
  if (!r.ok) tip(r.error || '导出失败')
}

function saveBases() {
  setBases({ tool: (cfgTool.value || '').trim(), cs: (cfgCs.value || '').trim() })
  bases.value = getBases()
  cfgOpen.value = false
  tip('已保存服务器地址')
}

onLoad(() => {
  guest.value = storage.get('ut_guest')
  token.value = storage.get('ut_token')
  email.value = storage.get('ut_email')
  bases.value = getBases()
  cfgTool.value = bases.value.tool
  cfgCs.value = bases.value.cs
  init()
})
</script>

<style scoped>
.session-controls { display:flex; flex-wrap:wrap; gap:6px; padding:8px 12px; background:#fff; max-height:180px; overflow:auto; flex-shrink:0; font-size:13px; }
.session-controls button { margin:0; font-size:13px; line-height:2; padding:0 10px; color:#245b9c; }
.session-controls label { width:100%; }
.page { display: flex; flex-direction: column; height: 100vh; background: #f5f6f8; }
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
.log { flex: 1; overflow-y: auto; padding: 14px; box-sizing: border-box; }
.msg { max-width: 86%; padding: 9px 12px; border-radius: 12px; font-size: 15px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; margin-bottom: 10px; }
.bot { background: #fff; align-self: flex-start; box-shadow: 0 1px 3px rgba(0, 0, 0, 0.08); }
.me { background: #1677ff; color: #fff; align-self: flex-end; }
.nav { display: flex; gap: 8px; padding: 10px; background: #fff; border-top: 1px solid #eee; }
.navbtn { margin: 0; flex: 1; border: 0; border-radius: 8px; font-size: 15px; line-height: 2.4; background: #1677ff; color: #fff; }
.navbtn::after { border: 0; }
.navbtn.ghost { background: #fff; color: #1677ff; border: 1px solid #1677ff; }
.drawer { position: fixed; left: 0; right: 0; top: 0; bottom: 0; z-index: 20; }
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
