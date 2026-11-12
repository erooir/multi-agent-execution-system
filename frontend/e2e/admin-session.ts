import { createHash } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { BrowserContext } from "@playwright/test";

// API-focused tests share a verified session instead of consuming the production
// login rate limit repeatedly. Separate browser tests still exercise UI login.
export async function adminSession(context: BrowserContext) {
  const baseURL = process.env.HANGZHI_BROWSER_URL || "http://127.0.0.1:8000";
  const username = process.env.HANGZHI_SMOKE_USERNAME || "admin";
  const key = createHash("sha256")
    .update(`${baseURL}:${username}`)
    .digest("hex")
    .slice(0, 16);
  const directory = resolve("../.local/browser-test-sessions");
  const filename = resolve(directory, `${key}.json`);
  try {
    const saved = JSON.parse(await readFile(filename, "utf8"));
    if (saved.baseURL === baseURL && saved.username === username) {
      await context.addCookies(saved.cookies);
      const current = await context.request.get("/api/auth/me");
      if (current.ok()) {
        const user = await current.json();
        if (user.username === username && user.role === "admin") return current;
      }
    }
  } catch {
    // Missing, stale or invalid test state requires an ordinary authenticated login.
  }
  await context.clearCookies();
  const login = await context.request.post("/api/auth/login", {
    data: {
      username,
      password:
        process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password",
    },
  });
  if (login.ok()) {
    await mkdir(directory, { recursive: true });
    await writeFile(
      filename,
      JSON.stringify({ baseURL, username, cookies: await context.cookies() }),
      { mode: 0o600 },
    );
  }
  return login;
}
