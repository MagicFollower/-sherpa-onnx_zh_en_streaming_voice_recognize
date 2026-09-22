import { readFileSync } from 'node:fs'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  base: '/',
  // worker&url触发Vite独立打包FIR依赖；不能把原始TypeScript作为普通?url资源发布。
  worker: { format: 'es' },
  build: { outDir: 'dist', target: 'es2022', sourcemap: false },
  // 监听所有网卡，局域网设备可通过 https://<本机IP>:5173 访问。
  // Vite 代理在服务端转发到后端 localhost:8765，后端无需对外开放。
  // HTTPS 使用 mkcert 生成的本地 CA 证书，手机端需接受证书警告或安装根 CA。
  server: {
    host: '0.0.0.0', port: 5173, strictPort: true,
    https: { key: readFileSync('./.cert-key.pem'), cert: readFileSync('./.cert.pem') },
    proxy: {
      '/api': { target: 'http://localhost:8765', changeOrigin: true },
      '/ws': { target: 'ws://localhost:8765', ws: true, changeOrigin: true },
    },
  },
  preview: {
    host: '0.0.0.0', port: 4173, strictPort: true,
    https: { key: readFileSync('./.cert-key.pem'), cert: readFileSync('./.cert.pem') },
  },
})
