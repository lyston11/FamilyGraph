import { fileURLToPath, URL } from 'node:url'

import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    proxy: {
      // 开发模式代理：与生产 nginx 的 /api 反代行为保持一致（m0a design）
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    /*
     * 09-11 性能治理（R6）：
     * - manualChunks 把框架核心与 naive-ui 运行时从业务主 chunk 拆出，
     *   业务主 chunk（路由级动态 import）回到 500kB 以下；
     * - three.module（≈735kB，CosmicBackdrop 内按需 await import('three')，
     *   仅星空视图加载）是单一三方库无法再拆——属于经记录批准的阈值例外，
     *   chunkSizeWarningLimit 750 仅为此保留；评审以 build 产物清单为准
     *   （记录见任务 release-evidence.md）。
     */
    chunkSizeWarningLimit: 750,
    rollupOptions: {
      output: {
        manualChunks(id: string) {
          if (!id.includes('node_modules')) return undefined
          // 只拆框架核心：naive-ui 组件按视图自然分摊（合并会造出 >800kB 巨块）
          if (/[\\/]node_modules[\\/](@vue|vue|vue-router|pinia)[\\/]/.test(id)) {
            return 'vue-core'
          }
          return undefined
        },
      },
    },
  },
  test: {
    environment: 'jsdom',
    // jsdom 缺失 API 补丁（matchMedia 等，naive-ui 弹层渲染依赖，见文件头注释）
    setupFiles: ['./vitest.setup.ts'],
  },
})
