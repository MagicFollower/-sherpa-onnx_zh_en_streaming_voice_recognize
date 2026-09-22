import { afterEach, vi } from 'vitest'
import { TextDecoder, TextEncoder } from 'node:util'
Object.assign(globalThis, { TextEncoder, TextDecoder })
if (typeof globalThis.PointerEvent === 'undefined') {
  class PointerEvent extends MouseEvent {
    readonly pointerId: number; readonly pointerType: string; readonly isPrimary: boolean
    constructor(type: string, init: Record<string, unknown> = {}) {
      super(type, init)
      this.pointerId = init.pointerId as number ?? 0
      this.pointerType = init.pointerType as string ?? 'mouse'
      this.isPrimary = init.isPrimary as boolean ?? false
    }
  }
  Object.assign(globalThis, { PointerEvent })
}
Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', { value: vi.fn(), configurable: true })
afterEach(() => { document.body.innerHTML = ''; vi.useRealTimers(); vi.unstubAllGlobals() })
