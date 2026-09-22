import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { LasrConnection, socketUrl } from '../../src/services/connection'
import { SESSION } from '../fixtures'

class FakeWebSocket {
  url: string; protocol: string; binaryType = 'arraybuffer'
  readyState = 1
  onmessage: ((e: { data: string | ArrayBuffer }) => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  sent: (string | ArrayBuffer)[] = []
  closed = false
  constructor(url: string, protocol: string) { this.url = url; this.protocol = protocol }
  send(data: string | ArrayBuffer) { this.sent.push(data) }
  close(code?: number) { this.closed = true; this.readyState = 3; this.onclose?.() }
  // 测试辅助
  receive(data: string | ArrayBuffer) { this.onmessage?.({ data }) }
  triggerError() { this.onerror?.() }
}

function createConnection(events?: { message?: (m: any) => void; closed?: (r: string) => void }) {
  const sockets: FakeWebSocket[] = []
  const factory = (url: string, protocol: string) => {
    const ws = new FakeWebSocket(url, protocol)
    sockets.push(ws)
    return ws as unknown as WebSocket
  }
  const conn = new LasrConnection(
    { message: events?.message ?? vi.fn(), closed: events?.closed ?? vi.fn() },
    factory
  )
  return { conn, sockets }
}

function sendHello(ws: FakeWebSocket, sessionId = SESSION) {
  ws.receive(JSON.stringify({
    type: 'hello', protocol_version: '1.0', session_id: sessionId,
    model_ready: true, config_id: 'test', max_active: 1, max_utterance_ms: 60000
  }))
}

describe('socketUrl', () => {
  it('https生成wss', () => {
    expect(socketUrl({ protocol: 'https:', hostname: 'localhost', host: 'localhost:8765' }))
      .toBe('wss://localhost:8765/ws')
  })
  it('http localhost生成ws', () => {
    expect(socketUrl({ protocol: 'http:', hostname: 'localhost', host: 'localhost:8765' }))
      .toBe('ws://localhost:8765/ws')
  })
  it('http 127.0.0.1生成ws', () => {
    expect(socketUrl({ protocol: 'http:', hostname: '127.0.0.1', host: '127.0.0.1:8765' }))
      .toBe('ws://127.0.0.1:8765/ws')
  })
  it('http非localhost抛错', () => {
    expect(() => socketUrl({ protocol: 'http:', hostname: '192.168.1.1', host: '192.168.1.1:8765' }))
      .toThrow()
  })
})

describe('LasrConnection', () => {
  it('连接时发送WebSocket请求', () => {
    const { sockets } = createConnection()
    expect(sockets.length).toBe(1)
    expect(sockets[0]!.url).toContain('/ws')
  })
  it('收到hello后设置session', () => {
    const { conn, sockets } = createConnection()
    sendHello(sockets[0]!)
    expect(conn.session).toBe(SESSION)
  })
  it('hello超时关闭', async () => {
    vi.useFakeTimers()
    const closed = vi.fn()
    const { sockets } = createConnection({ closed })
    vi.advanceTimersByTime(5100)
    expect(closed).toHaveBeenCalled()
    vi.useRealTimers()
  })
  it('拒绝非hello首条消息', () => {
    const closed = vi.fn()
    const { sockets } = createConnection({ closed })
    sockets[0]!.receive(JSON.stringify({
      type: 'partial', protocol_version: '1.0', session_id: SESSION,
      utterance_id: '00112233-4455-4677-8899-aabbccddeeff', revision: 1, text: 'x'
    }))
    expect(closed).toHaveBeenCalled()
  })
  it('心跳ping自动回复pong', () => {
    const { conn, sockets } = createConnection()
    sendHello(sockets[0]!)
    sockets[0]!.receive(JSON.stringify({
      type: 'heartbeat', protocol_version: '1.0', session_id: SESSION,
      kind: 'ping', nonce: 'test123'
    }))
    const lastSent = sockets[0]!.sent.filter(s => typeof s === 'string')
    const pong = JSON.parse(lastSent[lastSent.length - 1] as string)
    expect(pong.type).toBe('heartbeat')
    expect(pong.kind).toBe('pong')
    expect(pong.nonce).toBe('test123')
  })
  it('session不匹配关闭', () => {
    const closed = vi.fn()
    const { sockets } = createConnection({ closed })
    sendHello(sockets[0]!)
    sockets[0]!.receive(JSON.stringify({
      type: 'heartbeat', protocol_version: '1.0', session_id: 'wrong-session-id-0000-000000000000',
      kind: 'ping', nonce: 'x'
    }))
    expect(closed).toHaveBeenCalled()
  })
  it('close清理资源', () => {
    const { conn, sockets } = createConnection()
    sendHello(sockets[0]!)
    conn.close()
    expect(sockets[0]!.closed).toBe(true)
  })
  it('重复close幂等', () => {
    const { conn, sockets } = createConnection()
    sendHello(sockets[0]!)
    conn.close()
    conn.close()
    expect(sockets[0]!.closed).toBe(true)
  })
  it('send在close后抛错', () => {
    const { conn, sockets } = createConnection()
    sendHello(sockets[0]!)
    conn.close()
    expect(() => conn.send({ type: 'heartbeat', kind: 'ping', nonce: 'x' })).toThrow()
  })
  it('消息转发到事件处理器', () => {
    const messages: any[] = []
    const { sockets } = createConnection({ message: m => messages.push(m) })
    sendHello(sockets[0]!)
    sockets[0]!.receive(JSON.stringify({
      type: 'partial', protocol_version: '1.0', session_id: SESSION,
      utterance_id: '00112233-4455-4677-8899-aabbccddeeff', revision: 1, text: '草稿'
    }))
    // hello也会穿透到message事件，验证最后一条是partial
    const partials = messages.filter(m => m.type === 'partial')
    expect(partials.length).toBe(1)
    expect(partials[0].type).toBe('partial')
    expect(partials[0].text).toBe('草稿')
  })
  it('WebSocket错误触发关闭', () => {
    const closed = vi.fn()
    const { sockets } = createConnection({ closed })
    sockets[0]!.triggerError()
    expect(closed).toHaveBeenCalled()
  })
  it('send序列化包含protocol_version和session_id', () => {
    const { conn, sockets } = createConnection()
    sendHello(sockets[0]!)
    conn.send({ type: 'heartbeat', kind: 'ping', nonce: 'x' })
    const lastSent = sockets[0]!.sent.filter(s => typeof s === 'string')
    const parsed = JSON.parse(lastSent[lastSent.length - 1] as string)
    expect(parsed.protocol_version).toBe('1.0')
    expect(parsed.session_id).toBe(SESSION)
  })
})
