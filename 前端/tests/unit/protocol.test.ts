import { describe, expect, it } from 'vitest'
import { AudioLedger, encodeAudio, quantize } from '../../src/protocol/audio'
import { bytes, parseObject, parseServer, parseStatus } from '../../src/protocol/schema'
import { ID, SESSION, ack, base, final, ready } from '../fixtures'
describe('LASR运行时schema', () => {
  it('完整消息与未知可选字段', () => expect(parseServer(JSON.stringify({ ...ready(), optional: 1 }))).toMatchObject(ready()))
  it.each(['[]', 'null', '"text"', '{', '1'])('拒绝非法根 %s', raw => expect(() => parseObject(raw)).toThrow())
  it('限制深度而非仅已知字段', () => {
    expect(() => parseObject('{"a":{"b":{"c":{}}}}')).not.toThrow()
    expect(() => parseObject('{"a":{"b":{"c":{"d":{}}}}}')).toThrow()
  })
  it('最大16KiB按UTF8计数', () => expect(() => parseObject(JSON.stringify({ value: '汉'.repeat(5500) }))).toThrow())
  it.each([{ protocol_version: '2.0' }, { session_id: ID.toUpperCase() }, { utterance_id: '00000000-0000-0000-0000-000000000000' }, { type: 'unknown' }, { sample_rate: 48000 }, { punctuation_enabled: true }, { config_id: '' }, { config_id: '汉'.repeat(43) }])('拒绝不合法字段 %j', patch => expect(() => parseServer(JSON.stringify({ ...ready(), ...patch }))).toThrow())
  it('正文8192字节精确边界', () => {
    const text = '汉'.repeat(2730) + 'ab'
    expect(bytes(text)).toBe(8192)
    expect(() => parseServer(JSON.stringify({ ...base, type: 'partial', revision: 1, text }))).not.toThrow()
    expect(() => parseServer(JSON.stringify({ ...base, type: 'partial', revision: 1, text: text + 'x' }))).toThrow()
  })
  it.each([0, -1, 1.1, Number.MAX_SAFE_INTEGER + 1, null])('revision正安全整数 %s', revision => expect(() => parseServer(JSON.stringify({ ...base, type: 'partial', revision, text: 'x' }))).toThrow())
  it.each([{ status: 'ok' }, { text: '不空' }, { result_mode: 'invalid' }, { result_mode: 'two_pass' }, { degraded: true }, { degradation_reason: 'other' }, { punctuation_status: 'applied' }, { refinement_status: 'failed' }])('final一致性 %j', patch => expect(() => parseServer(JSON.stringify({ ...final(), ...patch }))).toThrow())
  it('错误retryable和action不能矛盾', () => {
    const message = { ...base, type: 'error', code: 'BUSY', scope: 'request', terminal: false, retryable: true, action: 'retry_new', message: '忙碌' }
    expect(() => parseServer(JSON.stringify(message))).not.toThrow()
    expect(() => parseServer(JSON.stringify({ ...message, retryable: false }))).toThrow()
    expect(() => parseServer(JSON.stringify({ ...message, message: '汉'.repeat(86) }))).toThrow()
  })
  it('heartbeat nonce边界', () => {
    expect(() => parseServer(JSON.stringify({ ...base, type: 'heartbeat', kind: 'ping', nonce: 'a'.repeat(32) }))).not.toThrow()
    expect(() => parseServer(JSON.stringify({ ...base, type: 'heartbeat', kind: 'ping', nonce: 'a'.repeat(33) }))).toThrow()
  })
  it('HTTP status完整必填项', () => {
    const status = { protocol_version: '1.0', config_id: 'frozen', model_ready: true, busy: false, max_active: 1, max_utterance_ms: 60000, connection_available: true }
    expect(parseStatus(JSON.stringify(status))).toEqual(status)
    expect(() => parseStatus(JSON.stringify({ ...status, connection_available: undefined }))).toThrow()
  })
  it('hello允许模型未就绪但会话必须UUIDv4', () => expect(parseServer(JSON.stringify({ type: 'hello', protocol_version: '1.0', session_id: SESSION, model_ready: false, config_id: 'c', max_active: 1, max_utterance_ms: 60000 })).type).toBe('hello'))
})
describe('PCM黄金向量', () => {
  it('32字节头、规范UUID、seq1和正负1000严格小端', () => {
    const bytes = Array.from(new Uint8Array(encodeAudio(ID, 1, new Int16Array([1000, -1000]))))
    expect(bytes).toEqual([76,65,83,82,1,0,32,0,0,17,34,51,68,85,70,119,136,153,170,187,204,221,238,255,1,0,0,0,2,0,0,0,232,3,24,252])
  })
  it.each([[-2,-32768],[-1,-32768],[-.5,-16384],[0,0],[.5,16383],[1,32767],[2,32767]])('量化%s', (input, output) => expect(quantize(input!)).toBe(output))
  it.each([NaN, Infinity, -Infinity])('拒绝非有限值', value => expect(() => quantize(value)).toThrow())
  it.each([0,321])('非法帧长%s', size => expect(() => encodeAudio(ID, 0, new Int16Array(size))).toThrow())
  it('正常满帧672字节', () => expect(encodeAudio(ID, 0, new Int16Array(320)).byteLength).toBe(672))
})
describe('网络计数和最终ACK', () => {
  it('零帧必须最终ACK先于final', () => {
    const ledger = new AudioLedger(); ledger.stop()
    expect(() => ledger.final(final())).toThrow()
    ledger.accept(ack({ stop_received: true })); expect(() => ledger.final(final())).not.toThrow()
  })
  it('最后短帧和完整processed水位', () => {
    const ledger = new AudioLedger(); ledger.push(320); ledger.push(160); ledger.stop()
    ledger.accept(ack({ received_seq: 1, received_samples: 480, processed_seq: 0, processed_samples: 320, stop_received: true }))
    const result = final({ last_seq: 1, total_samples: 480 })
    expect(() => ledger.final(result)).toThrow()
    ledger.accept(ack({ received_seq: 1, received_samples: 480, processed_seq: 1, processed_samples: 480, stop_received: true }))
    expect(() => ledger.final(result)).not.toThrow()
    expect(() => ledger.final({ ...result, total_samples: 481 })).toThrow()
  })
  it('短帧后禁止新帧', () => { const ledger = new AudioLedger(); ledger.push(1); expect(() => ledger.push(320)).toThrow() })
  it('stop后禁止新帧', () => { const ledger = new AudioLedger(); ledger.stop(); expect(() => ledger.push(320)).toThrow() })
  it.each([{ received_seq: 0, received_samples: 319 }, { processed_seq: 0, processed_samples: 160 }, { received_seq: 1, received_samples: 640 }, { stop_received: true }])('拒绝错水位 %j', patch => {
    const ledger = new AudioLedger(); ledger.push(320)
    expect(() => ledger.accept(ack(patch))).toThrow()
  })
  it('ACK不可倒退或撤销stop', () => {
    const ledger = new AudioLedger(); ledger.push(320); ledger.stop()
    ledger.accept(ack({ received_seq: 0, received_samples: 320, processed_seq: 0, processed_samples: 320, stop_received: true }))
    expect(() => ledger.accept(ack())).toThrow()
  })
  it('60秒及3000帧硬限制', () => {
    const ledger = new AudioLedger(); for (let i = 0; i < 3000; i++) ledger.push(320)
    expect(ledger.total).toBe(960000); expect(() => ledger.push(1)).toThrow()
  })
})
