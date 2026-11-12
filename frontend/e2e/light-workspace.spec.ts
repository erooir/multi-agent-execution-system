import { test, expect } from "@playwright/test";
import { mkdir } from "node:fs/promises";

test("Light 工作空间：导航、说明展开、表单及移动布局", async ({ page }) => {
  const folder = "../.local/light-screens";
  await mkdir(folder, { recursive: true });
  await page.goto("/manage");
  await expect(page.locator("body")).toHaveCSS("color-scheme", "light");
  await page.screenshot({ path: `${folder}/login.png`, fullPage: true });
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_USERNAME || "admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("当前项目")).toBeVisible();
  const projectResponse = await page.request.post("/api/v2/projects", {
    data: {
      name: `Light 界面验收-${Date.now()}`,
      description: "界面验收使用的合成资料",
    },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const project = await projectResponse.json();
  const assetResponse = await page.request.post("/api/v2/data-assets", {
    data: {
      project_id: project.id,
      name: "季度数据（合成）",
      modality: "structured",
      media_type: "application/json",
      content: [{ name: "Alpha", value: 2 }],
      source_id: "synthetic:light-ui",
      provenance: "仅供 UI 回归的合成数据",
      classification: "local",
    },
  });
  expect(assetResponse.ok()).toBeTruthy();
  const asset = await assetResponse.json();
  const taskResponse = await page.request.post("/api/v2/tasks", {
    data: {
      project_id: project.id,
      name: "季度数据整理（合成）",
      goal: "整理合成表格并保留名称和数值",
      task_type: "structured_transform",
      input_refs: [asset.id],
      idempotency_key: `light-${Date.now()}`,
    },
  });
  expect(taskResponse.ok()).toBeTruthy();
  const task = await taskResponse.json();
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
  const pages = [
    "工作台",
    "场景分析",
    "智能体资产",
    "任务中心",
    "工作流",
    "工具箱",
    "扩展接入",
    "我的资料",
    "知识库",
    "效果评测",
    "设置",
  ];
  for (const [index, name] of pages.entries()) {
    await page
      .getByRole("navigation")
      .getByRole("button", { name, exact: true })
      .click();
    await expect(
      page.getByRole("heading", { name, exact: true }),
    ).toBeVisible();
    await expect(page.locator("main .error-line")).toHaveCount(0);
    if (name === "工作台") {
      await expect(page.getByLabel("当前项目")).toHaveValue(project.id);
      await expect(
        page.getByRole("region", { name: "项目资源汇总" }),
      ).toBeVisible();
      await expect(page.locator(".workspace-summary-stats dd")).toHaveCount(4);
      await expect(
        page.locator(".workspace-summary-stats dd").last(),
      ).toHaveText(/^\d+$/);
      await expect(page.locator("main button, main a")).toHaveCount(0);
    }
    await page.screenshot({
      path: `${folder}/page-${index}.png`,
      fullPage: true,
    });
  }
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "任务中心", exact: true })
    .click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("任务名称", { exact: true })).toBeVisible();
  await expect(
    dialog.getByRole("combobox", { name: "执行模式", exact: true }),
  ).toHaveValue("local");
  const help = dialog.locator(".help-note").first();
  await expect(help).not.toHaveAttribute("open", "");
  await help.locator("summary").click();
  await expect(help).toHaveAttribute("open", "");
  await expect(help.locator("div")).toBeVisible();
  await help.locator("summary").click();
  await page.screenshot({ path: `${folder}/task-form.png`, fullPage: true });
  await page.keyboard.press("Escape");
  await page
    .getByRole("button", { name: "季度数据整理（合成）", exact: true })
    .click();
  await expect(page.locator(".task-summary")).toContainText(
    "整理合成表格并保留名称和数值",
  );
  await page.getByRole("button", { name: "执行图与实例", exact: true }).click();
  await expect(page.locator(".react-flow")).toHaveClass(/light/);
  await page.screenshot({ path: `${folder}/task-graph.png`, fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "工作台", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "工作台", exact: true }),
  ).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await expect(page.locator(".workspace-summary-stats dd").last()).toHaveText(
    /^\d+$/,
  );
  await page.screenshot({ path: `${folder}/mobile.png`, fullPage: true });
  await page.goto("/");
  await expect(
    page.getByRole("heading", { name: "我的任务", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".taskcase-history")).not.toContainText(
    "正在读取任务",
  );
  await page.screenshot({
    path: `${folder}/question-mobile.png`,
    fullPage: true,
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.screenshot({ path: `${folder}/question.png`, fullPage: true });
});
