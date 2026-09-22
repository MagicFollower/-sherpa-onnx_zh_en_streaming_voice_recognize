import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RoundManager, newRoundView, type Card } from '../../src/state/rounds'
import type { AudioCapture, CaptureCallbacks, DrainResult } from '../../src/audio/capture'
import type { Transport } from '../../src/services/connection'
import type { ServerMessage } from '../../src/protocol/messages'
import { AUDIO } from '../../src/protocol/messages'
import { ID, SESSION, ack, final, ready } from '../fixtures'

class FakeCapture implements AudioCapture {
  prepared = false; started = false; stopped = false; disposed = false
  callbacks?: CaptureCallbacks
  rate = 48000
  async prepare() { this.prepared = true; return this.rate }
  start(callbacks: CaptureCallbacks) { this.started = true; this.callbacks = callbacks }
  async stop(): Promise<DrainResult> {
    this.stopped = true
    return { total: 0, sourceTotal: 0, durationLimit: false }
  }
  acknowledge(processed: number) {}
  dispose() { this.disposed = true }
}

class FakeTransport implements Transport {
  session = SESSION
  bufferedAmount = 0
  sent: Record<string, unknown>[] = []
  closed = false
  send(fields: Record<string, unknown> | ArrayBuffer) {
    if (fields instanceof ArrayBuffer) return
    this.sent.push(fields)
  }
  close() { this.closed = true }
}

function createManager(overrides: Partial<{ capture: () => AudioCapture; canStart: () => boolean; configId: () => string; fault: (m: string) => void; uuid: () => string }> = {}) {
  const view = newRoundView()
  const capture = new FakeCapture()
  const manager = new RoundManager(view, {
    capture: () => capture,
    canStart: overrides.canStart ?? (() => true),
    configId: overrides.configId ?? (() => 'test-config'),
    fault: overrides.fault ?? vi.fn(),
    uuid: () => ID,
  })
  const transport = new FakeTransport()
  manager.transport = transport
  return { manager, view, capture, transport }
}

function serverMessage(msg: Partial<ServerMessage> & { type: string }): ServerMessage {
  return { protocol_version: '1.0', session_id: SESSION, utterance_id: ID, ...msg } as unknown as ServerMessage
}

describe('RoundManager', () => {
  it('begin创建卡片并进入preparing', () => {
    const { manager, view, capture } = createManager()
    expect(manager.begin('pointer')).toBe(true)
    expect(view.cards.length).toBe(1)
    expect(view.cards[0]!.phase).toBe('preparing')
    expect(view.activeId).toBe(ID)
  })
  it('活动时拒绝新轮次', () => {
    const { manager } = createManager()
    manager.begin('pointer')
    expect(manager.begin('pointer')).toBe(false)
  })
  it('canStart返回false时拒绝', () => {
    const { manager } = createManager({ canStart: () => false })
    expect(manager.begin('pointer')).toBe(false)
  })
  it('prepare成功后发送start', async () => {
    const { manager, capture, transport } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    expect(transport.sent.length).toBe(1)
    expect(transport.sent[0]!.type).toBe('start')
  })
  it('收到ready后进入recording', async () => {
    const { manager, capture, transport } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    expect(manager.view.cards[0]!.phase).toBe('recording')
    expect(capture.started).toBe(true)
  })
  it('收到partial更新正文', async () => {
    const { manager, capture } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    manager.message(serverMessage({ type: 'partial', revision: 1, text: '测试草稿' }))
    expect(manager.view.cards[0]!.text).toBe('测试草稿')
    expect(manager.view.cards[0]!.revision).toBe(1)
  })
  it('收到final完成轮次', async () => {
    const { manager, capture, view } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    // 完整流程: release → stopped → ack(stop) → final
    await manager.release(1000)
    manager.message(serverMessage(ack({ stop_received: true })))
    manager.message(serverMessage(final({ text: '终稿', status: 'ok', revision: 1 })))
    expect(view.cards[0]!.phase).toBe('completed')
    expect(view.cards[0]!.text).toBe('终稿')
    expect(view.activeId).toBe('')
  })
  it('cancel取消轮次', async () => {
    const { manager, capture, view } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    manager.cancel('user')
    expect(view.cards[0]!.discarded).toBe(true)
    expect(view.activeId).toBe('')
  })
  it('准备期间松手取消', async () => {
    const { manager, view } = createManager()
    manager.begin('pointer')
    // 不等prepare完成，直接release
    await manager.release(1000)
    expect(view.cards[0]!.phase).toBe('cancelled')
  })
  it('超过50张卡移除旧的', () => {
    const { manager, view } = createManager()
    for (let i = 0; i < 51; i++) {
      manager.begin('pointer')
      manager.cancel('user')
    }
    expect(view.cards.length).toBeLessThanOrEqual(50)
  })
  it('clear在无活动时清空', () => {
    const { manager, view } = createManager()
    manager.begin('pointer')
    manager.cancel('user')
    manager.clear()
    expect(view.cards.length).toBe(0)
  })
  it('clear在有活动时不清空', async () => {
    const { manager, view, capture } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    manager.clear()
    expect(view.cards.length).toBe(1)
  })
  it('destroy清空所有', async () => {
    const { manager, view, capture } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.destroy()
    expect(view.cards.length).toBe(0)
    expect(view.activeId).toBe('')
  })
  it('错误终态标记failed', async () => {
    const { manager, capture, view } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage({ type: 'error', code: 'BUSY', scope: 'utterance', terminal: true, retryable: true, action: 'retry_new', message: '忙碌' }))
    expect(view.cards[0]!.phase).toBe('failed')
  })
  it('disconnect标记活动轮failed', async () => {
    const { manager, view, capture } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    manager.disconnect('连接断开')
    expect(view.cards[0]!.phase).toBe('failed')
  })
  it('revision不递减', async () => {
    const { manager, capture } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    // 首条partial必须revision=1（代码要求 card.revision===0 时 message.revision===1）
    manager.message(serverMessage({ type: 'partial', revision: 1, text: '第一版' }))
    manager.message(serverMessage({ type: 'partial', revision: 2, text: '第二版' }))
    manager.message(serverMessage({ type: 'partial', revision: 1, text: '旧版' }))
    expect(manager.view.cards[0]!.text).toBe('第二版')
    manager.cancel('user')
  })
  it('空final标记empty', async () => {
    const { manager, capture, view } = createManager()
    manager.begin('pointer')
    await vi.waitFor(() => expect(capture.prepared).toBe(true))
    manager.message(serverMessage(ready()))
    // 完整流程: release → stopped → ack(stop) → final(empty)
    await manager.release(1000)
    manager.message(serverMessage(ack({ stop_received: true })))
    manager.message(serverMessage(final({ text: '', status: 'empty', revision: 1 })))
    expect(view.cards[0]!.phase).toBe('completed')
    expect(view.cards[0]!.text).toBe('')
  })
})
