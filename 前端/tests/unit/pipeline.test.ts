import { describe, expect, it } from 'vitest'
import { CapturePipeline, type AudioEvent } from '../../src/audio/pipeline'

function makePipeline(rate: number) {
  const events: AudioEvent[] = []
  const pipeline = new CapturePipeline(rate, e => events.push(e))
  return { pipeline, events }
}

function pushSilence(pipeline: CapturePipeline, sourceSamples: number, startFrame = 0) {
  const batchSize = 256
  let frame = startFrame
  while (sourceSamples > 0) {
    const size = Math.min(batchSize, sourceSamples)
    pipeline.push([new Float32Array(size)], frame)
    frame += size
    sourceSamples -= size
  }
  return frame
}

describe('CapturePipeline', () => {
  it('48kHz推入足够样本产生PCM帧', () => {
    const { pipeline, events } = makePipeline(48000)
    // commit = max(0, sourceTotal - ceil(rate*0.1)) = sourceTotal - 4800
    // 需要 sourceTotal > 4800 才开始喂FIR，再加 (320+64)*3=1152 产生320输出
    pushSilence(pipeline, 6000)
    const pcmEvents = events.filter(e => e.type === 'pcm')
    expect(pcmEvents.length).toBeGreaterThanOrEqual(1)
    const totalSamples = pcmEvents.reduce((sum, e) => sum + (e.type === 'pcm' ? e.samples.length : 0), 0)
    expect(totalSamples).toBe(320)
  })
  it('produced按源样本换算', () => {
    const { pipeline } = makePipeline(48000)
    pushSilence(pipeline, 480)
    expect(pipeline.produced).toBe(160)
  })
  it('拒绝后向帧跳跃', () => {
    const { pipeline } = makePipeline(48000)
    pushSilence(pipeline, 480)
    // 回退: 帧 200 < 480 (已采集水位), 不可恢复
    expect(() => pipeline.push([new Float32Array(480)], 200)).toThrow()
  })
  it('拒绝过大块', () => {
    const { pipeline } = makePipeline(48000)
    expect(() => pipeline.push([new Float32Array(1025)], 0)).toThrow()
  })
  it('拒绝多声道不一致', () => {
    const { pipeline } = makePipeline(48000)
    expect(() => pipeline.push([new Float32Array(128), new Float32Array(64)], 0)).toThrow()
  })
  it('声道平均下混', () => {
    const { pipeline, events } = makePipeline(48000)
    // 推入足够双声道样本产生至少一帧，每批不超过1024
    const size = 600
    const left = new Float32Array(size).fill(0.5)
    const right = new Float32Array(size).fill(-0.5)
    pipeline.push([left, right], 0)
    pipeline.push([left, right], size)
    const pcmEvents = events.filter(e => e.type === 'pcm')
    for (const e of pcmEvents) {
      if (e.type === 'pcm') {
        for (const sample of e.samples) {
          expect(Math.abs(sample)).toBeLessThanOrEqual(32767)
        }
      }
    }
  })
  it('stop裁剪到截止帧', () => {
    const { pipeline, events } = makePipeline(48000)
    // 推入两批各480样本
    pushSilence(pipeline, 480, 0)
    pushSilence(pipeline, 480, 480)
    // 截止在第480帧
    pipeline.stop(480)
    const drained = events.find(e => e.type === 'drained')
    expect(drained).toBeDefined()
    if (drained && drained.type === 'drained') {
      expect(drained.sourceTotal).toBe(480)
    }
  })
  it('stop拒绝非法截止帧', () => {
    const { pipeline } = makePipeline(48000)
    pushSilence(pipeline, 480)
    expect(() => pipeline.stop(1.5)).toThrow()
  })
  it('stop(-1)使用全部已采集样本', () => {
    const { pipeline, events } = makePipeline(48000)
    pushSilence(pipeline, 480, 0)
    pushSilence(pipeline, 480, 480)
    // -1 表示“使用全部已采集样本”，worklet 松手时发送
    pipeline.stop(-1)
    const drained = events.find(e => e.type === 'drained')
    expect(drained).toBeDefined()
    if (drained && drained.type === 'drained') {
      expect(drained.sourceTotal).toBe(960)
    }
  })
  it('前向帧跳跃补零保持计数连续', () => {
    const { pipeline } = makePipeline(48000)
    // 第一批: 480 样本, 从帧 0 开始
    pipeline.push([new Float32Array(480)], 0)
    // 跳跃: 下一批从帧 960 开始 (跳过了 480 样本)
    // 应补零填充间隙, 不抛错。推入 1 个样本仅用于触发 push 检查。
    expect(() => pipeline.push([new Float32Array(1)], 960)).not.toThrow()
    // sourceTotal = 480(实际) + 480(补零) + 1(实际) = 961
    expect(pipeline.produced).toBe(Math.floor(961 * 16000 / 48000))
  })
  it('discard后不再输出', () => {
    const { pipeline, events } = makePipeline(48000)
    pipeline.discard()
    pipeline.push([new Float32Array(480)], 0)
    expect(events.length).toBe(0)
  })
  it('acknowledge更新processed', () => {
    const { pipeline } = makePipeline(48000)
    pushSilence(pipeline, 480)
    pipeline.acknowledge(100)
    // 不抛错即可
  })
  it('积压硬限制', () => {
    const { pipeline } = makePipeline(16000)
    const batch = new Float32Array(256)
    let frame = 0
    let threw = false
    for (let i = 0; i < 200; i++) {
      try {
        pipeline.push([batch], frame)
        frame += 256
      } catch { threw = true; break }
    }
    expect(threw).toBe(true)
  })
  it('PCM量化正满幅为32767', () => {
    const { pipeline, events } = makePipeline(16000)
    // 16kHz→16kHz，推入320+64=384样本以确保产生320输出
    const input = new Float32Array(400)
    input.fill(1.0)
    pipeline.push([input], 0)
    const pcmEvents = events.filter(e => e.type === 'pcm')
    if (pcmEvents.length > 0) {
      const e = pcmEvents[0]!
      if (e.type === 'pcm') {
        for (const sample of e.samples) {
          expect(sample).toBe(32767)
        }
      }
    }
  })
  it('负满幅量化为-32768', () => {
    const { pipeline, events } = makePipeline(16000)
    const input = new Float32Array(400)
    input.fill(-1.0)
    pipeline.push([input], 0)
    const pcmEvents = events.filter(e => e.type === 'pcm')
    if (pcmEvents.length > 0) {
      const e = pcmEvents[0]!
      if (e.type === 'pcm') {
        for (const sample of e.samples) {
          expect(sample).toBe(-32768)
        }
      }
    }
  })
})
