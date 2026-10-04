import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './' — сборка раздаётся службой каталога как статика по /ui/
// (пути ресурсов относительные, как у панели CDPM). Прокси нужен
// только dev-серверу: служба слушает 8105.
export default defineConfig({
  plugins: [react()],
  base: './',
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8105',
    },
  },
})
