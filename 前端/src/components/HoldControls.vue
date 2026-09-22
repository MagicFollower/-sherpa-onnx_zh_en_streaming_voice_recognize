<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { HoldInput, type InputActions } from '../state/input'
import type { Phase } from '../state/rounds'
import StudioIcon from './StudioIcon.vue'
const props = defineProps<{ canStart: boolean; active: boolean; phase?: Phase; canCopy: boolean; canClear: boolean; actions: InputActions }>()
defineEmits<{ copy: []; clear: []; cancel: [] }>()
const button = ref<HTMLButtonElement>()
let input: HoldInput | undefined
const label = computed(() => ({ preparing: '保持按住 · 准备中', recording: '正在聆听 · 松手结束', draining: '正在提交尾音', finalizing: '正在整理文字', cancelling: '正在释放资源' }[props.phase ?? ''] ?? '按住讲话'))
onMounted(() => { input = new HoldInput(button.value!, props.actions); input.attach() })
onUnmounted(() => input?.dispose())
</script>
<template>
  <footer class="control-dock" aria-label="录音操作">
    <div class="dock-inner">
      <div class="dock-tools"><button class="text-button" :disabled="!active || phase === 'cancelling'" @click="$emit('cancel')"><StudioIcon name="close" />取消本轮</button><span class="shortcut">Esc</span></div>
      <div class="hold-center">
        <!-- 活动时不能原生disabled，否则浏览器可能吞掉拥有者pointerup；aria-disabled只禁止新意图。 -->
        <button ref="button" class="hold-button" :class="{ listening: phase === 'recording', unavailable: !canStart && !active }" :aria-disabled="!canStart && !active" aria-label="按住讲话" aria-describedby="hold-help"><StudioIcon name="mic" /><span>{{ label }}</span><span class="hold-dot" /></button>
        <p id="hold-help">鼠标左键 / 触摸长按 <span>或按住 <kbd>空格</kbd></span> · 最长 60 秒</p>
      </div>
      <div class="dock-tools dock-right"><button class="text-button" :disabled="!canCopy" @click="$emit('copy')">复制最近终稿</button><button class="text-button muted" :disabled="!canClear" @click="$emit('clear')">清空记录</button></div>
    </div>
    <p class="dock-privacy">音频仅在本地服务处理，不主动保存录音。页面记录仅存内存，刷新即清除。</p>
  </footer>
</template>
