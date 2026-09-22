import workletUrl from './capture.worklet.ts?worker&url'
import type { AudioEvent } from './pipeline'
export interface DrainResult { total: number; sourceTotal: number; durationLimit: boolean }
export interface CaptureCallbacks {
  pcm: (samples: Int16Array, produced: number) => void
  progress: (produced: number) => void
  limit: (result: DrainResult) => void
  fault: (code: string) => void
}
export interface AudioCapture {
  prepare(): Promise<number>
  start(callbacks: CaptureCallbacks): void
  stop(): Promise<DrainResult>
  acknowledge(processed: number): void
  dispose(): void
}
export function audioCapability(): string | undefined {
  if (!globalThis.isSecureContext) return '需要可信 HTTPS；仅电脑本机 localhost 可用于开发。'
  if (!navigator.mediaDevices?.getUserMedia) return '浏览器未提供麦克风能力，请检查权限或更新浏览器。'
  if (typeof AudioContext === 'undefined' || typeof AudioWorkletNode === 'undefined') return '浏览器不支持 AudioWorklet，无法保证音频边界。'
  return undefined
}
/**
 * 生产入口永远真实getUserMedia，不存在URL/env假音频开关。测试只能注入AudioCapture接口。
 * dispose即刻关track；迟到授权也会被disposed检查回收。松手后先关track，worklet只裁剪
 * 已缓存源样本并drain，完成后关context，不等待模型final才关麦克风。
 */
export class BrowserCapture implements AudioCapture {
  private context?: AudioContext
  private stream?: MediaStream
  private node?: AudioWorkletNode
  private source?: MediaStreamAudioSourceNode
  private disposed = false
  private closing = false
  private callbacks?: CaptureCallbacks
  private drain?: { resolve: (result: DrainResult) => void; reject: (error: Error) => void }
  async prepare(): Promise<number> {
    const unavailable = audioCapability()
    if (unavailable) throw new Error(unavailable)
    // 在用户手势同步阶段创建和resume，避免移动浏览器在权限await之后拒绝自动播放。
    const context = this.context = new AudioContext({ latencyHint: 'interactive' })
    const resumed = context.resume()
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: false, autoGainControl: false, noiseSuppression: false }, video: false })
      this.stream = stream
      if (this.disposed) { this.stopTracks(); throw new Error('CAPTURE_DISPOSED') }
      await resumed
      await context.audioWorklet.addModule(workletUrl)
      if (this.disposed) throw new Error('CAPTURE_DISPOSED')
      this.node = new AudioWorkletNode(context, 'lasr-capture', { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] })
      this.source = context.createMediaStreamSource(stream)
      this.source.connect(this.node); this.node.connect(context.destination)
      this.node.port.onmessage = event => this.receive(event.data)
      this.node.onprocessorerror = () => this.fail('AUDIO_PROCESSOR_FAILED')
      for (const track of stream.getAudioTracks()) track.onended = () => { if (!this.closing) this.fail('DEVICE_MISSING') }
      // 不再创建 AudioClock 或 40ms 轮询定时器。
      // 松手截止点由 pipeline.sourceTotal 直接确定（worklet 精确帧计数），
      // 无需跨时钟域映射——彻底消除 getOutputTimestamp 在不同硬件上的初始行为差异。
      return context.sampleRate
    } catch (error) { this.dispose(); throw error }
  }
  start(callbacks: CaptureCallbacks) {
    if (this.disposed || !this.node) throw new Error('AUDIO_NOT_READY')
    this.callbacks = callbacks
    this.node.port.postMessage({ type: 'start' })
  }
  stop(): Promise<DrainResult> {
    if (this.disposed || this.drain || !this.node) return Promise.reject(new Error('AUDIO_NOT_READY'))
    // 先通知 worklet 设定截止点（pipeline.done=true），再停麦克风。
    // 顺序关键：stopTracks 同步生效，但 postMessage 异步；
    // 若先停轨道，音频线程在收到 stop 前会收到中断数据 → currentFrame 跳跃 → CLOCK 错误。
    return new Promise((resolve, reject) => {
      this.drain = { resolve, reject }
      this.node!.port.postMessage({ type: 'stop', frame: -1 })
      this.stopTracks()
    })
  }
  acknowledge(samples: number) { this.node?.port.postMessage({ type: 'processed', samples }) }
  dispose() {
    if (this.disposed) return
    this.disposed = true
    this.stopTracks()
    this.node?.port.postMessage({ type: 'discard' })
    if (this.node) { this.node.port.onmessage = null; this.node.port.close(); this.node.disconnect() }
    this.source?.disconnect()
    if (this.context && this.context.state !== 'closed') void this.context.close().catch(() => undefined)
    this.drain?.reject(new Error('CAPTURE_DISPOSED')); this.drain = undefined
    this.callbacks = undefined; this.stream = undefined; this.node = undefined; this.source = undefined
  }
  private stopTracks() { this.closing = true; this.stream?.getTracks().forEach(track => { track.onended = null; track.stop() }) }
  private fail(code: string) { const callback = this.callbacks?.fault; this.dispose(); callback?.(code) }
  private receive(event: AudioEvent | { type: 'fault'; code: string }) {
    if (this.disposed) return
    if (event.type === 'pcm') this.callbacks?.pcm(event.samples, event.produced)
    if (event.type === 'progress') this.callbacks?.progress(event.produced)
    if (event.type === 'fault') this.fail(event.code)
    if (event.type === 'drained') {
      const result: DrainResult = event
      const waiting = this.drain; this.drain = undefined
      const limit = this.callbacks?.limit
      this.dispose()
      if (waiting) waiting.resolve(result)
      else if (event.durationLimit) limit?.(result)
    }
  }
}
