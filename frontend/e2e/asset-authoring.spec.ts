import { test, expect } from "@playwright/test";

test("真实资产编排：定义表单、不可变版本差异、可视化依赖与循环保护", async ({
  page,
}) => {
  await page.setViewportSize({ width: 1440, height: 1100 });
  const name = `资产编排验收-${Date.now()}`;
  await page.goto("/");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_USERNAME || "admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("当前项目")).toBeVisible();
  const response = await page.request.post("/api/v2/projects", {
    data: {
      name,
      description: "仅含合成配置；本地工具编排验收，不调用付费模型。",
    },
  });
  expect(response.ok()).toBeTruthy();
  const project = await response.json();
  await page.reload();
  await page.getByLabel("当前项目").selectOption(project.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "智能体资产", exact: true })
    .click();
  await page.getByRole("button", { name: "创建智能体", exact: true }).click();
  let dialog = page.getByRole("dialog", { name: "创建智能体", exact: true });
  await dialog.getByLabel("名称", { exact: true }).fill(name);
  await dialog
    .getByLabel("职责说明", { exact: true })
    .fill("整理合成表格并清除重复记录。");
  await dialog
    .getByRole("combobox", { name: "结构化处理方式", exact: true })
    .selectOption("deduplicate");
  await dialog.getByLabel(/^排序字段/).fill("name");
  await dialog.getByRole("button", { name: "保存定义", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await page.getByRole("button", { name: new RegExp(name) }).click();
  let details = page.getByRole("dialog", { name, exact: true });
  await expect(
    details.getByText("处理方式：去除重复记录", { exact: true }),
  ).toBeVisible();
  await details
    .getByRole("button", { name: "发布新版本", exact: true })
    .click();
  let confirmation = page.getByRole("dialog", {
    name: `发布新版本「${name}」`,
    exact: true,
  });
  await confirmation
    .getByRole("button", { name: "发布新版本", exact: true })
    .click();
  await expect(confirmation).not.toBeVisible();
  await details.getByRole("button", { name: "编辑定义", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "编辑智能体定义", exact: true });
  await dialog
    .getByLabel("执行指令", { exact: true })
    .fill("保持所有字段来源，不补造缺失内容。");
  await dialog.getByRole("button", { name: "保存定义", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await details
    .getByRole("button", { name: "发布新版本", exact: true })
    .click();
  confirmation = page.getByRole("dialog", {
    name: `发布新版本「${name}」`,
    exact: true,
  });
  await confirmation
    .getByRole("button", { name: "发布新版本", exact: true })
    .click();
  await expect(confirmation).not.toBeVisible();
  await details.getByRole("button", { name: /不可变版本/ }).click();
  await expect(
    details.getByRole("heading", { name: "版本差异", exact: true }),
  ).toBeVisible();
  await expect(details.locator(".version-diff")).toContainText("执行指令");
  await expect(details.locator(".version-diff")).toContainText(
    "保持所有字段来源",
  );
  await details
    .getByRole("button", { name: "关闭对话框", exact: true })
    .click();

  await page
    .getByRole("navigation")
    .getByRole("button", { name: "协作流程", exact: true })
    .click();
  await page.getByRole("button", { name: "创建流程", exact: true }).click();
  dialog = page.getByRole("dialog", { name: "创建复用流程", exact: true });
  await dialog.getByLabel("流程名称", { exact: true }).fill(`${name}-双节点`);
  await dialog
    .getByRole("combobox", { name: "添加智能体节点", exact: true })
    .selectOption({ label: `${name} · v2` });
  await dialog.getByRole("button", { name: "添加节点", exact: true }).click();
  await dialog.getByLabel("节点名称", { exact: true }).fill("整理来源");
  await dialog.getByRole("button", { name: "添加节点", exact: true }).click();
  await dialog.getByLabel("节点名称", { exact: true }).fill("整理结果");
  await dialog.getByRole("checkbox", { name: "整理来源", exact: true }).check();
  await expect(dialog.locator(".react-flow__edge")).toHaveCount(1);
  await dialog
    .getByRole("checkbox", { name: "整理来源", exact: true })
    .uncheck();
  await expect(dialog.locator(".react-flow__edge")).toHaveCount(0);
  await dialog.locator(".builder-canvas").scrollIntoViewIfNeeded();
  const sourceHandle = dialog
    .locator(".react-flow__node")
    .filter({ hasText: "整理来源" })
    .locator(".react-flow__handle.source");
  const targetHandle = dialog
    .locator(".react-flow__node")
    .filter({ hasText: "整理结果" })
    .locator(".react-flow__handle.target");
  await sourceHandle.dragTo(targetHandle);
  await expect(dialog.locator(".react-flow__edge")).toHaveCount(1);
  await expect(
    dialog.getByRole("checkbox", { name: "整理来源", exact: true }),
  ).toBeChecked();

  await dialog
    .getByRole("combobox", { name: "当前节点", exact: true })
    .selectOption({ label: "1. 整理来源" });
  await dialog.getByRole("checkbox", { name: "整理结果", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("会形成循环");
  await expect(
    dialog.getByRole("checkbox", { name: "整理结果", exact: true }),
  ).not.toBeChecked();
  await dialog.getByRole("button", { name: "整理布局", exact: true }).click();
  await dialog
    .locator(".workflow-builder")
    .screenshot({ path: test.info().outputPath("workflow-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await page.setViewportSize({ width: 1440, height: 1000 });
  const saved = page.waitForResponse(
    (item) =>
      item.url().endsWith("/api/v2/workflows") &&
      item.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "保存流程", exact: true }).click();
  const workflow = await (await saved).json();
  await expect(dialog).not.toBeVisible();
  expect(workflow.nodes).toHaveLength(2);
  expect(workflow.nodes[1].depends_on).toEqual([workflow.nodes[0].id]);
  expect(workflow.nodes[0].depends_on).toEqual([]);
  await page
    .getByRole("button", { name: new RegExp(`${name}-双节点`) })
    .click();
  details = page.getByRole("dialog", { name: `${name}-双节点`, exact: true });
  await details
    .getByRole("button", { name: "校验并发布", exact: true })
    .click();
  confirmation = page.getByRole("dialog", {
    name: `发布「${name}-双节点」`,
    exact: true,
  });
  await confirmation.getByRole("button", { name: "确认", exact: true }).click();
  await expect(confirmation).not.toBeVisible();
  const published = await page.request.get(
    `/api/v2/workflows/${workflow.id}/versions`,
  );
  const versions = await published.json();
  expect(versions).toHaveLength(1);
  expect(
    versions[0].nodes.map(
      (node: { agent_version: number }) => node.agent_version,
    ),
  ).toEqual([2, 2]);
});
