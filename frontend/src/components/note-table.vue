<template>
  <view class="nt">
    <view class="bar">
      <button class="primary" :disabled="!notes.length || exporting" @click="exportXlsx">⬇️ {{ t('exportExcel') }}</button>
      <button class="ghost" @click="load">↻ {{ t('refresh') }}</button>
    </view>

    <view v-if="error" class="empty">{{ error }}</view>
    <view v-else-if="loading" class="empty">{{ t('loading') }}</view>
    <view v-else-if="!notes.length" class="empty">{{ t('listEmpty') }}</view>

    <scroll-view v-else scroll-x class="table-scroll">
      <view class="table" :style="{ width: tableWidth + 'px' }">
        <view class="tr th">
          <view class="td idx">#</view>
          <view v-for="key in keys" :key="key" class="td field">{{ label(key) }}</view>
          <view class="td status">{{ t('statusHeader') }}</view>
          <view class="td photo-col">{{ t('photoHeader') }}</view>
        </view>
        <view v-for="(n, i) in notes" :key="n.id" class="tr">
          <view class="td idx">{{ i + 1 }}</view>
          <view v-for="key in keys" :key="key" class="td field">
            <!-- 档口归属依据只读（同旧 list.html）；照片字段展示图片；其余单元格可编辑，失焦即存 -->
            <image
              v-if="isPhotoKey(key) && n.photo && !photoFailed[n.id]"
              class="photo"
              :src="photoUrl(n)"
              mode="aspectFill"
              @error="photoFailed[n.id] = true" />
            <text v-else-if="isPhotoKey(key) && !n.photo"></text>
            <text v-else-if="key === '档口归属依据'" class="cell">{{ text(n, key) }}</text>
            <input
              v-else
              class="cell-input"
              :value="text(n, key)"
              @blur="onEdit(n, key, $event)"
              @confirm="onEdit(n, key, $event)" />
          </view>
          <view class="td status">{{ n.status === 'draft' ? t('statusDraft') : t('statusConfirmed') }}</view>
          <view class="td photo-col">
            <image
              v-if="n.photo && !photoFailed['row-' + n.id]"
              class="photo"
              :src="photoUrl(n)"
              mode="aspectFill"
              @error="photoFailed['row-' + n.id] = true" />
          </view>
        </view>
      </view>
    </scroll-view>

    <view class="saved">{{ t('listEditingHelp') }}</view>
  </view>
</template>

<script setup>
import { useCustomerLanguage } from '../use-language.js'
import LanguagePicker from '../components/language-picker.vue'
const { locale, dir, t, label, display, changeLanguage } = useCustomerLanguage()

import { ref, reactive, computed, watch, onMounted } from 'vue'
import { csApi } from '../api.js'

const props = defineProps({
  k: { type: String, required: true }  // cs_link 令牌（旧页 ?k=）
})

const notes = ref([])
const keys = ref([])
const loading = ref(false)
const error = ref('')
const exporting = ref(false)
const photoFailed = reactive({})   // 图片加载失败标记（对应旧页 onerror remove）

const tableWidth = computed(() => 40 + keys.value.length * 170 + 80 + 110)

watch(() => props.k, () => { load() })
onMounted(() => { load() })

function photoUrl(n) {
  return csApi.photoUrl(n.photo || '')
}

function text(n, key) {
  const v = n.fields ? n.fields[key] : ''
  return v === null || v === undefined ? '' : String(display(v))
}

function isPhotoKey(key) {
  return key.includes('图') || /^photo$/i.test(key)
}

async function load() {
  if (!props.k) {
    error.value = t('linkExpiredContact')
    notes.value = []
    return
  }
  loading.value = true
  error.value = ''
  const r = await csApi.linkNotes(props.k)
  loading.value = false
  if (!r.ok) {
    notes.value = []
    error.value = (r.data && r.data.detail) || t('linkExpiredContact')
    return
  }
  const list = (r.data && r.data.notes) || []
  notes.value = list
  const cols = []
  list.forEach((n) => {
    Object.keys(n.fields || {}).forEach((key) => {
      if (!cols.includes(key)) cols.push(key)
    })
  })
  keys.value = cols
}

async function onEdit(note, key, ev) {
  const value = ((ev && ev.detail && ev.detail.value) || '').trim()
  if (value === text(note, key)) return  // 未变化不发请求
  const r = await csApi.editNote(props.k, note.id, key, value)
  if (r.ok) {
    const fields = (r.data && r.data.fields) || null
    if (fields) {
      const target = notes.value.find((n) => n.id === note.id)
      if (target) target.fields = fields
    }
    uni.showToast({ title: t('saved'), icon: 'none' })
  } else {
    uni.showToast({ title: (r.data && r.data.detail) || t('saveError'), icon: 'none' })
  }
}

async function exportXlsx() {
  exporting.value = true
  const r = await csApi.exportXlsx(props.k)
  exporting.value = false
  if (!r.ok) uni.showToast({ title: r.error || t('exportError'), icon: 'none' })
}

defineExpose({ load })
</script>

<style scoped>
.page,.nt{text-align:start}.cell-input,.nf-text{unicode-bidi:plaintext}.msg{unicode-bidi:plaintext}

.nt { padding: 0 12px; }
.bar { display: flex; gap: 10px; margin: 12px 0; }
.bar button { margin: 0; flex: 1; border-radius: 8px; font-size: 15px; line-height: 2.2; }
.bar button::after { border: 0; }
button.primary { background: #1677ff; color: #fff; }
button.primary[disabled] { background: #9ec4ff; color: #fff; }
button.ghost { background: #fff; color: #1677ff; border: 1px solid #1677ff; }
.table-scroll { width: 100%; }
.table { background: #fff; border-radius: 8px; overflow: hidden; }
.tr { display: flex; border-bottom: 1px solid #eee; }
.tr.th .td { background: #fafafa; color: #666; font-weight: 600; }
.td { padding: 8px 10px; font-size: 14px; word-break: break-all; }
.idx { width: 40px; flex-shrink: 0; }
.field { width: 170px; flex-shrink: 0; }
.status { width: 80px; flex-shrink: 0; }
.photo-col { width: 110px; flex-shrink: 0; }
.cell { white-space: pre-wrap; word-break: break-all; }
.cell-input { width: 100%; font-size: 14px; min-height: 22px; padding: 2px 4px; margin: -2px -4px; }
.photo { width: 90px; height: 90px; border-radius: 4px; background: #f5f6f8; }
.empty { padding: 40px; text-align: center; color: #999; background: #fff; border-radius: 8px; margin: 12px 0; font-size: 14px; }
.saved { color: #2da44e; font-size: 12px; line-height: 1.6; margin: 14px 0; }
</style>
