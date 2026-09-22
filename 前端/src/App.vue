<script setup lang="ts">
import { computed } from 'vue'
import { useStudio } from './composables/useStudio'
import PairCard from './components/PairCard.vue'
import TranscriptList from './components/TranscriptList.vue'
import HoldControls from './components/HoldControls.vue'
import StudioIcon from './components/StudioIcon.vue'
const { view, connection, notice, capability, canStart, active, latest, retrySeconds, rounds, refresh, pair, logout, copy } = useStudio()
const connectionLabel = computed(() => !connection.paired ? '等待配对' : connection.connecting ? '连接中' : connection.connected ? '本地已连接' : '连接已断开')
const actions = { begin: rounds.begin.bind(rounds), release: (time: number) => { void rounds.release(time) }, cancel: rounds.cancel.bind(rounds) }
</script>
<template>
  <a class="skip-link" href="#workspace">跳到转写记录</a>
  <div class="app-shell">
    <header class="site-header">
      <a class="brand" href="#" aria-label="听间，本地语音工作台"><span class="brand-icon"><StudioIcon name="mic" /></span><span class="brand-name">听间<span>LOCAL VOICE STUDIO</span></span></a>
      <div class="header-actions"><span class="connection-pill" :class="{ online: connection.connected }"><i />{{ connectionLabel }}</span><button v-if="connection.paired" class="text-button" @click="logout">注销</button><a class="help-link" href="#help">使用帮助 ↗</a></div>
    </header>
    <main id="workspace" tabindex="-1">
      <section class="intro"><div><p class="eyebrow">本地离线 · 中英双语</p><h1>让表达，自然流动。</h1><p class="intro-description">按住说话，松手成文。你的声音，不必经过云端。</p></div><div class="edition"><span>01 /</span><p>即时转写<br />专注此刻</p></div></section>
      <div class="workspace-grid">
        <aside class="workspace-aside">
          <div class="aside-label"><span class="tiny-line" />转写工作台</div>
          <p class="record-count">{{ String(view.cards.length).padStart(2, '0') }}<span> / 50</span></p><p class="aside-note">此页记录<br />不跨设备同步</p>
          <div class="model-status"><i :class="{ ready: connection.modelReady }" /><span>{{ !connection.paired ? '配对后查看模型' : connection.modelReady ? connection.busy ? '模型正在忙碌' : '双语模型已就绪' : '模型准备中' }}</span></div>
          <p class="aside-note">流式识别<br />标点与精修已关闭</p>
        </aside>
        <div class="workspace-content">
          <div v-if="connection.error" class="alert" role="alert"><span>{{ connection.error }}</span><button v-if="connection.paired" class="text-button" :disabled="!!view.activeId || connection.connecting" @click="refresh(true)">重新连接</button></div>
          <div v-if="capability" class="alert">{{ capability }} <a href="#help">查看解决方式</a></div>
          <div v-if="view.warning" class="load-warning" role="status">本地处理负载较高，若积压达到上限将安全中断本轮。</div>
          <PairCard v-if="!connection.paired" :pending="connection.pairing" :retry-seconds="retrySeconds" @pair="pair" />
          <TranscriptList :cards="view.cards" :active-id="view.activeId" @copy="copy" />
        </div>
      </div>
      <details id="help" class="help-panel"><summary>使用前，了解这几件小事<span>权限 · 连接 · 隐私</span></summary><div class="help-grid"><section><h3>按住，才开始聆听</h3><p>首次允许麦克风后，请重新按住。准备提示变为“正在聆听”时再讲话；松手立即结束。切换页面、失焦或设备断开会取消当前轮。</p></section><section><h3>连接你自己的电脑</h3><p>运行前须由电脑端准备好离线模型与服务。手机使用可信 HTTPS 局域网地址；localhost 仅指当前设备。证书须匹配地址并经本人核对信任，请勿跳过证书警告。</p></section><section><h3>留在本地，不等于持久保存</h3><p>每轮最长60秒，历史最多50条，刷新或注销后清除。复制终稿会写入系统剪贴板。失败草稿不作为完整结果；本服务没有云端识别回退。</p></section><section><h3>遇到麦克风问题</h3><p>在浏览器站点设置中允许麦克风，检查设备连接，使用支持 AudioWorklet 和音频时钟映射的浏览器。无法确定松手边界时会明确取消，不继续收音来掩盖问题。</p><p v-if="connection.configId" class="config-id">模型配置：{{ connection.configId }}</p></section></div></details>
    </main>
  </div>
  <HoldControls :can-start="canStart" :active="!!active" :phase="active?.phase" :can-copy="!!latest" :can-clear="!view.activeId && !!view.cards.length" :actions="actions" @cancel="rounds.cancel('user')" @copy="copy(latest)" @clear="rounds.clear()" />
  <p class="sr-only" role="status" aria-live="polite" aria-atomic="true">{{ view.announcement }}</p>
  <div v-if="notice" class="toast" role="status">{{ notice }}<button aria-label="关闭提示" @click="notice = ''">×</button></div>
</template>
