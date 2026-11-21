import { adminSession } from "./admin-session";
import { group, workspacePage } from "./workspace-navigation";
import { test, expect } from "@playwright/test";

test("接入能力：Calculator登记、发布与不可变版本", async ({ page }) => {
  expect((await adminSession(page.context())).ok()).toBeTruthy();
  await page.goto("/manage");
  await expect(page.locator(".wb-main")).toBeVisible();
  const created = await page.request.post("/api/v2/projects", {
    data: { name: `原生能力验收-${Date.now()}`, description: "仅合成配置" },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  await page.reload();
  await group(page, project.id).click();
  await workspacePage(page, "扩展接入");
  await page
    .getByRole("main")
    .getByRole("button", { name: "接入工具", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog.getByLabel("接入类型").selectOption("agno_toolkit");
  await dialog.getByLabel("名称").fill("原生计算器");
  await dialog.getByLabel("允许功能").fill("add");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText("原生计算器", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "发布新版本", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByText(/^平台版本 v1 ·/)).toBeVisible();
  await page.getByRole("button", { name: "版本", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog.getByText("v1", { exact: true })).toBeVisible();
  await dialog.getByText("不可变定义或发现快照", { exact: true }).click();
  await expect(dialog.getByText('"native_driver"')).toBeVisible();
});
