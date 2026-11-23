import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "tests",
  testMatch: "*.spec.js",
  workers: 1,
  timeout: 30000,
  use: {
    baseURL: `https://localhost:${process.env.VIEWER_TEST_PORT || 18951}`,
    ignoreHTTPSErrors: true,
    viewport: { width: 1280, height: 800 },
    screenshot: "only-on-failure",
  },
  webServer: {
    command: "node tests/serve.mjs",
    url: `https://localhost:${process.env.VIEWER_TEST_PORT || 18951}/health`,
    ignoreHTTPSErrors: true,
    reuseExistingServer: false,
    gracefulShutdown: { signal: "SIGTERM", timeout: 60000 },
    timeout: 120000,
  },
});
