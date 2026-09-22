import { computed, onMounted, onUnmounted, reactive, ref } from 'vue'
import { BrowserCapture, audioCapability } from '../audio/capture'
import { ApiError, LocalApi } from '../services/api'
import { LasrConnection } from '../services/connection'
import { newRoundView, RoundManager, type Card } from '../state/rounds'
export function useStudio() {
  const view = reactive(newRoundView())
  const connection = reactive({ paired: false, connected: false, connecting: false, modelReady: false, busy: false, configId: '', error: '', pairing: false, retryAt: 0 })
  const notice = ref('')
  const clock = ref(Date.now())
  const capability = ref<string>()
  const api = new LocalApi()
  let socket: LasrConnection | undefined
  let disposed = false
  let generation = 0
  let polling = false
  let ticker: ReturnType<typeof setInterval>
  let poller: ReturnType<typeof setInterval>
  const canStart = computed(() => connection.connected && connection.modelReady && !connection.busy && !view.activeId && !capability.value)
  const active = computed(() => view.cards.find(card => card.id === view.activeId))
  const latest = computed(() => [...view.cards].reverse().find(card => card.phase === 'completed' && card.text && !card.discarded))
  const retrySeconds = computed(() => Math.max(0, Math.ceil((connection.retryAt - clock.value) / 1000)))
  const rounds = new RoundManager(view, { capture: () => new BrowserCapture(), canStart: () => canStart.value, configId: () => connection.configId, fault: reason => offline(reason) })
  function offline(reason: string) {
    socket?.close(); socket = undefined
    connection.connected = false; connection.connecting = false; connection.error = reason
    rounds.disconnect(reason)
  }
  async function refresh(connect = false) {
    if (polling || disposed || view.activeId) return
    polling = true
    const token = generation
    try {
      const status = await api.status()
      if (disposed || token !== generation) return
      connection.paired = true; connection.modelReady = status.model_ready; connection.busy = status.busy; connection.configId = status.config_id
      if (connect && !socket) {
        if (!status.connection_available) { connection.error = '此授权已有其他页面连接，请关闭旧页面再重连。'; return }
        connection.connecting = true; connection.error = ''
        socket = new LasrConnection({
          message: message => {
            if (message.type === 'hello') {
              connection.connected = true; connection.connecting = false; connection.modelReady = message.model_ready; connection.configId = message.config_id
            } else if (message.type === 'error' && message.scope === 'connection') {
              offline(`${message.code}：${message.message}`)
              if (message.action === 'pair') connection.paired = false
            } else rounds.message(message)
          },
          closed: reason => { offline(reason); void refresh(false) },
        })
        rounds.transport = socket
      }
    } catch (error) {
      if (disposed || token !== generation) return
      if (error instanceof ApiError && error.status === 401) { offline('请先与本机服务配对。'); connection.paired = false; connection.error = '' }
      else connection.error = error instanceof ApiError ? error.message : '无法读取本地服务状态，请检查服务与可信 HTTPS。'
    } finally { polling = false }
  }
  async function pair(code: string) {
    if (connection.pairing || retrySeconds.value || disposed) return
    connection.pairing = true; connection.error = ''
    const token = generation
    try { await api.pair(code); if (!disposed && generation === token) await refresh(true) }
    catch (error) {
      if (disposed || token !== generation) return
      connection.error = error instanceof ApiError ? error.message : '配对响应异常，请检查本地服务版本。'
      if (error instanceof ApiError && error.status === 429) connection.retryAt = Date.now() + Math.max(1, error.retryAfter) * 1000
    } finally { connection.pairing = false }
  }
  async function logout() {
    ++generation
    rounds.cancel('user'); socket?.close(); socket = undefined; rounds.destroy()
    connection.paired = false; connection.connected = false; connection.connecting = false; connection.modelReady = false; connection.error = ''
    try { await api.logout(); notice.value = '已注销，页面文字已清除。' }
    catch { notice.value = '页面文字已清除，但服务端注销未确认；请关闭页面并在电脑端撤销授权。' }
  }
  async function copy(card?: Card) {
    if (!card || card.phase !== 'completed' || card.discarded || !card.text) return
    try { await navigator.clipboard.writeText(card.text); notice.value = '终稿已复制到系统剪贴板。' }
    catch { notice.value = '浏览器不允许写入剪贴板，请直接选中文字复制。' }
  }
  onMounted(() => {
    capability.value = audioCapability()
    void refresh(true)
    ticker = setInterval(() => { clock.value = Date.now() }, 1000)
    // status和heartbeat不续授权业务idle；空闲低频查询，不发送试探start或音频。
    poller = setInterval(() => { if (connection.paired) void refresh(false) }, 10000)
  })
  onUnmounted(() => { disposed = true; ++generation; clearInterval(ticker); clearInterval(poller); socket?.close(); rounds.destroy() })
  return { view, connection, notice, capability, canStart, active, latest, retrySeconds, rounds, refresh, pair, logout, copy }
}
