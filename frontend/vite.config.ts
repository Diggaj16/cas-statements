import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // honor PORT from the environment so preview tooling can assign a free port
    port: process.env.PORT ? Number(process.env.PORT) : 5173,
    // local-only tool; allow serving when launched via 8.3 short paths (no spaces)
    fs: { strict: false },
  },
})
