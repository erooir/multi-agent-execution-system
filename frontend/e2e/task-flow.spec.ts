import { test, expect } from "@playwright/test";

test("真实浏览器：上传、失败保留、任务完成、双视角、复制、事件续传与产物权限", async ({
  page,
  playwright,
}) => {
  const name = `浏览器验收-${Date.now()}`;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("当前项目")).toBeVisible();
  await page.context().storageState({ path: "../.local/browser-auth.json" });
  const created = await page.request.post("/api/v2/projects", {
    data: { name, description: "浏览器自动验收，仅含合成资料" },
  });
  expect(created.ok()).toBeTruthy();
  const project = await created.json();
  await page.reload();
  await page.getByLabel("当前项目").selectOption(project.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "数据资产", exact: true })
    .click();
  await page.getByRole("button", { name: "登记 / 上传数据" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "上传文件", exact: true }).click();
  await dialog
    .getByLabel("选择文件")
    .setInputFiles({
      name: `${name}.json`,
      mimeType: "application/json",
      buffer: Buffer.from('[{"name":"A","amount":2},{"name":"B","amount":3}]'),
    });
  await dialog
    .getByLabel("来源标识", { exact: true })
    .fill(`synthetic:browser:${name}`);
  await dialog
    .getByLabel("来源及授权说明", { exact: true })
    .fill("合成公开样本，仅供浏览器功能验收。");
  await dialog.getByRole("button", { name: "验证并登记" }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "任务运行", exact: true })
    .click();
  await page.getByRole("button", { name: "新建任务", exact: true }).focus();
  await page.keyboard.press("Enter");
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("任务名称", { exact: true }).fill(name);
  await dialog
    .getByLabel("任务目标", { exact: true })
    .fill("规范化全部合成记录并保留字段。");
  await expect(
    dialog
      .getByRole("combobox", { name: "执行模式", exact: true })
      .getByRole("option", { name: "云端模型 · 计入累计预算" }),
  ).toHaveJSProperty("disabled", true);
  await dialog.getByRole("checkbox", { name: new RegExp(name) }).check();
  const fault = "**/api/v2/tasks/preflight";
  await page.route(fault, (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        detail: {
          code: "fixture_unavailable",
          message: "合成故障：预检查暂不可用",
        },
      }),
    }),
  );
  await dialog.getByRole("button", { name: "提交并自动执行" }).click();
  await expect(dialog.getByRole("alert")).toContainText("合成故障");
  await expect(dialog.getByLabel("任务名称", { exact: true })).toHaveValue(
    name,
  );
  await page.unroute(fault);
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v2/tasks") &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "提交并自动执行" }).click();
  const task = await (await accepted).json();
  await expect(page.locator(".task-strip .badge")).toHaveText("成功", {
    timeout: 20000,
  });
  await expect(page.locator(".react-flow__node")).toHaveCount(1);
  await page
    .getByRole("button", { name: "结构化数据整理", exact: true })
    .click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page
    .getByRole("dialog")
    .getByRole("button", { name: /运行实例/ })
    .click();
  await page.getByRole("button", { name: "查看所属任务" }).click();
  await expect(page.locator(".task-strip .badge")).toHaveText("成功");
  await page.getByRole("button", { name: /结果与产物/ }).click();
  await page.getByRole("button", { name: "查看内容", exact: true }).click();
  await expect(page.getByRole("dialog").locator("pre.prose")).toContainText(
    '"amount": 2',
  );
  await page.keyboard.press("Escape");
  const detail = await (
    await page.request.get(`/api/v2/tasks/${task.id}`)
  ).json();
  const artifact = detail.result.artifacts[0].id;
  expect(
    (await page.request.get(`/api/v2/artifacts/${artifact}/download`)).status(),
  ).toBe(200);
  const anonymous = await playwright.request.newContext({
    baseURL: process.env.HANGZHI_BROWSER_URL || "http://127.0.0.1:8000",
  });
  expect(
    (await anonymous.get(`/api/v2/artifacts/${artifact}/download`)).status(),
  ).toBe(401);
  await anonymous.dispose();
  const last = detail.events.at(-1).sequence;
  const resumed = await page.request.get(`/api/v2/tasks/${task.id}/stream`, {
    headers: { "Last-Event-ID": String(last - 1) },
  });
  expect(await resumed.text()).toContain(`id: ${last}`);
  await page.getByRole("button", { name: "复制为新任务" }).click();
  await expect(
    page.getByRole("dialog").getByLabel("任务名称", { exact: true }),
  ).toHaveValue(`${name}（重试）`);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  await expect(
    page.locator("tr").filter({ hasText: task.id }).locator(".badge"),
  ).toHaveText("成功");
  await page.screenshot({
    path: `../.local/browser-iteration.png`,
    fullPage: true,
  });
});

test("浏览器查看已完成真实云任务的MCP参数、结果和证据", async ({ browser }) => {
  const context = await browser.newContext({
    storageState: "../.local/browser-auth.json",
    baseURL: process.env.HANGZHI_BROWSER_URL || "http://127.0.0.1:8000",
  });
  try {
    const tasks = await (await context.request.get("/api/v2/tasks")).json();
    const task = tasks.find(
      (item: any) =>
        item.status === "succeeded" &&
        item.request.mode === "cloud" &&
        item.tool_calls?.some((call: any) => call.kind === "mcp"),
    );
    test.skip(!task, "本环境尚无真实云端MCP验收记录；CI不调用付费模型");
    const page = await context.newPage();
    await page.goto("/");
    await page.getByLabel("当前项目").selectOption(task.project_id);
    await page
      .getByRole("navigation")
      .getByRole("button", { name: "任务运行", exact: true })
      .click();
    await page
      .locator("tr")
      .filter({ hasText: task.id })
      .getByRole("button")
      .first()
      .click();
    await page.getByRole("button", { name: /^工具与技能/ }).click();
    const row = page
      .locator("tr")
      .filter({ hasText: "mcp.tables.aggregate" })
      .filter({ hasText: "已完成" })
      .first();
    await row.getByRole("button", { name: "查看输入与输出" }).click();
    await expect(page.getByRole("dialog")).toContainText('"groups"');
    await expect(page.getByRole("dialog")).toContainText('"result_checksum"');
    await page.keyboard.press("Escape");
    await page.screenshot({
      path: "../.local/browser-tools.png",
      fullPage: true,
    });
  } finally {
    await context.close();
  }
});
