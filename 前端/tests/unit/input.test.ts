import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HoldInput, type InputActions } from '../../src/state/input'

function createButton() {
  const btn = document.createElement('button')
  btn.setPointerCapture = vi.fn()
  btn.releasePointerCapture = vi.fn()
  btn.hasPointerCapture = vi.fn(() => true)
  document.body.appendChild(btn)
  return btn
}

function createActions(): InputActions & { calls: string[] } {
  const calls: string[] = []
  return {
    calls,
    begin: vi.fn((source: 'pointer' | 'keyboard') => { calls.push(`begin:${source}`); return true }),
    release: vi.fn((time: number) => { calls.push(`release:${time}`) }),
    cancel: vi.fn((reason: string) => { calls.push(`cancel:${reason}`) }),
  }
}

describe('HoldInput', () => {
  let button: HTMLButtonElement
  let actions: ReturnType<typeof createActions>
  let input: HoldInput

  beforeEach(() => {
    button = createButton()
    actions = createActions()
    input = new HoldInput(button, actions)
    input.attach()
  })
  afterEach(() => { input.dispose() })

  it('pointerdown开始pointer轮次', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 1 }))
    expect(actions.begin).toHaveBeenCalledWith('pointer')
  })
  it('pointerup释放', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 1 }))
    button.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, timeStamp: 1234 }))
    expect(actions.release).toHaveBeenCalledWith(expect.any(Number))
  })
  it('非主指针忽略', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: false, button: 0, pointerId: 2 }))
    expect(actions.begin).not.toHaveBeenCalled()
  })
  it('右键忽略', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 2, pointerId: 1 }))
    expect(actions.begin).not.toHaveBeenCalled()
  })
  it('空格键开始keyboard轮次', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false }))
    expect(actions.begin).toHaveBeenCalledWith('keyboard')
  })
  it('空格keyup释放', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false }))
    window.dispatchEvent(new KeyboardEvent('keyup', { code: 'Space', timeStamp: 5678 }))
    expect(actions.release).toHaveBeenCalledWith(expect.any(Number))
  })
  it('repeat空格忽略', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: true }))
    expect(actions.begin).not.toHaveBeenCalled()
  })
  it('Escape取消', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false }))
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Escape' }))
    expect(actions.cancel).toHaveBeenCalledWith('user')
  })
  it('blur取消', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false }))
    window.dispatchEvent(new Event('blur'))
    expect(actions.cancel).toHaveBeenCalledWith('focus_lost')
  })
  it('visibilitychange隐藏取消', () => {
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false }))
    Object.defineProperty(document, 'hidden', { value: true, configurable: true })
    document.dispatchEvent(new Event('visibilitychange'))
    expect(actions.cancel).toHaveBeenCalledWith('page_hidden')
    Object.defineProperty(document, 'hidden', { value: false, configurable: true })
  })
  it('pointercancel取消', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 1 }))
    button.dispatchEvent(new PointerEvent('pointercancel', { pointerId: 1 }))
    expect(actions.cancel).toHaveBeenCalledWith('pointer_cancel')
  })
  it('拥有者pointerup后lostpointercapture不二次取消', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 1 }))
    button.dispatchEvent(new PointerEvent('pointerup', { pointerId: 1, timeStamp: 100 }))
    // pointerup已清owner，lostpointercapture不应再触发cancel
    button.dispatchEvent(new PointerEvent('lostpointercapture', { pointerId: 1 }))
    expect(actions.cancel).not.toHaveBeenCalled()
  })
  it('活动时第二个pointerdown不抢占', () => {
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 1 }))
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 2 }))
    expect(actions.begin).toHaveBeenCalledTimes(1)
  })
  it('IME期间空格忽略', () => {
    document.dispatchEvent(new Event('compositionstart'))
    window.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false }))
    expect(actions.begin).not.toHaveBeenCalled()
    document.dispatchEvent(new Event('compositionend'))
  })
  it('输入框焦点时空格忽略', () => {
    const textInput = document.createElement('input')
    document.body.appendChild(textInput)
    textInput.focus()
    textInput.dispatchEvent(new KeyboardEvent('keydown', { code: 'Space', repeat: false, bubbles: true }))
    expect(actions.begin).not.toHaveBeenCalled()
    document.body.removeChild(textInput)
  })
  it('contextmenu阻止默认', () => {
    const event = new Event('contextmenu')
    const spy = vi.spyOn(event, 'preventDefault')
    button.dispatchEvent(event)
    expect(spy).toHaveBeenCalled()
  })
  it('dispose后不再响应', () => {
    input.dispose()
    button.dispatchEvent(new PointerEvent('pointerdown', { isPrimary: true, button: 0, pointerId: 1 }))
    expect(actions.begin).not.toHaveBeenCalled()
  })
})
