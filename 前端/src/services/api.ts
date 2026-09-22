import { bytes, integer, parseObject, parseStatus, ProtocolFault } from '../protocol/schema'
export class ApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string, readonly retryAfter = 0) { super(message) }
}
const messages: Record<number, string> = {
  400: '配对口令格式不正确，请重新输入。', 401: '需要重新配对；口令可能已失效或会话已到期。',
  403: '访问地址未获允许，请检查 HTTPS 地址与服务配置。', 409: '配对设备已满，请在电脑端撤销旧会话。',
  415: '请求格式不兼容，请检查前后端版本。', 429: '尝试过于频繁，请等待后重试。', 405: '服务接口不匹配，请检查部署版本。',
}
/** API只消费明确schema，不把200 HTML、JSON对象或服务端堆栈直接塞进页面。凭据仅由cookie管理。 */
export class LocalApi {
  constructor(private request: typeof fetch = (...args: Parameters<typeof fetch>) => fetch(...args)) {}
  private async call(path: string, body?: object): Promise<string> {
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 5000)
    try {
      const response = await this.request(path, { method: body ? 'POST' : 'GET', credentials: 'same-origin', cache: 'no-store', signal: controller.signal,
        ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) })
      const raw = await response.text()
      if (bytes(raw) > 16384) throw new ProtocolFault()
      if (!response.ok) {
        const value = parseObject(raw)
        if (typeof value.code !== 'string' || typeof value.message !== 'string' || bytes(value.message) > 256) throw new ProtocolFault()
        const header = Number(response.headers.get('Retry-After'))
        const retry = response.status === 429 && integer(header, 0, 86400) ? header : integer(value.retry_after_seconds, 0, 86400) ? Number(value.retry_after_seconds) : 0
        throw new ApiError(response.status, value.code, messages[response.status] ?? '本地服务暂时不可用，请稍后检查。', retry)
      }
      if (body) { if (response.status !== 204 || raw !== '') throw new ProtocolFault() }
      else if (response.status !== 200 || !response.headers.get('content-type')?.includes('application/json')) throw new ProtocolFault()
      return raw
    } finally { clearTimeout(timeout) }
  }
  async pair(code: string) { await this.call('/api/pair', { pairing_code: code.trim().toUpperCase() }) }
  async logout() { await this.call('/api/logout', {}) }
  async status() { return parseStatus(await this.call('/api/status')) }
}
