import { AUDIO, UUID, type ServerMessage, type Status } from './messages'
export class ProtocolFault extends Error { constructor(message = '连接协议不一致，请检查前后端版本。') { super(message); this.name = 'ProtocolFault' } }
const encoder = new TextEncoder()
export const bytes = (s: string) => encoder.encode(s).length
export function requireThat(ok: unknown): asserts ok { if (!ok) throw new ProtocolFault() }
export const integer = (v: unknown, min = 0, max = Number.MAX_SAFE_INTEGER) => typeof v === 'number' && Number.isSafeInteger(v) && v >= min && v <= max
const text = (v: unknown, max: number, min = 0) => typeof v === 'string' && bytes(v) <= max && bytes(v) >= min
const bool = (v: unknown) => typeof v === 'boolean'
const oneOf = (v: unknown, values: readonly unknown[]) => values.includes(v)
export const cancelReasons = ['user', 'focus_lost', 'page_hidden', 'pointer_cancel', 'capture_lost', 'device_lost', 'client_timeout', 'client_overload'] as const
function depth(value: unknown, level: number): void {
  if (typeof value !== 'object' || value === null) return
  requireThat(level <= 4)
  for (const child of Object.values(value)) depth(child, level + 1)
}
/** 先限制UTF-8字节再解析；根对象深度计1，未知可选字段仍占用同一预算。 */
export function parseObject(raw: string): Record<string, unknown> {
  requireThat(bytes(raw) <= 16384)
  let value: unknown
  try { value = JSON.parse(raw) } catch { throw new ProtocolFault() }
  requireThat(value && typeof value === 'object' && !Array.isArray(value))
  depth(value, 1)
  return value as Record<string, unknown>
}
function common(v: Record<string, unknown>) {
  requireThat(v.protocol_version === '1.0' && typeof v.session_id === 'string' && UUID.test(v.session_id))
}
function config(v: Record<string, unknown>) { requireThat(text(v.config_id, 128, 1)) }
function limits(v: Record<string, unknown>) { requireThat(v.max_active === 1 && v.max_utterance_ms === 60000) }
function counts(v: Record<string, unknown>, seq: string, samples: string) {
  requireThat(integer(v[seq], -1, 2999) && integer(v[samples], 0, 960000))
  requireThat((v[seq] === -1) === (v[samples] === 0))
}
export function parseStatus(raw: string): Status {
  const v = parseObject(raw)
  requireThat(v.protocol_version === '1.0')
  config(v); limits(v)
  requireThat(bool(v.model_ready) && bool(v.busy) && bool(v.connection_available))
  return v as unknown as Status
}
export function parseServer(raw: string): ServerMessage {
  const v = parseObject(raw)
  common(v)
  if (!['hello', 'heartbeat', 'error'].includes(String(v.type))) requireThat(typeof v.utterance_id === 'string' && UUID.test(v.utterance_id))
  switch (v.type) {
    case 'hello': config(v); limits(v); requireThat(bool(v.model_ready)); break
    case 'ready':
      config(v)
      for (const [key, value] of Object.entries(AUDIO)) requireThat(v[key] === value)
      requireThat(v.punctuation_enabled === false && v.refinement_enabled === false && v.itn_enabled === false)
      break
    case 'heartbeat': requireThat(oneOf(v.kind, ['ping', 'pong']) && typeof v.nonce === 'string' && v.nonce.length <= 32); break
    case 'ack':
      counts(v, 'received_seq', 'received_samples'); counts(v, 'processed_seq', 'processed_samples')
      requireThat(bool(v.stop_received) && Number(v.processed_seq) <= Number(v.received_seq) && Number(v.processed_samples) <= Number(v.received_samples))
      break
    case 'partial': requireThat(integer(v.revision, 1) && text(v.text, 8192)); break
    case 'final':
      requireThat(integer(v.revision, 1) && text(v.text, 8192) && oneOf(v.status, ['ok', 'empty']))
      requireThat((v.status === 'empty') === (v.text === ''))
      counts(v, 'last_seq', 'total_samples')
      requireThat(oneOf(v.result_mode, ['streaming', 'two_pass']) && bool(v.degraded))
      requireThat(oneOf(v.degradation_reason, ['none', 'punctuation_timeout', 'punctuation_failed', 'refinement_budget', 'refinement_timeout', 'refinement_failed', 'multiple']))
      requireThat(oneOf(v.punctuation_status, ['disabled', 'applied', 'timeout', 'failed']))
      requireThat(oneOf(v.refinement_status, ['disabled', 'applied', 'skipped_budget', 'timeout', 'failed']))
      // 本版ready明确关闭全部增强。不能接受服务端悄悄改模式或把关闭误称降级。
      requireThat(v.result_mode === 'streaming' && v.degraded === false && v.degradation_reason === 'none' && v.punctuation_status === 'disabled' && v.refinement_status === 'disabled')
      break
    case 'cancelled': requireThat(oneOf(v.reason, cancelReasons)); break
    case 'error':
      requireThat(text(v.code, 64, 1) && text(v.message, 256) && oneOf(v.scope, ['request', 'utterance', 'connection']))
      requireThat(bool(v.terminal) && bool(v.retryable) && oneOf(v.action, ['retry_new', 'reconnect', 'pair', 'configure', 'none']))
      requireThat(v.retryable === (v.action === 'retry_new' || v.action === 'reconnect'))
      if (v.utterance_id !== undefined || v.scope === 'utterance') requireThat(typeof v.utterance_id === 'string' && UUID.test(v.utterance_id))
      requireThat(!v.terminal || v.scope === 'utterance')
      break
    default: throw new ProtocolFault()
  }
  return v as unknown as ServerMessage
}
