import { test, expect } from "@playwright/test";
import { adminSession } from "./admin-session";

test("用例岗位对照显示共享算法和不同权限，当前专员可执行已授权的本地证据工具", async ({
  page,
}) => {
  test.setTimeout(90_000);
  const login = await adminSession(page.context());
  expect(login.ok()).toBeTruthy();
  const post = async (path: string, data: Record<string, unknown>) => {
    const res = await page.request.post("/api/v2/" + path, { data });
    expect(res.ok()).toBeTruthy();
    return res.json();
  };
  const p = await post("projects", { name: "用例岗位工具验收-" + Date.now() });
  await post("data-assets", {
    project_id: p.id,
    name: "合成科研定义",
    modality: "text",
    media_type: "text/plain",
    content:
      "Measured 18 ppm. Particle size distributions (PSDs) were recorded.",
    source_id: "local-science-example",
    provenance: "synthetic",
    classification: "local",
  });
  await page.goto("/manage");
  await page.getByLabel("当前项目").selectOption(p.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "场景分析" })
    .click();
  const installed = page.waitForResponse(
    (r) =>
      r.url().includes("/api/v2/casework/install?") &&
      r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "发布本项目用例岗位与流程", exact: true })
    .click();
  expect((await installed).ok()).toBeTruthy();
  await expect(
    page.getByRole("button", { name: "发布本项目用例岗位与流程", exact: true }),
  ).not.toBeVisible({ timeout: 30_000 });
  await page.getByRole("button", { name: "用例岗位对照", exact: true }).click();
  await expect(page.locator(".role-conclusion strong")).toHaveText(
    "共用同一主算法",
  );
  await expect(page.locator(".role-column:visible")).toHaveCount(2);
  await expect(page.locator(".role-column:visible").first()).toContainText(
    "evidence.compare_passages",
  );
  await expect(page.locator(".role-column:visible").first()).toContainText(
    "当前发布流程已绑定用例岗位授权",
  );
  await page
    .getByRole("button", { name: "地理计算 · G03 / G09", exact: true })
    .click();
  await expect(page.locator(".role-conclusion strong")).toHaveText(
    "主算法不同",
  );
  await expect(page.locator(".role-column:visible").first()).toContainText(
    "series.baseline_anomaly",
  );
  await expect(page.locator(".role-column:visible").last()).toContainText(
    "geo.route_detour",
  );

  const all = await (await page.request.get("/api/v2/agents")).json();
  const specialist = all.find(
    (a: Record<string, any>) =>
      a.project_id === p.id &&
      a.role_contract?.case_id === "T01" &&
      a.role_contract?.stage === "analyze",
  );
  expect(specialist).toBeTruthy();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "智能体资产" })
    .click();
  await page.getByLabel("执行方式筛选").selectOption("dual");
  await page.getByLabel("用例岗位分工").selectOption("analyze");
  await page.getByLabel("发布状态").selectOption("published");
  await page.getByLabel("搜索智能体").fill("T01");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await page
    .locator(".asset-card")
    .filter({ hasText: specialist.name })
    .click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toContainText("已发布用例岗位合同");
  await expect(dlg).toContainText(specialist.executor_bindings.local.id);
  await expect(dlg).toContainText(specialist.executor_bindings.cloud.id);
  await dlg
    .locator("summary")
    .filter({ hasText: "试用此版本的本地工具" })
    .click();
  await dlg.getByLabel("试用工具").selectOption("evidence.list_definitions");
  await dlg.getByRole("checkbox", { name: /合成科研定义/ }).check();
  await dlg.getByLabel(/^缩略语/).fill("PSD");
  const args = dlg.getByLabel("调用参数（JSON）");
  const data = JSON.parse(await args.inputValue());
  expect(data.source_id).toBeTruthy();
  expect(data.acronym).toBe("PSD");
  await args.fill(JSON.stringify({ ...data, acronym: "PSDs" }));
  await dlg.getByRole("button", { name: "执行本地试用" }).click();
  await expect(dlg.locator(".tool-result")).toContainText(
    "Particle size distributions (PSDs)",
  );
});
