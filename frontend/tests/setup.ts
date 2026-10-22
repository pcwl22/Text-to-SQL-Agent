import { vi } from 'vitest'

class ResizeObserverStub { observe() {} unobserve() {} disconnect() {} }
globalThis.ResizeObserver = ResizeObserverStub
window.matchMedia = vi.fn().mockImplementation(() => ({ matches: false, media: '', onchange: null, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent() { return false } }))
// Any accidental fetch/XHR is a test failure; all product data must be local.
globalThis.fetch = vi.fn(() => { throw new Error('Frontend tests must not use network') })
vi.spyOn(XMLHttpRequest.prototype, 'send').mockImplementation(() => { throw new Error('Frontend tests must not use network') })
