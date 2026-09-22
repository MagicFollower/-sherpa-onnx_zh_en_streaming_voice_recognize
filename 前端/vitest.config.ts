import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    include: ['tests/unit/**/*.test.ts'],
    setupFiles: ['tests/setup.ts'],
    restoreMocks: true,
    clearMocks: true,
    coverage: { provider: 'v8', include: ['src/**/*.ts', 'src/**/*.vue'], exclude: ['src/main.ts', 'src/audio/capture.worklet.ts'], reporter: ['text', 'json-summary', 'html'], reportsDirectory: 'coverage' },
  },
})
