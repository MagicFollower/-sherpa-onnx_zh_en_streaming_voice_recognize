<script setup lang="ts">
import { nextTick, onMounted, onUnmounted, ref, watch } from 'vue'
import type { Card, Phase } from '../state/rounds'
import StudioIcon from './StudioIcon.vue'
const props = defineProps<{ cards: Card[]; activeId: string }>()
defineEmits<{ copy: [card: Card] }>()
const end = ref<HTMLElement>()
const following = ref(true)
const labels: Record<Phase, string> = { preparing: '准备中', recording: '正在聆听', draining: '提交尾音', finalizing: '整理中', cancelling: '正在取消', completed: '已完成', cancelled: '已取消', failed: '本轮中断' }
const time = (value: number) => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(value)
function measure() { following.value = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 240 }
function follow() { end.value?.scrollIntoView({ block: 'nearest', behavior: 'auto' }); following.value = true }
// 只在用户原本靠近底部时跟随；选中文字和浏览历史绝不因partial抢回滚动。
watch(() => props.cards.map(card => `${card.id}:${card.text}:${card.phase}`).join('|'), async () => {
  if (!following.value || window.getSelection()?.toString()) return
  await nextTick(); follow()
})
onMounted(() => window.addEventListener('scroll', measure, { passive: true }))
onUnmounted(() => window.removeEventListener('scroll', measure))
</script>
<template>
  <section class="transcripts" aria-label="语音转写记录" aria-live="off">
    <div v-if="!cards.length" class="empty-state">
      <div class="empty-mark"><StudioIcon name="mic" /></div>
      <p class="eyebrow">给想法一个出口</p>
      <h2>按住，说出这一刻。</h2>
      <p>一句想法，一段灵感，或一场中英混合的表达。<br />松手后，文字会留在这里。</p>
      <div class="empty-steps"><span><b>01</b> 按住讲话</span><span><b>02</b> 松手结束</span><span><b>03</b> 复制终稿</span></div>
    </div>
    <article v-for="(card, index) in cards" :key="card.id" class="transcript-card" :class="[card.phase, { current: card.id === activeId }]" :data-phase="card.phase">
      <header class="card-meta"><span class="card-number">{{ String(index + 1).padStart(2, '0') }}</span><time :datetime="new Date(card.created).toISOString()">{{ time(card.created) }}</time><span class="card-phase">{{ labels[card.phase] }}</span><span class="duration">{{ (card.samples / 16000).toFixed(1) }} 秒</span></header>
      <p v-if="card.text" class="transcript-text" :class="{ draft: card.phase !== 'completed' }">{{ card.text }}</p>
      <p v-else class="transcript-placeholder">{{ card.phase === 'recording' ? '声音正在成为文字…' : card.phase === 'preparing' ? '麦克风准备好后，即可开始讲话。' : card.phase === 'completed' ? '这一轮，没有检测到语音。' : '这一轮未保留文字。' }}</p>
      <footer class="card-footer"><span>{{ card.note }}<template v-if="card.text && card.phase === 'failed'"> · 未完成草稿</template></span><button v-if="card.phase === 'completed' && card.text && !card.discarded" class="text-button" @click="$emit('copy', card)"><StudioIcon name="copy" />复制终稿</button></footer>
    </article>
    <button v-if="!following && activeId" class="return-current" @click="follow">回到当前轮 ↓</button>
    <div ref="end" class="scroll-anchor" />
  </section>
</template>
