import { group, currentGroup, workspacePage } from "./workspace-navigation";
import { test, expect } from "@playwright/test";
import { adminSession } from "./admin-session";

test("工作台只刷新当前项目，迟到的项目响应不能切回旧项目", async ({ page }) => {
  const login = await adminSession(page.context());
  expect(login.ok()).toBeTruthy();
  const stamp = Date.now();
  async function seed(side: string) {
    const response = await page.request.post("/api/v2/projects", {
      data: { name: side + "-scope-" + stamp },
    });
    expect(response.ok()).toBeTruthy();
    const p = await response.json(),
      name = side + "-record-" + stamp;
    const source = await page.request.post("/api/v2/data-assets", {
      data: {
        project_id: p.id,
        name,
        source_id: name,
        provenance: "synthetic",
        modality: "text",
        media_type: "text/plain",
        classification: "local",
        content: "test",
      },
    });
    expect(source.ok()).toBeTruthy();
    return { id: p.id, name };
  }
  const a = await seed("A"),
    b = await seed("B");
  const requested: URL[] = [];
  page.on("request", (r) => {
    const url = new URL(r.url());
    if (url.pathname === "/api/v2/bootstrap") requested.push(url);
  });
  const scoped = (url: string, id: string) => {
    const u = new URL(url);
    return (
      u.pathname === "/api/v2/bootstrap" &&
      u.searchParams.get("project_id") === id
    );
  };
  await page.goto("/manage");

  await expect(group(page, b.id)).toBeAttached();
  if ((await currentGroup(page).getAttribute("data-project-id")) !== b.id) {
    const refreshed = page.waitForResponse((r) => scoped(r.url(), b.id));
    await group(page, b.id).click();
    expect((await refreshed).ok()).toBeTruthy();
  }
  await workspacePage(page, "我的资料");
  await expect(page.getByText(b.name, { exact: true })).toBeVisible();
  let release!: () => void,
    captured!: () => void,
    completed!: () => void,
    held = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  const seen = new Promise<void>((resolve) => {
    captured = resolve;
  });
  const done = new Promise<void>((resolve) => {
    completed = resolve;
  });
  await page.route("**/api/v2/bootstrap?*", async (route) => {
    if (held || !scoped(route.request().url(), a.id)) {
      await route.continue();
      return;
    }
    held = true;
    const original = await route.fetch();
    captured();
    await gate;
    await route.fulfill({ response: original });
    completed();
  });
  try {
    await group(page, a.id).click();
    await seen;
    const newResponse = page.waitForResponse((r) => scoped(r.url(), b.id));
    await group(page, b.id).click();
    expect((await newResponse).ok()).toBeTruthy();
    release();
    await done;
    await page.evaluate(
      () =>
        new Promise((resolve) =>
          requestAnimationFrame(() =>
            requestAnimationFrame(() => resolve(true)),
          ),
        ),
    );
    await expect(currentGroup(page)).toHaveAttribute("data-project-id", b.id);
    await expect(page.getByText(b.name, { exact: true })).toBeVisible();
    await expect(page.getByText(a.name, { exact: true })).toHaveCount(0);
    expect(requested.length).toBeGreaterThan(2);
    expect(
      requested.every((url) => url.searchParams.get("scope") === "current"),
    ).toBeTruthy();
  } finally {
    release();
  }
});
