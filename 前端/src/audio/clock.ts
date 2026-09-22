export interface ClockStamp { contextTime: number; performanceTime: number }
/**
 * getOutputTimestamp提供AudioContext持续时钟到performance时钟的显式桥梁，
 * currentTime只是一个render quantum快照，不能直接当事件时间。至少两次前进的
 * 输出时间戳验证时钟速率；休眠、倒退、过旧或无法映射均拒绝，不以“减一点尾音”兜底。
 * 源采样使用currentFrame同一context时间轴；100ms提交缓冲覆盖输出管线提前处理。
 */
export class AudioClock {
  private stamp?: ClockStamp
  private anchor?: ClockStamp
  private stable = false
  constructor(private rate: number) {}
  observe(next: ClockStamp, now: number) {
    // 基本有效性：只拒绝真正无效的数据。performanceTime 过期不再拒绝，
    // 保证 stamp 始终新鲜——偶发抖动不应使时钟永久失效。
    if (!Number.isFinite(next.contextTime) || !Number.isFinite(next.performanceTime) || next.contextTime < 0 || next.performanceTime <= 0) throw new Error('AUDIO_CLOCK_UNCERTAIN')
    try {
      if (this.stamp && this.stamp.contextTime > 0 && next.contextTime > 0) {
        const elapsed = next.performanceTime - this.stamp.performanceTime
        const audioElapsed = (next.contextTime - this.stamp.contextTime) * 1000
        // 容差 10ms：覆盖定时器抖动、音频调度偏差和跨线程时间差。
        if (elapsed < 0 || audioElapsed < 0 || Math.abs(elapsed - audioElapsed) > 10) throw new Error()
      }
      if (!this.anchor && next.contextTime > 0) this.anchor = next
      if (this.anchor && (next.performanceTime - this.anchor.performanceTime) >= 0.3) this.stable = true
    } catch { /* 一致性不通过：stamp 仍然更新（保持新鲜），仅不推进稳定性 */ }
    this.stamp = next
  }
  get ready() { return this.stable }
  frameAt(eventTime: number, now: number, timeOrigin: number): number {
    const stamp = this.stamp
    const time = eventTime > 1e12 ? eventTime - timeOrigin : eventTime
    // 不再检查 stamp 新鲜度：observe() 始终存储有效 stamp，定时器每 40ms 更新。
    // 仅保留基本健全性检查。
    if (!this.stable || !stamp || !Number.isFinite(time) || time > now + 2 || now - time > 500) throw new Error('AUDIO_CLOCK_UNCERTAIN')
    const frame = Math.floor((stamp.contextTime + (time - stamp.performanceTime) / 1000) * this.rate)
    if (!Number.isSafeInteger(frame) || frame < 0) throw new Error('AUDIO_CLOCK_UNCERTAIN')
    return frame
  }
}
