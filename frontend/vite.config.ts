import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Proxy API calls to the FastAPI backend during development
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/analyzeArea': 'http://localhost:4289',
      '/analyzeSite': 'http://localhost:4289',
      '/analyzeContour': 'http://localhost:4289',
      '/findCatchment': 'http://localhost:4289',
      '/export': 'http://localhost:4289',
      '/cache': 'http://localhost:4289',
      '/health': 'http://localhost:4289',
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})
