<template>
  <view class="page">
    <view class="title">📦 我的采购清单</view>
    <note-table v-if="k" :k="k" />
    <view v-else class="empty">链接无效或已过期，请找商家重新生成</view>
  </view>
</template>

<script setup>
import { ref } from 'vue'
import { onLoad } from '@dcloudio/uni-app'
import NoteTable from '../../components/note-table.vue'

const k = ref('')

onLoad((options) => {
  options = options || {}
  k.value = options.k || ''
  // #ifdef H5
  if (!k.value) {
    // 兼容旧链接 /cs/list.html?k=xxx
    const m = window.location.search.match(/[?&]k=([^&]+)/)
    if (m) k.value = decodeURIComponent(m[1])
  }
  // #endif
})
</script>

<style scoped>
.page { min-height: 100vh; background: #f5f6f8; padding-bottom: 20px; }
.title { font-size: 18px; font-weight: 600; padding: 16px 16px 0; }
.empty { margin: 16px; padding: 40px 16px; text-align: center; color: #999; background: #fff; border-radius: 8px; font-size: 14px; }
</style>
