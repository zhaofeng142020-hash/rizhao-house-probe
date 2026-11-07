import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 开发模式把 /api 代理到本地后端（默认 127.0.0.1:8000）
// 生产构建产物直接输出到后端包内（backend/app/webui），由 FastAPI 托管，
// 单端口即可访问。这样部署时前端产物跟着 Python 包一起走，不会被当成
// 「构建产物」排除掉。
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    host: '0.0.0.0',        // 允许局域网/手机直接访问开发服务器
    proxy: {
      '/api': {
        target: process.env.RZ_API_BASE || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: '../backend/app/webui',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1600,
  },
})
