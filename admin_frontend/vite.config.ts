import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5174,
    // api_admin (8003) は localhost バインドのみ。ブラウザから直接叩けるよう
    // CORS を api_admin/main.py に追加済み。
  },
  build: {
    outDir: 'dist',
  },
})
