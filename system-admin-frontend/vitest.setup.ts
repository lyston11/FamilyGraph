/**
 * Vitest 全局环境补丁（jsdom 缺失 API）。
 *
 * naive-ui 内部组件（弹层/虚拟列表/媒体查询钩子）在 setup 期调用
 * window.matchMedia；jsdom 未实现该 API，缺失会让组件 setup 抛错。
 * 这里做最小可用 stub：不匹配任何查询、无事件监听副作用。
 */
if (typeof window !== 'undefined' && typeof window.matchMedia !== 'function') {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    configurable: true,
    value: (query: string): MediaQueryList =>
      ({
        matches: false,
        media: query,
        onchange: null,
        addListener: () => undefined, // 已废弃的旧 API，仅保留空实现
        removeListener: () => undefined,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
        dispatchEvent: () => false,
      }) as MediaQueryList,
  })
}

// jsdom 未实现 Element.scrollTo/scrollBy：naive-ui 弹层滚动会以
// unhandled rejection 形式污染测试进程，这里补空实现。
if (typeof Element !== 'undefined' && typeof Element.prototype.scrollTo !== 'function') {
  Element.prototype.scrollTo = (() => undefined) as typeof Element.prototype.scrollTo
  Element.prototype.scrollBy = (() => undefined) as typeof Element.prototype.scrollBy
}

// jsdom 未实现 blob object URL：头像缩略图（blob URL 内存展示）测试需要 stub
if (typeof URL !== 'undefined' && typeof URL.createObjectURL !== 'function') {
  URL.createObjectURL = (() => 'blob:mock') as typeof URL.createObjectURL
  URL.revokeObjectURL = (() => undefined) as typeof URL.revokeObjectURL
}
