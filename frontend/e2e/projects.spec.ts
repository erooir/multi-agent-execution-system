import { test, expect } from "@playwright/test";

test("同名项目区分、改名保存和项目范围保持", async ({ page }) => {
  await page.goto("/manage");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_USERNAME || "admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("当前项目")).toBeVisible();

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
  const picker = page.getByLabel("当前项目");
  await expect(picker.locator(`option[value="${first.id}"]`)).toHaveText(
    `${name} · ${first.id.slice(0, 8)}`,
  );
  await expect(picker.locator(`option[value="${second.id}"]`)).toHaveText(
    `${name} · ${second.id.slice(0, 8)}`,
  );
  await picker.selectOption(first.id);

  // The question portal uses the same disambiguation without exposing project data.
  await page.goto("/");
  await expect(
    page.getByLabel("当前项目").locator(`option[value="${first.id}"]`),
  ).toHaveText(`${name} · ${first.id.slice(0, 8)}`);
  await page.goto("/manage");
  await picker.selectOption(first.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "设置", exact: true })
    .click();
  await page.getByRole("button", { name: "项目与成员", exact: true }).click();
  const row = page
    .getByRole("row")
    .filter({ has: page.getByText(first.id, { exact: true }) });
  await row.getByRole("button", { name: "改名", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "修改项目名称" });
  await expect(dialog.getByLabel("项目名称")).toHaveValue(name);
  await dialog.getByLabel("项目名称").fill("取消的名称");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await expect(picker.locator(`option[value="${first.id}"]`)).toContainText(
    name,
  );
  await row.getByRole("button", { name: "改名", exact: true }).click();
  const renamed = `已改名-${Date.now()}`;
  await dialog.getByLabel("项目名称").fill(renamed);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(picker).toHaveValue(first.id);
  await expect(picker.locator(`option[value="${first.id}"]`)).toHaveText(
    renamed,
  );
  await expect(picker.locator(`option[value="${second.id}"]`)).toHaveText(name);
  await expect(page.locator(".breadcrumb")).toContainText(renamed);
  await page.reload();
  await expect(picker.locator(`option[value="${first.id}"]`)).toHaveText(
    renamed,
  );
  await picker.selectOption(first.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "我的资料", exact: true })
    .click();
  await expect(
    page.getByRole("main").getByText("项目专属资料（合成）", { exact: true }),
  ).toBeVisible();
  await picker.selectOption(second.id);
  await expect(
    page.getByRole("main").getByText("项目专属资料（合成）", { exact: true }),
  ).toHaveCount(0);
});
