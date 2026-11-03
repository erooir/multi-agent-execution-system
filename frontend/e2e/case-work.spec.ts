import { test, expect } from "@playwright/test";

test("用例任务：真实提交、三节点审核、来源证据与智能体监测", async ({
  page,
}) => {
  await page.goto("/manage");
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_USERNAME || "admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByLabel("当前项目")).toBeVisible();
  const name = "用例验收-" + Date.now();
  const p = await page.request.post("/api/v2/projects", { data: { name } });
  expect(p.ok()).toBeTruthy();
  const { id } = await p.json();
  const fields = [
    {
      kind: "policy",
      start_year: 2000,
      target_year: 2004,
      baseline_years: [2000, 2002],
      extent_unit: "km²",
    },
    ...[10, 12, 14, 11, 9].map((extent, i) => ({
      year: 2000 + i,
      month: 9,
      hemisphere: "N",
      extent,
    })),
  ];
  const a = await page.request.post("/api/v2/data-assets", {
    data: {
      project_id: id,
      name: "合成逐年观测",
      modality: "structured",
      media_type: "application/json",
      content: { records: fields },
      source_id: "test-observation",
      provenance: "synthetic",
      classification: "local",
    },
  });
  expect(a.ok()).toBeTruthy();
  await page.reload();
  await page.getByLabel("当前项目").selectOption(id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "场景分析" })
    .click();
  await page.getByRole("button", { name: "发布本项目用例岗位与流程" }).click();
  await page.getByRole("button", { name: "地理与时序", exact: true }).click();
  const card = page.locator(".case-grid .panel").filter({ hasText: "G03" });
  await card.getByRole("button", { name: "创建用例任务" }).click();
  const dlg = page.getByRole("dialog");
  await expect(dlg.getByLabel("分析用例")).toHaveValue("G03");
  await dlg.getByLabel("验收范围").selectOption("sample");
  await dlg.getByRole("checkbox", { name: /合成逐年观测/ }).check();
  await dlg.getByRole("button", { name: "检查匹配与授权" }).click();
  await expect(dlg.getByRole("status")).toContainText("预检查通过");
  const accepted = page.waitForResponse(
    (r) => r.url().endsWith("/api/v2/tasks") && r.request().method() === "POST",
  );
  await dlg.getByRole("button", { name: "提交并自动执行" }).click();
  const task = await (await accepted).json();
  expect(task.request.case_id).toBe("G03");
  await expect(page.locator(".task-strip .badge")).toHaveText("成功", {
    timeout: 35000,
  });
  await page.getByRole("button", { name: "执行图与实例", exact: true }).click();
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  const detail = await (
    await page.request.get("/api/v2/tasks/" + task.id)
  ).json();
  expect(detail.artifacts).toHaveLength(1);
  expect(detail.artifacts[0].content.products.anomaly.difference).toBe(-3);
  expect(detail.quality_verdict.passed).toBe(true);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "场景分析" })
    .click();
  await page.getByRole("button", { name: "智能体运行监测" }).click();
  const monitored = page
    .locator("tbody tr")
    .filter({ hasText: "独立证据核查员" });
  await expect(monitored).toContainText("1 / 0");
  await monitored.getByRole("button", { name: /review/ }).click();
  await expect(page.locator(".task-strip .badge")).toHaveText("成功");
  const finding = detail.artifacts[0].content.tool_findings.find(
    (item: any) => item.tool_id === "series.baseline_anomaly",
  );
  expect(finding.tool_call_id).toBeTruthy();
  await page.getByRole("button", { name: /^结果与产物/ }).click();
  await page.getByRole("button", { name: "查看内容", exact: true }).click();
  await page
    .locator("summary")
    .filter({ hasText: "注册工具的实际观测" })
    .click();
  await page.locator("summary").filter({ hasText: finding.tool_id }).click();
  await page.getByRole("button", { name: "追溯实际调用" }).click();
  const trace = page.getByRole("dialog", { name: "工具调用详情" });
  await expect(trace).toContainText(finding.tool_call_id);
  await expect(trace).toContainText("第 1 轮");
  await expect(trace).toContainText("合成逐年观测");
  await expect(trace).toContainText("baseline_mean");
  const revoke = await page.request.post(
    "/api/v2/data-assets/" + finding.source_refs[0] + "/revoke",
  );
  expect(revoke.ok()).toBeTruthy();
  await expect(trace).not.toBeVisible({ timeout: 10_000 });
  await expect(
    page.getByRole("button", { name: "查看内容", exact: true }),
  ).toBeDisabled();
});
