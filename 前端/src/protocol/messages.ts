/* LASR 1.0唯一字段定义。网络输入必须先经过schema，不能以TypeScript断言代替运行时校验。 */
export const VERSION = '1.0' as const
export const AUDIO = { sample_rate: 16000, channels: 1, format: 'pcm_s16le', frame_samples: 320 } as const
export const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
export type CancelReason = 'user' | 'focus_lost' | 'page_hidden' | 'pointer_cancel' | 'capture_lost' | 'device_lost' | 'client_timeout' | 'client_overload'
export type Action = 'retry_new' | 'reconnect' | 'pair' | 'configure' | 'none'
export interface Base { type: string; protocol_version: '1.0'; session_id: string }
export interface Round extends Base { utterance_id: string }
export interface Hello extends Base { type: 'hello'; model_ready: boolean; config_id: string; max_active: 1; max_utterance_ms: 60000 }
export interface Ready extends Round { type: 'ready'; config_id: string; sample_rate: 16000; channels: 1; format: 'pcm_s16le'; frame_samples: 320; punctuation_enabled: false; refinement_enabled: false; itn_enabled: false }
export interface Heartbeat extends Base { type: 'heartbeat'; kind: 'ping' | 'pong'; nonce: string }
export interface Ack extends Round { type: 'ack'; received_seq: number; received_samples: number; processed_seq: number; processed_samples: number; stop_received: boolean }
export interface Partial extends Round { type: 'partial'; revision: number; text: string }
export interface Final extends Round { type: 'final'; revision: number; text: string; status: 'ok' | 'empty'; last_seq: number; total_samples: number; result_mode: 'streaming' | 'two_pass'; degraded: boolean; degradation_reason: 'none' | 'punctuation_timeout' | 'punctuation_failed' | 'refinement_budget' | 'refinement_timeout' | 'refinement_failed' | 'multiple'; punctuation_status: 'disabled' | 'applied' | 'timeout' | 'failed'; refinement_status: 'disabled' | 'applied' | 'skipped_budget' | 'timeout' | 'failed' }
export interface Cancelled extends Round { type: 'cancelled'; reason: CancelReason }
export interface ServerError extends Base { type: 'error'; utterance_id?: string; code: string; scope: 'request' | 'utterance' | 'connection'; terminal: boolean; retryable: boolean; action: Action; message: string }
export type ServerMessage = Hello | Ready | Heartbeat | Ack | Partial | Final | Cancelled | ServerError
export interface Status { protocol_version: '1.0'; config_id: string; model_ready: boolean; busy: boolean; max_active: 1; max_utterance_ms: 60000; connection_available: boolean }
