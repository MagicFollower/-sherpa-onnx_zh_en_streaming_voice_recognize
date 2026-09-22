import { AUDIO, type CancelReason, type ServerMessage } from '../protocol/messages'
import { AudioLedger, encodeAudio } from '../protocol/audio'
import { requireThat } from '../protocol/schema'
import type { AudioCapture, DrainResult } from '../audio/capture'
import type { Transport } from '../services/connection'
export type Phase = 'preparing' | 'recording' | 'draining' | 'finalizing' | 'cancelling' | 'completed' | 'cancelled' | 'failed'
export interface Card { id: string; created: number; phase: Phase; text: string; revision: number; discarded: boolean; note: string; samples: number }
export interface RoundView { cards: Card[]; activeId: string; announcement: string; warning: boolean }
export const newRoundView = (): RoundView => ({ cards: [], activeId: '', announcement: '', warning: false })
interface Active { card: Card; capture: AudioCapture; ledger: AudioLedger; generation: number; sent: boolean; held: boolean; rate: number; reason?: CancelReason; timers: ReturnType<typeof setTimeout>[] }
export interface RoundOptions { capture: () => AudioCapture; canStart: () => boolean; configId: () => string; fault: (message: string) => void; now?: () => number; uuid?: () => string }
const localError = (error: unknown) => {
  const name = error instanceof Error ? error.name : ''
  const code = error instanceof Error ? error.message : ''
  if (name === 'NotAllowedError') return '麦克风权限未获允许，请在浏览器设置中授权后重新按住。'
  if (name === 'NotFoundError' || code === 'DEVICE_MISSING') return '麦克风已断开或不可用，请检查设备。'
  if (code.includes('CLOCK') || code.includes('CUTOFF')) return '音频采样边界异常，本轮已取消。请检查浏览器 AudioWorklet 兼容性后重试。'
  if (code === 'CLIENT_OVERLOADED') return '处理积压达到上限，本轮已中断，请稍后重试。'
  return '音频准备或处理失败，请检查可信 HTTPS、麦克风权限与浏览器 AudioWorklet 能力。'
}
/**
 * 一次意图只创建一张卡。generation是异步权限/音频回调的撤销栅栏，UUID用于网络归属，
 * 两者不可互相替代。取消立即discard正文，只有协议终态或连接销毁才释放活动槽。
 * 本类是页面终态唯一写入者；组件不凭最后一条草稿猜测成功，不持久化历史。
 */
export class RoundManager {
  transport?: Transport
  private active?: Active
  private generation = 0
  constructor(readonly view: RoundView, private options: RoundOptions) {}
  begin(source: 'pointer' | 'keyboard'): boolean {
    if (this.active || !this.transport?.session || !this.options.canStart()) return false
    const card: Card = { id: (this.options.uuid ?? (() => crypto.randomUUID()))(), created: Date.now(), phase: 'preparing', text: '', revision: 0, discarded: false, note: '正在准备麦克风，请保持按住。', samples: 0 }
    this.view.cards.push(card)
    if (this.view.cards.length > 50) this.view.cards.shift()
    // Vue代理数组中的对象与原对象可能不同；后续只写回视图中的引用，保证响应式更新。
    const visible = this.view.cards.at(-1)!
    const active: Active = { card: visible, capture: this.options.capture(), ledger: new AudioLedger(), generation: ++this.generation, sent: false, held: true, rate: 0, timers: [] }
    this.active = active; this.view.activeId = card.id; this.announce('正在准备麦克风')
    void this.prepare(active, source)
    return true
  }
  private async prepare(a: Active, source: 'pointer' | 'keyboard') {
    try {
      const rate = await a.capture.prepare()
      if (!this.valid(a)) { a.capture.dispose(); return }
      if (!a.held) { a.capture.dispose(); this.cancel('user', '准备期间已松手，请重新按住。'); return }
      a.rate = rate
      this.send(a, { type: 'start', input_source: source, capture_sample_rate: rate, ...AUDIO, language: 'zh-en' })
      a.sent = true
      this.timer(a, 5000, () => this.cancel('client_timeout', '服务准备超时，请稍后重新按住。'))
    } catch (error) { if (this.valid(a)) this.finish(a, 'failed', localError(error)) }
  }
  async release(eventTime: number) {
    const a = this.active
    if (!a) return
    a.held = false
    if (a.card.phase === 'preparing') { this.cancel('user', '准备期间已松手，请重新按住开始。'); return }
    if (a.card.phase !== 'recording') return
    this.clearTimers(a); a.card.phase = 'draining'; a.card.note = '正在提交松手前的尾音。'; this.announce('已停止采集，正在整理文字')
    // 15秒从release开始，包含2秒drain；绝不等stop ACK才重置期限。
    this.timer(a, 15000, () => this.cancel('client_timeout', '等待终稿超时，本轮未完成。'))
    const drainTimer = this.timer(a, 2000, () => this.cancel('client_timeout', 'AUDIO_DRAIN_TIMEOUT：尾音排空超时，本轮已取消。'))
    try {
      const result = await a.capture.stop()
      clearTimeout(drainTimer)
      if (this.valid(a)) this.stopped(a, result)
    } catch (error) { if (this.valid(a)) this.cancel('client_timeout', localError(error)) }
  }
  cancel(reason: CancelReason, note = '本轮已取消，未完成的内容已丢弃。') {
    const a = this.active
    if (!a || a.card.discarded) return
    ++this.generation; a.held = false; a.card.discarded = true; a.card.text = ''; a.card.note = note
    a.capture.dispose(); this.clearTimers(a)
    if (!a.sent) { this.finish(a, 'cancelled', note); return }
    a.card.phase = 'cancelling'; a.reason = reason; this.announce('已停止，正在取消')
    try {
      // 取消不经音频队列，不等待ACK或bufferedAmount回落；连接失效直接本地中断。
      this.send(a, { type: 'cancel', reason })
      this.timer(a, 2000, () => this.protocolFailure('取消确认超时，已关闭连接。'))
    } catch { this.protocolFailure('无法确认取消，已关闭连接。') }
    // generation已递增，finish的栅栏会跳过清理；此处补完。
    this.active = undefined; this.view.activeId = ''
  }
  message(message: ServerMessage) {
    const a = this.active
    if (!a || !('utterance_id' in message) || message.utterance_id !== a.card.id) return
    try {
      requireThat(message.session_id === this.transport?.session)
      if (message.type === 'ready') {
        if (a.card.discarded) return
        requireThat(a.sent && a.card.phase === 'preparing' && a.held && message.config_id === this.options.configId())
        this.clearTimers(a); a.card.phase = 'recording'; a.card.note = '正在聆听，松手生成终稿。'; this.announce('正在聆听')
        a.capture.start({ pcm: (samples, produced) => this.pcm(a, samples, produced), progress: produced => this.progress(a, produced),
          fault: code => { if (this.valid(a)) this.cancel(code === 'CLIENT_OVERLOADED' ? 'client_overload' : 'device_lost', localError(new Error(code))) },
          limit: result => {
            if (!this.valid(a)) return
            this.clearTimers(a); this.timer(a, 15000, () => this.cancel('client_timeout', '等待终稿超时。'))
            this.stopped(a, result)
          } })
        // 源帧达到60秒由worklet精确stop；65秒防止音频线程无回调而悬挂。
        this.timer(a, 65000, () => this.cancel('client_timeout', '音频线程未能在时限内结束。'))
      } else if (message.type === 'ack') {
        a.ledger.accept(message); a.capture.acknowledge(a.ledger.processed)
        this.updateWarning(a.card.samples - a.ledger.processed)
      } else if (message.type === 'partial') {
        if (a.card.discarded || message.revision <= a.card.revision) return
        requireThat(['recording', 'draining', 'finalizing'].includes(a.card.phase))
        requireThat(a.card.revision !== 0 || message.revision === 1)
        a.card.revision = message.revision; a.card.text = message.text
      } else if (message.type === 'final') {
        a.ledger.final(message)
        requireThat(message.revision > a.card.revision && (a.card.revision !== 0 || message.revision === 1))
        if (a.card.discarded) this.finish(a, 'cancelled', '本轮已丢弃，结果在取消前完成。')
        else {
          a.card.text = message.text; a.card.revision = message.revision
          this.finish(a, 'completed', message.status === 'empty' ? '未检测到语音，请重新按住。' : '已完成 · 本地流式识别')
        }
      } else if (message.type === 'cancelled') {
        requireThat(a.card.discarded && message.reason === a.reason)
        this.finish(a, 'cancelled', a.card.note)
      } else if (message.type === 'error') {
        if (message.terminal || (a.card.phase === 'preparing' && ['BUSY', 'MODEL_NOT_READY'].includes(message.code))) this.finish(a, 'failed', `${message.code}：${message.message}`)
      }
    } catch { this.protocolFailure('音频计数、结果版本或协议不一致，本轮中断且连接已关闭。') }
  }
  disconnect(note: string) {
    const a = this.active
    if (a) this.finish(a, 'failed', note)
    this.transport = undefined; ++this.generation
  }
  clear() { if (!this.active) this.view.cards.splice(0) }
  destroy() { this.disconnect('本轮已中断。'); this.view.cards.splice(0); this.view.announcement = '' }
  private stopped(a: Active, result: DrainResult) {
    try {
      requireThat(result.total === a.ledger.total && result.total === Math.floor(result.sourceTotal * 16000 / a.rate))
      a.ledger.stop()
      this.send(a, { type: 'stop', last_seq: a.ledger.lastSeq, total_samples: a.ledger.total, reason: result.durationLimit ? 'duration_limit' : 'release' })
      a.card.samples = result.total; a.card.phase = 'finalizing'; a.card.note = result.durationLimit ? '已达60秒上限，正在整理文字。' : '正在整理文字，可以取消本轮。'
      this.announce('采集已结束，正在整理文字')
    } catch { this.protocolFailure('尾帧与采样计数不一致，已关闭连接。') }
  }
  private pcm(a: Active, samples: Int16Array, produced: number) {
    if (!this.valid(a)) return
    this.progress(a, produced)
    if (!this.valid(a)) return
    try {
      const buffer = encodeAudio(a.card.id, a.ledger.lastSeq + 1, samples)
      requireThat(['recording', 'draining'].includes(a.card.phase))
      a.ledger.push(samples.length); this.transport!.send(buffer)
    } catch { this.protocolFailure('发送音频失败，本轮不会重传。') }
  }
  private progress(a: Active, produced: number) {
    if (!this.valid(a)) return
    a.card.samples = produced
    const buffered = this.transport?.bufferedAmount ?? 0
    const gap = produced - a.ledger.processed
    if (gap >= 32000 || buffered >= 67200) this.cancel('client_overload', '处理积压达到上限，请稍后重新讲话。')
    else this.updateWarning(gap)
  }
  private finish(a: Active, phase: Phase, note: string) {
    if (this.active !== a) return
    a.capture.dispose(); this.clearTimers(a); ++this.generation
    a.card.phase = phase; a.card.note = note
    if (a.card.discarded) a.card.text = ''
    this.active = undefined; this.view.activeId = ''; this.announce(note)
  }
  private updateWarning(gap: number) {
    // 仅以 gap（已采集未确认样本数）作为负载指标。
    // bufferedAmount 是 WebSocket 发送缓冲区瞬时快照，在 localhost 上锯齿波振荡，
    // 规范 05 §8 明确标注“只作保守辅助指标”，不参与用户可见的 warning 判定。
    // 阈值 16000（1 秒）避免正常处理抖动导致的误触发；硬取消 32000 不受影响。
    if (!this.view.warning && gap >= 16000) this.view.warning = true
    else if (this.view.warning && gap < 8000) this.view.warning = false
  }
  private valid(a: Active) { return this.active === a && a.generation === this.generation && !a.card.discarded }
  private send(a: Active, fields: Record<string, unknown>) { if (!this.transport) throw new Error('NETWORK_LOST'); this.transport.send({ ...fields, utterance_id: a.card.id }) }
  private timer(a: Active, ms: number, task: () => void) { const id = setTimeout(() => { if (this.active === a) task() }, ms); a.timers.push(id); return id }
  private clearTimers(a: Active) { a.timers.forEach(clearTimeout); a.timers = [] }
  private announce(text: string) { this.view.announcement = text }
  private protocolFailure(note: string) { this.transport?.close(); this.disconnect(note); this.options.fault(note) }
}
