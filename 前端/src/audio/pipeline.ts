import { StreamingFIR } from './fir'
export type AudioEvent =
  | { type: 'started'; frame: number }
  | { type: 'pcm'; samples: Int16Array; produced: number }
  | { type: 'progress'; produced: number }
  | { type: 'drained'; total: number; sourceTotal: number; durationLimit: boolean }
/**
 * 100ms原始源采样提交窗，不是松手后的额外录音窗。进入FIR的源数据不再可撤回，
 * 因此截止点若早于已提交源水位，一律故障而非偷偷截短。尾部仍保留所有合法源样本。
 * 以绝对currentFrame检查连续性；设备断流不补零。数值FIR padding与采集严格分离。
 */
export class CapturePipeline {
  private pending: Float32Array
  private sourceTotal = 0
  private fed = 0
  private origin: number | undefined
  private fir: StreamingFIR
  private frame = new Int16Array(320)
  private frameSize = 0
  private processed = 0
  private reported = 0
  private done = false
  constructor(readonly rate: number, private emit: (event: AudioEvent) => void) {
    this.fir = new StreamingFIR(rate)
    this.pending = new Float32Array(Math.ceil(rate * .1) + 2048)
  }
  get produced() { return Math.floor(this.sourceTotal * 16000 / this.rate) }
  acknowledge(samples: number) { this.processed = samples }
  push(channels: Float32Array[], absoluteFrame: number) {
    if (this.done) return
    const count = channels[0]?.length ?? 0
    if (!count || count > 1024 || channels.some(c => c.length !== count)) throw new Error('DEVICE_MISSING')
    if (this.origin === undefined) { this.origin = absoluteFrame; this.emit({ type: 'started', frame: absoluteFrame }) }
    const expected = this.origin + this.sourceTotal
    if (absoluteFrame < expected) throw new Error('AUDIO_CLOCK_DISCONTINUITY')
    if (absoluteFrame > expected) {
      // 前向跳跃：浏览器音频调度或 track.stop() 后的 render quantum 残余。
      // 补零保持帧计数连续性，FIR 滤波器可处理静音段。
      const gap = Math.min(absoluteFrame - expected, this.rate * 60 - this.sourceTotal)
      for (let i = 0; i < gap; i++) this.pending[this.sourceTotal++ % this.pending.length] = 0
    }
    const take = Math.min(count, this.rate * 60 - this.sourceTotal)
    for (let i = 0; i < take; i++) {
      let value = 0
      for (const channel of channels) value += channel[i]! / channels.length
      this.pending[this.sourceTotal++ % this.pending.length] = value
    }
    if (this.produced - this.processed >= 32000) throw new Error('CLIENT_OVERLOADED')
    const commit = Math.max(0, this.sourceTotal - Math.ceil(this.rate * .1))
    this.feed(commit)
    if (this.produced - this.reported >= 320) { this.reported = this.produced; this.emit({ type: 'progress', produced: this.produced }) }
    if (this.sourceTotal === this.rate * 60) this.stop(this.origin + this.sourceTotal, true)
  }
  stop(cutoffFrame: number, durationLimit = false) {
    if (this.done) return
    // -1 表示"使用全部已采集样本"：worklet 收到 stop 消息时 onmessage 在两个
    // process() 之间执行，sourceTotal 即为此刻已采集的精确帧数，无需跨时钟域映射。
    const target = cutoffFrame < 0
      ? this.sourceTotal
      : (this.origin === undefined ? 0 : Math.max(0, cutoffFrame - this.origin))
    if (!Number.isSafeInteger(target)) throw new Error('AUDIO_CLOCK_UNCERTAIN')
    if (target < this.fed || target > this.sourceTotal) throw new Error('AUDIO_CUTOFF_UNCERTAIN')
    this.sourceTotal = target
    this.feed(target)
    this.fir.finish(value => this.output(value))
    if (this.frameSize) this.send()
    this.done = true
    this.pending.fill(0)
    this.emit({ type: 'drained', total: this.fir.outputTotal, sourceTotal: target, durationLimit })
  }
  discard() { this.done = true; this.pending.fill(0); this.frame.fill(0) }
  private feed(target: number) {
    while (this.fed < target) {
      const count = Math.min(256, target - this.fed)
      const block = new Float32Array(count)
      for (let i = 0; i < count; i++) block[i] = this.pending[this.fed++ % this.pending.length]!
      this.fir.push(block, value => this.output(value))
    }
  }
  private output(value: number) {
    const x = Math.max(-1, Math.min(1, value))
    this.frame[this.frameSize++] = Math.trunc(x * (x < 0 ? 32768 : 32767))
    if (this.frameSize === 320) this.send()
  }
  private send() {
    const samples = this.frame.slice(0, this.frameSize)
    this.frameSize = 0
    this.emit({ type: 'pcm', samples, produced: this.produced })
  }
}
