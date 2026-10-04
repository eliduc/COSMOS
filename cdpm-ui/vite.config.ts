import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' — сборка раздаётся FastAPI как статика по /ui/ (пути
// ресурсов относительные). Прокси нужен только dev-серверу.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8095',
    },
  },
})
