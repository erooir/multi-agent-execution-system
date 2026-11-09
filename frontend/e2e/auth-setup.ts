import { expect, request, type FullConfig } from "@playwright/test";
import { mkdir } from "node:fs/promises";
import { dirname } from "node:path";

export function adminStorageStatePath(): string {
  const path = process.env.HANGZHI_BROWSER_AUTH_STATE;
  if (!path)
    throw new Error(
      "Browser authentication setup did not initialize a session path",
    );
  return path;
}

export default async function setup(config: FullConfig) {
  const session = await request.newContext({
    baseURL: config.projects[0].use.baseURL,
  });
  try {
    // One login per invocation, independent of test order, retries or filters.
    // Business tests still get separate browser contexts and their own projects.
    const response = await session.post("/api/auth/login", {
      data: {
        username: process.env.HANGZHI_SMOKE_USERNAME || "admin",
        password:
          process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password",
      },
    });
    expect(
      response.ok(),
      `Browser setup login returned HTTP ${response.status()}`,
    ).toBeTruthy();
    const path = adminStorageStatePath();
    await mkdir(dirname(path), { recursive: true });
    await session.storageState({ path });
  } finally {
    await session.dispose();
  }
}
