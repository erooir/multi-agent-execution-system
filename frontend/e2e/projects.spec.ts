import { adminSession } from "./admin-session";
import { group, currentGroup, workspacePage } from "./workspace-navigation";
import { test, expect } from "@playwright/test";

test("同名项目区分、改名保存和项目范围保持", async ({ page }) => {
  expect((await adminSession(page.context())).ok()).toBeTruthy();
  await page.goto("/manage");
  await expect(page.locator(".wb-main")).toBeVisible();

  const name = `同名项目-${Date.now()}`;
  const projects = [];
  for (let i = 0; i < 2; i++) {
    const response = await page.request.post("/api/v2/projects", {
      data: { name, description: "合成项目，用于改名与范围回归" },
    });
    expect(response.status()).toBe(201);
    projects.push(await response.json());
  }
  const [first, second] = projects;
  const asset = await page.request.post("/api/v2/data-assets", {
    data: {
      project_id: first.id,
      name: "项目专属资料（合成）",
      modality: "text",
      media_type: "text/plain",
      content: "Synthetic project-scope verification.",
      source_id: "synthetic:project-rename",
      provenance: "合成测试",
      classification: "local",
    },
  });
  expect(asset.ok()).toBeTruthy();
  await page.reload();

  await expect(group(page, first.id)).toHaveText(
    `${name} · ${first.id.slice(0, 8)}`,
  );
  await expect(group(page, second.id)).toHaveText(
    `${name} · ${second.id.slice(0, 8)}`,
  );
  await group(page, first.id).click();

  // The question portal uses the same disambiguation without exposing project data.
  await page.goto("/");
  await expect(group(page, first.id)).toHaveText(
    `${name} · ${first.id.slice(0, 8)}`,
  );
  await page.goto("/manage");
  await group(page, first.id).click();
  await workspacePage(page, "设置");
  await page.getByRole("button", { name: "项目与成员", exact: true }).click();
  const row = page
    .getByRole("row")
    .filter({ has: page.getByText(first.id, { exact: true }) });
  await row.getByRole("button", { name: "改名", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "修改项目名称" });
  await expect(dialog.getByLabel("项目名称")).toHaveValue(name);
  await dialog.getByLabel("项目名称").fill("取消的名称");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(group(page, first.id)).toContainText(name);
  await row.getByRole("button", { name: "改名", exact: true }).click();
  const renamed = `已改名-${Date.now()}`;
  await dialog.getByLabel("项目名称").fill(renamed);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(currentGroup(page)).toHaveAttribute("data-project-id", first.id);
  await expect(group(page, first.id)).toHaveText(renamed);
  await expect(group(page, second.id)).toHaveText(name);
  await expect(currentGroup(page)).toContainText(renamed);
  await page.reload();
  await expect(group(page, first.id)).toHaveText(renamed);
  await group(page, first.id).click();
  await workspacePage(page, "我的资料");
  await expect(
    page.getByRole("main").getByText("项目专属资料（合成）", { exact: true }),
  ).toBeVisible();
  await group(page, second.id).click();
  await expect(
    page.getByRole("main").getByText("项目专属资料（合成）", { exact: true }),
  ).toHaveCount(0);
});
