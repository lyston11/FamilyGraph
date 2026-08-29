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
  test: {
    environment: 'jsdom',
    // jsdom 缺失 API 补丁（matchMedia 等，naive-ui 弹层渲染依赖，见文件头注释）
    setupFiles: ['./vitest.setup.ts'],
  },
})
