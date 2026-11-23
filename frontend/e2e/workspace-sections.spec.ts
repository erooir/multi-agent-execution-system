import { expect, test } from "@playwright/test";
import { adminSession } from "./admin-session";
import { group, workspacePage } from "./workspace-navigation";

test("智能体卡片详情可用滚轮浏览且背景保持原位", async ({ page }) => {
  expect((await adminSession(page.context())).ok()).toBeTruthy();
  await page.setViewportSize({ width: 1280, height: 480 });
  await page.goto("/manage?view=hub&tab=expert");
  const card = page.locator(".hub-card").first();
  const name = await card.locator("strong").innerText();
  await card.scrollIntoViewIfNeeded();
  const pageScroll = () => page.evaluate(() => window.scrollY);
  const backgroundTop = await pageScroll();
  await card.click();

  const dialog = page.getByRole("dialog", { name, exact: true });
  const definition = dialog.getByRole("button", { name: "定义与生命周期" });
  const definitionTop = () =>
    definition.evaluate((el) => el.getBoundingClientRect().top);
  await expect(definition).toBeVisible();
  const startingTop = await definitionTop();
  await dialog.hover({ position: { x: 32, y: 120 } });
  await page.mouse.wheel(0, 450);
  await expect.poll(definitionTop).toBeLessThan(startingTop - 40);
  expect(await pageScroll()).toBe(backgroundTop);

  await dialog.getByRole("button", { name: "关闭对话框" }).click();
  await expect(dialog).toHaveCount(0);
  expect(await pageScroll()).toBe(backgroundTop);
  await card.hover();
  await page.mouse.wheel(0, backgroundTop > 0 ? -300 : 300);
  await expect.poll(pageScroll).not.toBe(backgroundTop);
});

test("资源计量、流程弹窗、连接器和侧栏都使用当前工作台", async ({ page }) => {
  expect((await adminSession(page.context())).ok()).toBeTruthy();
  const stamp = Date.now();
  const created = await page.request.post("/api/v2/projects", {
    data: { name: `界面验收-${stamp}`, description: "合成浏览器验证资料" },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  const serviceName = `合成 MCP ${stamp}`;
  const registered = await page.request.post(
    "/api/v2/capability-definitions/mcp",
    {
      data: {
        project_id: project.id,
        name: serviceName,
        transport: "stdio",
        launcher_id: "browser_fixture",
      },
    },
  );
  expect(registered.ok()).toBeTruthy();

  await page.goto("/manage");
  await group(page, project.id).click();
  await workspacePage(page, "智能体");
  for (const title of ["已发布智能体", "内置智能体"]) {
    const entry = page
      .locator(".hub-hero")
      .filter({ hasText: title })
      .getByRole("button")
      .first();
    const name = (await entry.innerText()).trim();
    await entry.click();
    const agent = page.getByRole("dialog", { name, exact: true });
    await expect(
      agent.getByRole("button", { name: "定义与生命周期" }),
    ).toBeVisible();
    await agent.getByRole("button", { name: "关闭对话框" }).click();
  }
  const coverage = page.locator(".hub-hero").filter({ hasText: "能力覆盖" });
  await expect(coverage).toContainText("类平台能力");
  for (const name of ["自主任务分析", "结构化数据整理", "来源文本整理"]) {
    await coverage.getByRole("button", { name, exact: true }).click();
    const detail = page.getByRole("dialog", {
      name: `能力详情 · ${name}`,
      exact: true,
    });
    await expect(
      detail.getByRole("heading", { name: /^声明此能力的智能体/ }),
    ).toBeVisible();
    if (name === "结构化数据整理") {
      await detail
        .locator(".record")
        .filter({ hasText: name })
        .getByRole("button", { name: "查看智能体", exact: true })
        .click();
      const agent = page.getByRole("dialog", { name, exact: true });
      await expect(
        agent.getByRole("button", { name: "定义与生命周期" }),
      ).toBeVisible();
      await agent.getByRole("button", { name: "关闭对话框" }).click();
      await expect(detail).toBeVisible();
    }
    await detail.getByRole("button", { name: "关闭对话框" }).click();
  }

  await workspacePage(page, "工作流");
  const library = page.locator(".table-scroll").filter({ hasText: "工作流" });
  const flow = library.getByRole("row").nth(1);
  const flowName = await flow.getByRole("cell").first().innerText();
  const openFlow = flow.getByRole("button", { name: "查看流程", exact: true });
  await openFlow.click();
  const flowDialog = page.getByRole("dialog", { name: flowName, exact: true });
  await expect(flowDialog).toBeVisible();
  await expect(library).toBeVisible();
  await expect(page.locator("main .asset-grid")).toHaveCount(0);
  await flowDialog.getByRole("button", { name: "关闭对话框" }).click();
  await expect(openFlow).toBeFocused();

  await workspacePage(page, "工具箱");
  await expect(page.getByText("MCP 表格", { exact: true })).toHaveCount(0);
  await expect(page.getByText("MCP 分组与统计", { exact: true })).toHaveCount(
    0,
  );

  await workspacePage(page, "连接器");
  await page.locator(".hub-card").filter({ hasText: "项目表格服务" }).click();
  const builtin = page.getByRole("dialog", {
    name: "项目表格服务",
    exact: true,
  });
  const aggregate = builtin
    .locator(".record")
    .filter({ hasText: "MCP 分组与统计" });
  await expect(aggregate).toBeVisible();
  await aggregate.getByRole("button", { name: "查看工具详情" }).click();
  const tool = page.getByRole("dialog", {
    name: "MCP 分组与统计",
    exact: true,
  });
  await expect(tool.getByText(/近7天该版本调用/)).toBeVisible();
  await expect(tool.getByText("查看输入与输出契约")).toBeVisible();
  await tool.getByRole("button", { name: "返回连接器详情" }).click();
  await expect(builtin.getByRole("button", { name: "管理连接器" })).toHaveCount(
    0,
  );
  await builtin.getByRole("button", { name: "关闭", exact: true }).click();
  await page.locator(".hub-card").filter({ hasText: serviceName }).click();
  const connector = page.getByRole("dialog", {
    name: serviceName,
    exact: true,
  });
  await connector.getByRole("button", { name: "管理连接器" }).click();
  const manager = page.getByRole("dialog", {
    name: `管理连接器 · ${serviceName}`,
    exact: true,
  });
  await expect(
    manager.getByRole("button", { name: "原生发现", exact: true }),
  ).toBeVisible();
  await expect(
    manager.getByRole("button", { name: "发布新版本", exact: true }),
  ).toBeVisible();
  await manager.getByRole("button", { name: "返回连接器详情" }).click();
  await expect(
    connector.getByRole("button", { name: "管理连接器" }),
  ).toBeFocused();
  await connector.getByRole("button", { name: "关闭", exact: true }).click();
  await expect(
    page.locator(".hub-card").filter({ hasText: serviceName }),
  ).toBeVisible();

  await page.locator(".wb-user-chip").click();
  const menu = page.getByRole("menu");
  await expect(menu.getByText("历史问答")).toHaveCount(0);
  await expect(menu.getByText("智能体资产")).toHaveCount(0);
  await page
    .locator(".wb-popover-backdrop")
    .click({ position: { x: 500, y: 20 } });

  const recent = page.getByRole("button", { name: "最近任务" });
  await expect(recent).toHaveAttribute("aria-expanded", "true");
  await recent.click();
  await expect(recent).toHaveAttribute("aria-expanded", "false");
  await recent.click();
  const nav = page.getByRole("navigation", { name: "主导航" });
  const scroll = page.locator(".wb-sidebar-scroll");
  await expect(scroll.locator(".wb-group-row").first()).toBeVisible();
  const bounds = await nav.boundingBox();
  await scroll.evaluate((el) => {
    el.style.maxHeight = "68px";
    el.scrollTop = el.scrollHeight;
  });
  expect(await scroll.evaluate((el) => el.scrollTop)).toBeGreaterThan(0);
  expect(await scroll.evaluate((el) => el.scrollWidth)).toBe(
    await scroll.evaluate((el) => el.clientWidth),
  );
  expect(await nav.boundingBox()).toEqual(bounds);
  await scroll.evaluate((el) => {
    el.style.removeProperty("max-height");
    el.scrollTop = 0;
  });

  await page.route("**/api/v2/resources?*", async (route) => {
    const response = await route.fetch();
    const data = await response.json();
    const gateway = {
      resource_schema_available: true,
      requests: 25,
      intranet_requests: 4,
      reported: 20,
      unconfirmed: 5,
      input_tokens: 750000,
      output_tokens: 250000,
      peak_context_tokens: 131072,
      peak_context_ratio: 0.5,
      estimated_peak_upper: 200000,
      estimated_samples: 10,
      latency: { samples: 15, p50_ms: 750, p95_ms: 1500 },
      settled_cny: 2.4,
      reserved_cny: 0.6,
    };
    await route.fulfill({ response, json: { ...data, gateway } });
  });
  await workspacePage(page, "资源计量");
  const dashboard = page.getByRole("region", { name: "资源计量" });
  await expect(dashboard.getByText("750,000", { exact: true })).toBeVisible();
  await expect(dashboard.getByText("1,500 ms", { exact: true })).toBeVisible();
  await expect(
    dashboard.locator(".resource-breakdown i").first(),
  ).toHaveAttribute("style", "width: 75%;");
  await expect(
    dashboard.locator(".resource-context + .resource-track i"),
  ).toHaveAttribute("style", "width: 50%;");
  await page.screenshot({
    path: test.info().outputPath("resource-dashboard.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390);
  await expect(dashboard.getByText("250,000", { exact: true })).toBeVisible();
});
