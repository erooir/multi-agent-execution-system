import { request, type FullConfig } from "@playwright/test";
import { access, rm } from "node:fs/promises";
import { adminStorageStatePath } from "./auth-setup";

export default async function teardown(config: FullConfig) {
  const path = adminStorageStatePath();
  try {
    await access(path);
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return;
    throw error;
  }
  try {
    const session = await request.newContext({
      baseURL: config.projects[0].use.baseURL,
      storageState: path,
    });
    try {
      await session.post("/api/auth/logout");
    } finally {
      await session.dispose();
    }
  } finally {
    await rm(path, { force: true });
  }
}
