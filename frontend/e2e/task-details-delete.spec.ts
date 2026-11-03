import { test, expect } from "@playwright/test";

test("任务详情、步骤弹窗与终态任务删除", async ({ page }) => {
  const name = `任务详情验收-${Date.now()}`;
  await page.goto("/manage");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_USERNAME || "admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("当前项目")).toBeVisible();
  const p = await page.request.post("/api/v2/projects", {
    data: { name, description: "合成任务详情与删除验收" },
  });
  expect(p.ok()).toBeTruthy();
  const project = await p.json();
  const a = await page.request.post("/api/v2/data-assets", {
    data: {
      project_id: project.id,
      name: "合成输入表",
      modality: "structured",
      media_type: "application/json",
      content: [{ name: "Alpha", value: 2 }],
      source_id: "synthetic:task-details",
      provenance: "合成测试，不包含真实资料",
      classification: "local",
    },
  });
  expect(a.ok()).toBeTruthy();
  const asset = await a.json();
  const response = await page.request.post("/api/v2/tasks", {
    data: {
      project_id: project.id,
      name,
      goal: "整理合成记录并保留名称与数值",
      task_type: "structured_transform",
      input_refs: [asset.id],
      mode: "local",
      idempotency_key: name,
    },
  });
  expect(response.ok()).toBeTruthy();
  const task = await response.json();
  await expect
    .poll(
      async () =>
        (await (await page.request.get(`/api/v2/tasks/${task.id}`)).json())
          .status,
      { timeout: 30000 },
    )
    .toBe("succeeded");
  await page.reload();
  await page.getByLabel("当前项目").selectOption(project.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "任务运行", exact: true })
    .click();
  await page.getByRole("button", { name, exact: true }).click();
  await expect(page.locator(".task-summary")).toContainText(
    "整理合成记录并保留名称与数值",
  );
  await expect(page.locator(".task-summary")).toContainText(
    "synthetic:task-details",
  );
  await expect(page.locator(".task-summary")).toContainText("合成输入表");
  await expect(page.locator(".task-summary")).toContainText("创建时间");
  await page.getByRole("button", { name: "查看步骤详情", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("开始时间");
  await expect(page.getByRole("dialog")).toContainText("步骤输出");
  await expect(
    page.getByRole("dialog").getByText("Alpha", { exact: true }),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: "../.local/task-details-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  await expect(page.locator(".task-summary")).toBeVisible();
  await page.screenshot({
    path: "../.local/task-details-mobile.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.getByRole("button", { name: "删除任务", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("累计费用仍保留");
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "取消", exact: true })
    .click();
  await expect(page.locator(".task-summary")).toBeVisible();
  await page.getByRole("button", { name: "删除任务", exact: true }).click();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: "确认删除", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "任务运行", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name, exact: true })).toHaveCount(0);
  expect((await page.request.get(`/api/v2/tasks/${task.id}`)).status()).toBe(
    404,
  );
});
