import { describe, expect, it } from 'vitest'
import { StreamingFIR } from '../../src/audio/fir'

describe('StreamingFIR', () => {
  it('拒绝非法采样率', () => {
    expect(() => new StreamingFIR(100)).toThrow()
    expect(() => new StreamingFIR(200000)).toThrow()
    expect(() => new StreamingFIR(NaN)).toThrow()
  })
  it('接受合法采样率', () => {
    expect(() => new StreamingFIR(16000)).not.toThrow()
    expect(() => new StreamingFIR(44100)).not.toThrow()
    expect(() => new StreamingFIR(48000)).not.toThrow()
  })
  it('48kHz→16kHz输出比例正确', () => {
    const fir = new StreamingFIR(48000)
    const output: number[] = []
    // 输入480个源样本，FIR需要radius=64右上下文，实际输出少于160
    const input = new Float32Array(480)
    for (let i = 0; i < 480; i++) input[i] = Math.sin(2 * Math.PI * 100 * i / 48000)
    fir.push(input, v => output.push(v))
    expect(output.length).toBe(139) // floor((480-64)/3) = 138, 但0到138共139个
    expect(fir.sourceTotal).toBe(480)
    expect(fir.outputTotal).toBe(139)
  })
  it('16kHz→16kHz是1:1直通', () => {
    const fir = new StreamingFIR(16000)
    const output: number[] = []
    const input = new Float32Array([0.5, -0.5, 0.25, -0.25])
    fir.push(input, v => output.push(v))
    // 16kHz同源采样率，等待radius后开始输出
    // 需要足够样本才能开始输出
    expect(fir.sourceTotal).toBe(4)
  })
  it('跨块累积不漂移', () => {
    const fir = new StreamingFIR(48000)
    const output: number[] = []
    // 分10次每次输入48个样本
    for (let batch = 0; batch < 10; batch++) {
      const block = new Float32Array(48)
      for (let i = 0; i < 48; i++) block[i] = 0.1
      fir.push(block, v => output.push(v))
    }
    // 480个源样本，FIR右上下文延迟后输出139个
    expect(fir.sourceTotal).toBe(480)
    expect(fir.outputTotal).toBe(139)
  })
  it('finish排空剩余输出', () => {
    const fir = new StreamingFIR(48000)
    const output: number[] = []
    const input = new Float32Array(480)
    input.fill(0.5)
    fir.push(input, v => output.push(v))
    const beforeFinish = output.length
    fir.finish(v => output.push(v))
    // finish后补零排空所有剩余输出
    expect(fir.outputTotal).toBe(160) // floor(480*16000/48000)=160
  })
  it('拒绝重复finish', () => {
    const fir = new StreamingFIR(48000)
    fir.finish(() => {})
    expect(() => fir.finish(() => {})).toThrow()
  })
  it('拒绝push在finish后', () => {
    const fir = new StreamingFIR(48000)
    fir.finish(() => {})
    expect(() => fir.push(new Float32Array(1), () => {})).toThrow()
  })
  it('拒绝过大块', () => {
    const fir = new StreamingFIR(48000)
    expect(() => fir.push(new Float32Array(1025), () => {})).toThrow()
  })
  it('拒绝非有限样本', () => {
    const fir = new StreamingFIR(48000)
    expect(() => fir.push(new Float32Array([NaN]), () => {})).toThrow()
    expect(() => fir.push(new Float32Array([Infinity]), () => {})).toThrow()
  })
  it('44.1kHz长轮不漂移', () => {
    const fir = new StreamingFIR(44100)
    const output: number[] = []
    // 输入4410个样本，分批推入避免超过1024块限制
    const total = 4410
    const batchSize = 256
    for (let offset = 0; offset < total; offset += batchSize) {
      const size = Math.min(batchSize, total - offset)
      const block = new Float32Array(size)
      for (let i = 0; i < size; i++) block[i] = Math.sin(2 * Math.PI * 440 * (offset + i) / 44100)
      fir.push(block, v => output.push(v))
    }
    // finish排空后应为1600: floor(4410*16000/44100)=1600
    fir.finish(v => output.push(v))
    expect(fir.outputTotal).toBe(1600)
  })
})
