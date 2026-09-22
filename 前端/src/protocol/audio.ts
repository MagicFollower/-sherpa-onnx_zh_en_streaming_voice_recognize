import { UUID, type Ack, type Final } from './messages'
import { integer, requireThat } from './schema'
/** 明确采用向零截断；负满幅与正满幅不对称，不能用Math.round或平台端序。 */
export function quantize(value: number): number {
  requireThat(Number.isFinite(value))
  const x = Math.max(-1, Math.min(1, value))
  return Math.trunc(x * (x < 0 ? 32768 : 32767))
}
export function encodeAudio(id: string, seq: number, samples: Int16Array): ArrayBuffer {
  requireThat(UUID.test(id) && integer(seq, 0, 2999) && integer(samples.length, 1, 320))
  const buffer = new ArrayBuffer(32 + samples.length * 2)
  const view = new DataView(buffer)
  new Uint8Array(buffer, 0, 4).set([0x4c, 0x41, 0x53, 0x52])
  view.setUint8(4, 1); view.setUint16(6, 32, true)
  const hex = id.replaceAll('-', '')
  // UUID按规范文本逐字节解码，特别不能采用Windows GUID的mixed-endian字段布局。
  for (let i = 0; i < 16; i++) view.setUint8(8 + i, Number.parseInt(hex.slice(i * 2, i * 2 + 2), 16))
  view.setUint32(24, seq, true); view.setUint16(28, samples.length, true)
  samples.forEach((sample, i) => view.setInt16(32 + 2 * i, sample, true))
  return buffer
}
/** 只保存最多3000个帧末水位，不保存正文或PCM。ACK必须命中完整网络帧末。 */
export class AudioLedger {
  private ends: number[] = []
  private short = false
  stopped = false
  received = 0
  processed = 0
  receivedSeq = -1
  processedSeq = -1
  stopReceived = false
  get total() { return this.ends.at(-1) ?? 0 }
  get lastSeq() { return this.ends.length - 1 }
  push(samples: number) {
    requireThat(!this.short && !this.stopped && integer(samples, 1, 320) && this.ends.length < 3000 && this.total + samples <= 960000)
    this.ends.push(this.total + samples); this.short = samples < 320
  }
  stop() { requireThat(!this.stopped); this.stopped = true }
  accept(ack: Ack) {
    const at = (seq: number) => seq === -1 ? 0 : this.ends[seq]
    requireThat(at(ack.received_seq) === ack.received_samples && at(ack.processed_seq) === ack.processed_samples)
    requireThat(ack.received_seq >= this.receivedSeq && ack.processed_seq >= this.processedSeq && ack.processed_seq <= ack.received_seq)
    requireThat(!this.stopReceived || ack.stop_received)
    if (ack.stop_received) requireThat(this.stopped && ack.received_seq === this.lastSeq && ack.received_samples === this.total)
    this.received = ack.received_samples; this.processed = ack.processed_samples
    this.receivedSeq = ack.received_seq; this.processedSeq = ack.processed_seq; this.stopReceived = ack.stop_received
  }
  final(final: Final) {
    requireThat(this.stopped && this.stopReceived && this.processed === this.total && this.processedSeq === this.lastSeq)
    requireThat(final.last_seq === this.lastSeq && final.total_samples === this.total)
  }
}
