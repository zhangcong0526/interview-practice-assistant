import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// 显式绑定 IPv4，否则某些 Node 版本只监听 [::1]，
// 浏览器访问 http://127.0.0.1:5173 会连接被拒。
const host = '127.0.0.1'
const proxy = {
  '/api': 'http://127.0.0.1:8000',
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  // strictPort：端口被占用时直接报错，不要静默换端口，保证入口地址固定。
  server: { host, port: 5173, strictPort: true, proxy },
  // 预览生产产物时同样需要代理，否则页面拿不到后端数据。
  preview: { host, port: 4173, strictPort: true, proxy },
})
