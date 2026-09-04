import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  timeout: 30000,
  retries: 1,
  use: {
    baseURL: 'http://localhost:5173',
    screenshot: 'only-on-failure',
    trace: 'on-first-retry',
  },
  projects: [
    { name: 'headless', use: { browserName: 'chromium', headless: true } },
    { name: 'headed', use: { browserName: 'chromium', headless: false } },
  ],
  reporter: [['html', { open: 'never' }], ['list']],
})
