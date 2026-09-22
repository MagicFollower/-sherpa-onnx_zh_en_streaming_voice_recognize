<script setup lang="ts">
import { ref } from 'vue'
import StudioIcon from './StudioIcon.vue'
defineProps<{ pending: boolean; retrySeconds: number }>()
const emit = defineEmits<{ pair: [code: string] }>()
const code = ref('')
function submit() {
  const value = code.value
  code.value = '' // 发送后立即清除表单秘密，不进URL、存储、日志或错误提示。
  emit('pair', value)
}
</script>
<template>
  <section class="pair-card" aria-labelledby="pair-title">
    <div class="pair-symbol"><StudioIcon name="shield" /></div>
    <div class="pair-content">
      <p class="eyebrow">一次配对 · 本地连接</p>
      <h2 id="pair-title">让这台设备，连接你的声音。</h2>
      <p>输入电脑端服务终端显示的一次性口令。配对成功后，才会请求麦克风。</p>
      <form @submit.prevent="submit">
        <label for="pair-code">配对口令</label>
        <div class="pair-fields">
          <input id="pair-code" v-model="code" name="pairing-code" type="password" autocomplete="off" spellcheck="false" autocapitalize="characters" placeholder="26 位一次性口令" required maxlength="64" :disabled="pending || retrySeconds > 0" />
          <button class="primary-small" type="submit" :disabled="pending || retrySeconds > 0 || !code.trim()">{{ pending ? '正在配对' : retrySeconds ? `等待 ${retrySeconds} 秒` : '连接设备' }}<StudioIcon name="arrow" /></button>
        </div>
      </form>
      <small>口令 5 分钟内有效，成功使用后即失效；不会保存到当前页面。</small>
    </div>
  </section>
</template>
