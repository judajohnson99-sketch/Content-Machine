import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // Pinned to 127.0.0.1, not 'localhost': the Django session cookie is
  // host-only and SameSite=Lax (webapp/cmweb/settings/dev.py), so the
  // frontend must be *served* from the same host the API/cookie use or the
  // browser drops the cookie on cross-site fetches, even with
  // credentials: "include".
  server: {
    host: '127.0.0.1',
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/setupTests.ts'],
  },
})
