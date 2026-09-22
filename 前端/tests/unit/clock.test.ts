import { describe, expect, it } from 'vitest'
import { AudioClock } from '../../src/audio/clock'

describe('AudioClock', () => {
  it('初始未就绪', () => {
    const clock = new AudioClock(48000)
    expect(clock.ready).toBe(false)
  })
  it('两次前进观察后稳定', () => {
    const clock = new AudioClock(48000)
    clock.observe({ contextTime: 1.0, performanceTime: 1000 }, 1000)
    expect(clock.ready).toBe(false)
    // 间隔须 >= 300ms 才满足稳定性阈值
    clock.observe({ contextTime: 1.35, performanceTime: 1350 }, 1350)
    expect(clock.ready).toBe(true)
  })
  it('接受过期performanceTime（stamp仍更新）', () => {
    const clock = new AudioClock(48000)
    // 过期数据不再拒绝，保证 stamp 始终新鲜
    expect(() => clock.observe({ contextTime: 1.0, performanceTime: 500 }, 1000)).not.toThrow()
  })
  it('接受未来performanceTime（stamp仍更新）', () => {
    const clock = new AudioClock(48000)
    expect(() => clock.observe({ contextTime: 1.0, performanceTime: 1060 }, 1000)).not.toThrow()
  })
  it('拒绝非有限contextTime', () => {
    const clock = new AudioClock(48000)
    expect(() => clock.observe({ contextTime: NaN, performanceTime: 1000 }, 1000)).toThrow()
  })
  it('接受零contextTime并拒绝负值', () => {
    const clock = new AudioClock(48000)
    // contextTime === 0 是 AudioContext 刚启动时的合法初始值
    expect(() => clock.observe({ contextTime: 0, performanceTime: 1000 }, 1000)).not.toThrow()
    // 负值仍然被拒绝
    expect(() => clock.observe({ contextTime: -1, performanceTime: 1000 }, 1000)).toThrow()
  })
  it('时钟速率不匹配不抛错但延迟稳定', () => {
    const clock = new AudioClock(48000)
    clock.observe({ contextTime: 1.0, performanceTime: 1000 }, 1000)
    // context前进了100ms但performance只前进了1ms → 一致性失败，stamp仍存储但不推进稳定
    expect(() => clock.observe({ contextTime: 1.1, performanceTime: 1001 }, 1001)).not.toThrow()
    expect(clock.ready).toBe(false)
  })
  it('时间倒退不抛错但延迟稳定', () => {
    const clock = new AudioClock(48000)
    clock.observe({ contextTime: 1.0, performanceTime: 1000 }, 1000)
    expect(() => clock.observe({ contextTime: 0.9, performanceTime: 999 }, 999)).not.toThrow()
    expect(clock.ready).toBe(false)
  })
  it('frameAt映射正确', () => {
    const clock = new AudioClock(48000)
    clock.observe({ contextTime: 10.0, performanceTime: 5000 }, 5000)
    clock.observe({ contextTime: 10.35, performanceTime: 5350 }, 5350)
    // eventTime在performanceTime=5350，contextTime=10.35
    const frame = clock.frameAt(5350, 5350, 0)
    // contextTime=10.35, rate=48000 → frame=496800
    expect(frame).toBe(496800)
  })
  it('frameAt未稳定时拒绝', () => {
    const clock = new AudioClock(48000)
    clock.observe({ contextTime: 1.0, performanceTime: 1000 }, 1000)
    expect(() => clock.frameAt(1000, 1000, 0)).toThrow()
  })
  it('frameAt过期事件拒绝', () => {
    const clock = new AudioClock(48000)
    clock.observe({ contextTime: 10.0, performanceTime: 5000 }, 5000)
    clock.observe({ contextTime: 10.35, performanceTime: 5350 }, 5350)
    // 600ms前的事件（超过 500ms 阈值）
    expect(() => clock.frameAt(4750, 5350, 0)).toThrow()
  })
  it('frameAt处理大eventTime（绝对时间戳）', () => {
    const clock = new AudioClock(48000)
    const timeOrigin = 1700000000000
    clock.observe({ contextTime: 10.0, performanceTime: 5000 }, 5000)
    clock.observe({ contextTime: 10.35, performanceTime: 5350 }, 5350)
    // eventTime > 1e12 → 减去timeOrigin
    const eventTime = timeOrigin + 5350
    const frame = clock.frameAt(eventTime, 5350, timeOrigin)
    expect(frame).toBe(496800)
  })
})
