/**
 * 有界、跨块的中心对称窗化sinc FIR。输出索引始终由累计整数计数决定，
 * 不能逐块round，否则44.1kHz长轮会漂移。滤波中心直接对齐源时间轴，
 * 在线等待右侧64点，排空时只用数值零填充左右上下文；不延长录音或网络样本。
 * 全部计算在AudioWorklet运行；固定环形缓存与预计算相位避免主线程大数组/逐点三角运算。
 */
export class StreamingFIR {
  readonly radius = 64
  private ring = new Float32Array(4096)
  private kernels: Float64Array[] = []
  private phases: number
  sourceTotal = 0
  outputTotal = 0
  private finished = false
  constructor(readonly sourceRate: number) {
    if (!Number.isSafeInteger(sourceRate) || sourceRate < 8000 || sourceRate > 192000) throw new Error('不支持的采样率')
    const gcd = (a: number, b: number): number => b ? gcd(b, a % b) : a
    this.phases = 16000 / gcd(sourceRate, 16000)
    const cutoff = .45 * Math.min(1, 16000 / sourceRate)
    for (let p = 0; p < this.phases; p++) {
      const weights = new Float64Array(this.radius * 2 + 1)
      let sum = 0
      for (let k = -this.radius; k <= this.radius; k++) {
        const x = k - p / this.phases
        const window = Math.abs(x) > this.radius ? 0 : .42 + .5 * Math.cos(Math.PI * x / this.radius) + .08 * Math.cos(2 * Math.PI * x / this.radius)
        const sinc = Math.abs(x) < 1e-10 ? 2 * cutoff : Math.sin(2 * Math.PI * cutoff * x) / (Math.PI * x)
        sum += weights[k + this.radius] = sinc * window
      }
      for (let k = 0; k < weights.length; k++) weights[k] = weights[k]! / sum
      this.kernels.push(weights)
    }
  }
  push(samples: Float32Array, emit: (sample: number) => void) {
    if (this.finished || samples.length > 1024) throw new Error('FIR块边界非法')
    for (const sample of samples) {
      if (!Number.isFinite(sample)) throw new Error('无效音频样本')
      this.ring[this.sourceTotal++ % this.ring.length] = sample
      this.produce(false, emit)
    }
  }
  finish(emit: (sample: number) => void) {
    if (this.finished) throw new Error('重复排空')
    this.finished = true
    this.produce(true, emit)
    this.ring.fill(0)
  }
  private produce(final: boolean, emit: (sample: number) => void) {
    const target = Math.floor(this.sourceTotal * 16000 / this.sourceRate)
    while (this.outputTotal < target) {
      const numerator = this.outputTotal * this.sourceRate
      const center = Math.floor(numerator / 16000)
      if (!final && center + this.radius >= this.sourceTotal) break
      const phase = Math.round((numerator % 16000) * this.phases / 16000)
      const weights = this.kernels[phase]!
      let value = 0
      for (let k = -this.radius; k <= this.radius; k++) {
        const index = center + k
        if (index >= 0 && index < this.sourceTotal) value += this.ring[index % this.ring.length]! * weights[k + this.radius]!
      }
      this.outputTotal++
      emit(value)
    }
  }
}
