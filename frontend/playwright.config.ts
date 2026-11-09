import { defineConfig } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { resolve } from "node:path";

// Propagated to workers/retries; concurrent test invocations never share a file.
process.env.HANGZHI_BROWSER_AUTH_STATE ||= resolve(
  "../.local/browser-sessions",
  `${randomUUID()}.json`,
);

export default defineConfig({
  testDir: "./e2e",
  workers: 1,
  timeout: 60000,
  globalSetup: "./e2e/auth-setup.ts",
  globalTeardown: "./e2e/auth-teardown.ts",
  use: {
    baseURL: process.env.HANGZHI_BROWSER_URL || "http://127.0.0.1:8000",
    channel: process.platform === "win32" ? "msedge" : undefined,
    headless: true,
    trace: "retain-on-failure",
    storageState: process.env.HANGZHI_BROWSER_AUTH_STATE,
  },
});
