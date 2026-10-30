import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', 'VITE_')
  // Workspace Backend 使用动态端口；开发服务器的代理必须与前端 API 客户端
  // 读取同一个 VITE_EDGE_API_URL，否则 /api/docs 等相对链接会落到旧的 8002。
  const apiTarget = (env.VITE_EDGE_API_URL || 'http://127.0.0.1:8002').replace(/\/+$/, '')

  return {
    base: '/console/',
    plugins: [react()],
    build: {
      outDir: '../unilabos/app/web/static/console',
      emptyOutDir: true,
    },
    server: {
      port: 4174,
      strictPort: true,
      proxy: {
        '/api': {
          target: apiTarget,
          changeOrigin: true,
        },
      },
    },
    preview: {
      port: 4174,
      strictPort: true,
    },
    test: {
      environment: 'jsdom',
      setupFiles: ['./src/test/setup.ts'],
      css: true,
    },
  }
})
