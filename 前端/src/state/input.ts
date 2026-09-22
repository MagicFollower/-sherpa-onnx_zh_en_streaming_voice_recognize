import type { CancelReason } from '../protocol/messages'
export interface InputActions { begin: (source: 'pointer' | 'keyboard') => boolean; release: (time: number) => void; cancel: (reason: CancelReason) => void }
type Owner = { source: 'pointer'; id: number } | { source: 'keyboard' }
/**
 * 输入所有权与轮次状态分离：60秒自动结束后仍须先松开物理输入，不能自动续录。
 * 正常pointerup先清owner再释放capture，随后lostpointercapture不能把合法drain改成取消。
 * 非拥有者、IME、可编辑控件和repeat不抢占；只在真正接管空格时preventDefault。
 */
export class HoldInput {
  private owner?: Owner
  private composing = false
  private detach: (() => void)[] = []
  constructor(private button: HTMLElement, private actions: InputActions) {}
  pointerDown = (event: PointerEvent) => {
    if (this.owner || !event.isPrimary || (event.pointerType === 'mouse' && event.button !== 0)) return
    if (!this.actions.begin('pointer')) return
    this.owner = { source: 'pointer', id: event.pointerId }
    event.preventDefault()
    try { this.button.setPointerCapture(event.pointerId); if (!this.button.hasPointerCapture(event.pointerId)) throw new Error() }
    catch { this.abort('capture_lost') }
  }
  pointerUp = (event: PointerEvent) => {
    if (this.owner?.source !== 'pointer' || this.owner.id !== event.pointerId) return
    event.preventDefault()
    this.owner = undefined
    this.actions.release(event.timeStamp)
    this.releaseCapture(event.pointerId)
  }
  pointerCancel = (event: PointerEvent) => { if (this.owner?.source === 'pointer' && this.owner.id === event.pointerId) this.abort('pointer_cancel') }
  lostCapture = (event: PointerEvent) => { if (this.owner?.source === 'pointer' && this.owner.id === event.pointerId) this.abort('capture_lost') }
  keyDown = (event: KeyboardEvent) => {
    if (event.code === 'Escape' && !event.isComposing && !this.composing) { this.abort('user'); return }
    if (event.code !== 'Space' || event.repeat || event.isComposing || this.composing || this.owner) return
    const target = event.target instanceof Element ? event.target : null
    if (target !== this.button && target?.closest('input,textarea,select,button,a,[contenteditable]:not([contenteditable="false"]),[role="button"],[role="menu"],[role="menuitem"],[role="textbox"],[role="combobox"],[role="slider"],summary')) return
    if (!this.actions.begin('keyboard')) return
    this.owner = { source: 'keyboard' }; event.preventDefault()
  }
  keyUp = (event: KeyboardEvent) => {
    if (event.code !== 'Space' || this.owner?.source !== 'keyboard') return
    event.preventDefault(); this.owner = undefined; this.actions.release(event.timeStamp)
  }
  abort(reason: CancelReason) {
    const owner = this.owner; this.owner = undefined
    this.actions.cancel(reason)
    if (owner?.source === 'pointer') this.releaseCapture(owner.id)
  }
  attach() {
    const bind = (target: EventTarget, name: string, fn: EventListener) => { target.addEventListener(name, fn); this.detach.push(() => target.removeEventListener(name, fn)) }
    bind(this.button, 'pointerdown', this.pointerDown as EventListener)
    bind(this.button, 'pointerup', this.pointerUp as EventListener)
    bind(this.button, 'pointercancel', this.pointerCancel as EventListener)
    bind(this.button, 'lostpointercapture', this.lostCapture as EventListener)
    bind(this.button, 'contextmenu', event => event.preventDefault())
    bind(this.button, 'click', event => event.preventDefault())
    bind(window, 'keydown', this.keyDown as EventListener); bind(window, 'keyup', this.keyUp as EventListener)
    bind(window, 'blur', () => this.abort('focus_lost'))
    bind(document, 'visibilitychange', () => { if (document.hidden) this.abort('page_hidden') })
    bind(document, 'compositionstart', () => { this.composing = true })
    bind(document, 'compositionend', () => { this.composing = false })
    bind(window, 'pagehide', () => this.abort('page_hidden'))
  }
  dispose() { this.abort('page_hidden'); this.detach.forEach(fn => fn()); this.detach = [] }
  private releaseCapture(id: number) { try { if (this.button.hasPointerCapture(id)) this.button.releasePointerCapture(id) } catch { /* 已由浏览器释放，不产生第二次取消。 */ } }
}
