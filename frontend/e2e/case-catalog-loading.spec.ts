import { expect, test } from "@playwright/test";

test("场景目录并行载入，工具说明延迟时仍可打开用例任务", async ({ page }) => {
  const user = { id: "operator", role: "operator", username: "operator" };
  const project = { id: "fixture-project", name: "合成项目", members: [user.id] };
  const capability = {
    id: "case_analysis", available: true, description: "合成本地分析",
    modes: ["local"], modalities: ["structured"],
  };
  const profile = {
    id: "G03", title: "合成年度序列", project_tools_current: true, installed: true,
    role_contract: { stage: "analyze", operation: "fixture", grants: ["fixture.tool"] },
  };
  let catalogRequests = 0, toolRequests = 0;
  let finishBootstrap!: () => void, finishTools!: () => void;
  const bootstrapReady = new Promise<void>((resolve) => { finishBootstrap = resolve; });
  const toolsReady = new Promise<void>((resolve) => { finishTools = resolve; });
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: user });
    if (url.searchParams.get("project_id") !== project.id) return route.fulfill({ status: 403, json: {} });
    if (url.pathname === "/api/v2/bootstrap") {
      await bootstrapReady;
      return route.fulfill({ json: {
        user, projects: [project], current_project_id: project.id, capabilities: [capability],
        agents: [], workflows: [], tasks: [], data_assets: [], schedules: [], improvements: [],
        connectors: [], memories: [], evaluations: [], workers: [], stats: {}, system: {}, budget: {},
      } });
    }
    if (url.pathname === "/api/v2/casework/catalog") {
      catalogRequests++;
      return route.fulfill({ json: [profile] });
    }
    if (url.pathname === "/api/v2/casework/G03/demo-sample") {
      return route.fulfill({ json: { available: false, verified_task_id: null, request: null } });
    }
    if (url.pathname === "/api/v2/capabilities") {
      toolRequests++;
      await toolsReady;
      return route.fulfill({ json: { capabilities: [capability], tools: [{ id: "fixture.tool", label: "合成核对工具" }] } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  try {
    await page.goto("/manage?view=casework&project=" + project.id);
    await expect.poll(() => catalogRequests).toBe(1);
    finishBootstrap();
    const card = page.locator(".cc-card").filter({ hasText: profile.title });
    await expect(card).toBeVisible();
    expect(toolRequests).toBe(0);
    await card.getByText("职责、共用算法与授权清单", { exact: true }).click();
    await expect.poll(() => toolRequests).toBe(1);
    await card.getByRole("button", { name: "创建用例任务" }).click();
    const form = page.getByRole("dialog", { name: "提交用例任务" });
    await expect(form.getByText("本项目还没有可复用的已验证演示样本。")).toBeVisible();
    await form.getByRole("button", { name: "自定义项目资料" }).click();
    await expect(form.getByLabel("分析用例")).toHaveValue(profile.id);
    await expect(form.getByLabel("任务类型").locator('option[value="case_analysis"]')).toBeEnabled();
    await form.getByRole("button", { name: "关闭对话框" }).click();
    finishTools();
    await expect(card.getByText("合成核对工具", { exact: true })).toBeVisible();
    expect(catalogRequests).toBe(1);
    expect(toolRequests).toBe(1);
  } finally {
    finishBootstrap();
    finishTools();
  }
});
