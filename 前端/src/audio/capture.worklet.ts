import { CapturePipeline, type AudioEvent } from './pipeline'
// AudioWorklet专用全局声明，不把DOM控制器或应用凭据带入音频线程。
declare const sampleRate: number
declare const currentFrame: number
declare abstract class AudioWorkletProcessor { readonly port: MessagePort; abstract process(inputs: Float32Array[][]): boolean }
declare function registerProcessor(name: string, processor: typeof AudioWorkletProcessor): void
class LasrCapture extends AudioWorkletProcessor {
  private pipeline?: CapturePipeline
  private failed = false
  constructor() {
    super()
    this.port.onmessage = (event: MessageEvent) => {
      try {
        const message = event.data
        if (message.type === 'start' && !this.pipeline && !this.failed) this.pipeline = new CapturePipeline(sampleRate, output => this.output(output))
        if (message.type === 'stop') this.pipeline?.stop(message.frame)
        if (message.type === 'processed') this.pipeline?.acknowledge(message.samples)
        if (message.type === 'discard') { this.pipeline?.discard(); this.pipeline = undefined; this.failed = true }
      } catch (error) { this.fault(error) }
    }
  }
  private output(event: AudioEvent) {
    if (event.type === 'pcm') this.port.postMessage(event, [event.samples.buffer])
    else this.port.postMessage(event)
  }
  private fault(error: unknown) {
    this.pipeline?.discard(); this.pipeline = undefined; this.failed = true
    this.port.postMessage({ type: 'fault', code: error instanceof Error ? error.message : 'AUDIO_FAILED' })
  }
  process(inputs: Float32Array[][]) {
    try { this.pipeline?.push(inputs[0] ?? [], currentFrame) } catch (error) { this.fault(error) }
    // 输出保持零，麦克风永远不回放到扬声器；连接destination只让渲染时钟持续工作。
    return !this.failed
  }
}
registerProcessor('lasr-capture', LasrCapture)
