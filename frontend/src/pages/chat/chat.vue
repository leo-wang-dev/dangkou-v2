<template>
  <view class="page">
    <!-- 页内操作条（标题栏由 pages.json 提供；token 从链接带入） -->
    <view class="header">
      <button class="hbtn ghost" @click="openList">📋 我的清单</button>
      <button class="hbtn ghost" @click="photo">📷</button>
      <button class="hbtn boss" @click="send('找老板')">找老板</button>
    </view>

    <!-- 首访语言选择 -->
    <view v-if="langBar" class="langbar">
      <text class="langtip">语言 / Language:</text>
      <button class="langbtn" @click="pickLang('中文')">中文</button>
      <button class="langbtn" @click="pickLang('English')">English</button>
    </view>

    <!-- 消息区 -->
    <scroll-view class="log" scroll-y :scroll-top="tail" scroll-with-animation>
      <view v-for="(m, i) in messages" :key="i" class="msg" :class="m.role === 'me' ? 'me' : 'bot'">
        <text v-for="(seg, j) in segments(m.text)" :key="j"
              :class="{ link: seg.link }" @click="onSegment(seg)">{{ seg.v }}</text>
      </view>
    </scroll-view>

    <!-- 输入区 -->
    <view class="form">
      <input v-model="input" class="text" placeholder="发消息，或点 📷 拍照整理采购清单" confirm-type="send" @confirm="send()" />
      <button class="send" @click="send()">➤</button>
    </view>

    <!-- 我的清单抽屉：内嵌 note-table 组件承接（小程序无 iframe） -->
    <view v-if="drawer" class="drawer">
      <view class="mask" @click="drawer = false" />
      <view class="aside">
        <view class="dhead">
          <text class="dtitle">📋 我的清单</text>
          <button class="hbtn" @click="drawer = false">✕ 关闭</button>
        </view>
        <view v-if="listEmpty" class="dempty">还没有清单——在对话里发照片整理条目，然后说「出表」生成清单。</view>
        <scroll-view v-else class="dbody" scroll-y>
          <note-table v-if="listK" :k="listK" />
        </scroll-view>
      </view>
    </view>
  </view>
</template>

<script setup>
import { ref, nextTick } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import { csApi, choosePhoto } from '../../api.js'
import { storage } from '../../storage.js'
import NoteTable from '../../components/note-table.vue'

const token = ref('')
const visitor = ref('')
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
  uni.setClipboardData({ data: seg.v, success: () => tip('链接已复制，可在浏览器打开') })
  // #endif
}

async function send(override) {
  const text = override || (input.value || '').trim()
  if (!text) return
  if (!override) input.value = ''
  bubble('me', text)
  const r = await csApi.send(token.value, text, visitor.value)
  if (r.ok) {
    bubble('bot', (r.data && r.data.reply) || '…')
  } else {
    tip((r.data && r.data.detail) || '网络异常，请重试')
  }
}

async function photo() {
  let filePath = ''
  try { filePath = await choosePhoto() } catch (e) { return }
  if (!filePath) return
  bubble('me', '📷 照片')
  tip('识别中…')
  const r = await csApi.uploadPhoto(token.value, visitor.value, filePath)
  if (r.ok) {
    bubble('bot', (r.data && r.data.reply) || '…')
  } else {
    tip((r.data && r.data.detail) || '上传失败，请重试')
  }
}

async function pickLang(lang) {
  await csApi.setLang(token.value, lang, visitor.value)
  langBar.value = false
  storage.set('dk_lang_done', '1')
  bubble('bot', lang === '中文'
    ? '您好！可以查询商品、发照片整理采购清单，需要联系商家可点右上角「找老板」。'
    : 'Hello! You can search products or send photos to build a purchase list. Tap "找老板" to reach the owner.')
}

// 我的清单抽屉：按访客取最近 cs_link token，交给 note-table 渲染/编辑/导出
async function openList() {
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
  visitor.value = storage.get('h5v')
  if (!visitor.value) {
    visitor.value = 'h5-' + Math.random().toString(36).slice(2, 12)
    storage.set('h5v', visitor.value)
  }
  // 首访先选语言；老访客直接欢迎（旧页用 sessionStorage，小程序无此能力，改持久标记）
  if (!storage.get('dk_lang_done')) {
    langBar.value = true
  } else {
    bubble('bot', '您好，有什么可以帮您？')
  }
})
</script>

<style scoped>
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
