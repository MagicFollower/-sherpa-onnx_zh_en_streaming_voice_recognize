import { parseServer, parseObject, requireThat } from '../protocol/schema'
import type { ServerMessage } from '../protocol/messages'
export interface Transport {
  session: string
  readonly bufferedAmount: number
  send(fields: Record<string, unknown> | ArrayBuffer): void
  close(): void
}
export interface ConnectionEvents { message: (message: ServerMessage) => void; closed: (reason: string) => void }
export function socketUrl(location: Pick<Location, 'protocol' | 'hostname' | 'host'>) {
  if (location.protocol === 'https:') return `wss://${location.host}/ws`
  if (location.protocol === 'http:' && ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return `ws://${location.host}/ws`
  throw new Error('局域网录音必须使用可信 HTTPS。')
}
/** 连接只管理会话与保活，不持有音频重传队列；每次重连都是新session。 */
export class LasrConnection implements Transport {
  session = ''
  private ws: WebSocket
  private helloTimer: ReturnType<typeof setTimeout>
  private heartbeat?: ReturnType<typeof setInterval>
  private lastValid = performance.now()
  private lastPing = 0
  private done = false
  get bufferedAmount() { return this.ws.bufferedAmount }
  constructor(private events: ConnectionEvents, factory: (url: string, protocol: string) => WebSocket = (url, protocol) => new WebSocket(url, protocol)) {
    this.ws = factory(socketUrl(window.location), 'lasr.v1')
    this.ws.binaryType = 'arraybuffer'
    this.helloTimer = setTimeout(() => this.fail('等待连接问候超时，请重连。'), 5000)
    this.ws.onmessage = event => {
      try {
        requireThat(typeof event.data === 'string')
        const message = parseServer(event.data)
        if (!this.session) {
          requireThat(message.type === 'hello' && this.ws.protocol === 'lasr.v1')
          this.session = message.session_id; clearTimeout(this.helloTimer)
          this.heartbeat = setInterval(() => this.tick(), 1000)
        } else requireThat(message.type !== 'hello' && message.session_id === this.session)
        this.lastValid = performance.now()
        if (message.type === 'heartbeat') {
          if (message.kind === 'ping') this.send({ type: 'heartbeat', kind: 'pong', nonce: message.nonce })
        } else this.events.message(message)
      } catch { this.fail('连接协议异常，已停止本轮，请检查版本后重连。') }
    }
    this.ws.onclose = () => this.fail('本地连接已断开，未完成的录音不会续传。')
    // 浏览器不开放握手HTTP正文；上层收到关闭后以/api/status区分认证与连接配额。
    this.ws.onerror = () => this.fail('无法建立本地连接，请检查服务、证书或其他已打开页面。')
  }
  send(fields: Record<string, unknown> | ArrayBuffer) {
    requireThat(!this.done && this.ws.readyState === 1 && this.session)
    if (fields instanceof ArrayBuffer) this.ws.send(fields)
    else {
      const raw = JSON.stringify({ ...fields, protocol_version: '1.0', session_id: this.session })
      parseObject(raw)
      this.ws.send(raw)
    }
  }
  close() {
    if (this.done) return
    this.done = true; clearTimeout(this.helloTimer); clearInterval(this.heartbeat)
    this.ws.onmessage = null; this.ws.onclose = null; this.ws.onerror = null
    this.ws.close(1000)
  }
  private fail(reason: string) { if (!this.done) { this.close(); this.events.closed(reason) } }
  private tick() {
    const now = performance.now()
    if (now - this.lastValid >= 30000) { this.fail('连接心跳超时，请恢复网络后重连。'); return }
    if (now - this.lastPing >= 10000) {
      this.lastPing = now
      try { this.send({ type: 'heartbeat', kind: 'ping', nonce: crypto.randomUUID().replaceAll('-', '') }) } catch { this.fail('连接已失效。') }
    }
  }
}
