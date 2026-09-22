import type { Ack, Final, Ready } from '../src/protocol/messages'
import { AUDIO } from '../src/protocol/messages'
export const ID = '00112233-4455-4677-8899-aabbccddeeff'
export const SESSION = '10112233-4455-4677-8899-aabbccddeeff'
export const base = { protocol_version: '1.0' as const, session_id: SESSION, utterance_id: ID }
export const ready = (): Ready => ({ ...base, type: 'ready', ...AUDIO, config_id: 'test-config', punctuation_enabled: false, refinement_enabled: false, itn_enabled: false })
export const ack = (fields: Partial<Ack> = {}): Ack => ({ ...base, type: 'ack', received_seq: -1, received_samples: 0, processed_seq: -1, processed_samples: 0, stop_received: false, ...fields })
export const final = (fields: Partial<Final> = {}): Final => ({ ...base, type: 'final', revision: 1, text: '', status: 'empty', last_seq: -1, total_samples: 0, result_mode: 'streaming', degraded: false, degradation_reason: 'none', punctuation_status: 'disabled', refinement_status: 'disabled', ...fields })
export function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
