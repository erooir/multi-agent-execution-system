import { expect, test, type Locator, type Page } from "@playwright/test";

const project = { id: "sample-project", name: "合成工作空间", members: ["operator"] };
const other = { ...project, id: "other-project", name: "另一工作空间" };
const user = { id: "operator", username: "operator", role: "operator" };
const profile = { id: "G03", title: "合成海冰序列", installed: true, project_tools_current: true };
const otherCase = { ...profile, id: "G07", title: "合成环境观测" };
const capability = { id: "case_analysis", available: true, modes: ["local", "cloud"], modalities: ["structured"] };
const sample = {
  name: "演示样本任务", goal: "计算合成海冰序列的差值。", input_refs: ["ice-asset"],
  deadline_seconds: 1800, max_cost_cny: 0.8, max_revisions: 1,
  quality: { min_records: 3, min_length: 1, required_fields: ["year"] },
};
const asset = {
  id: "ice-asset", project_id: project.id, name: "合成海冰年表", status: "active", classification: "public",
  expires_at: "2099-01-01T00:00:00Z", modality: "structured", media_type: "application/json",
};
const published = {
  ...profile, edition: "public-casework-v1", workflow_id: "published-workflow",
  inputs: [asset.name], case_request: { name: profile.title, goal: sample.goal, input_refs: [asset.id] },
};
const knowledgeLabel = "允许工具型智能体检索本项目公开知识";
const capabilities = [capability, { id: "text_analysis", available: true, modes: ["cloud"], modalities: ["text"] }];

async function setup(page: Page, options: { offline?: boolean; error?: boolean; published?: boolean } = {}) {
  const lookups: string[] = [];
  let fail = options.error, submissions = 0;
  await page.route("**/api/**", async (route) => {
    const { pathname, searchParams } = new URL(route.request().url());
    const selected = searchParams.get("project_id") === other.id ? other : project;
    if (pathname === "/api/auth/me") return route.fulfill({ json: user });
    if (pathname === "/api/v2/bootstrap") return route.fulfill({ json: {
      user, projects: [project, other], current_project_id: selected.id, capabilities,
      agents: [], workflows: [], tasks: [], data_assets: selected === project ? [asset] : [],
      schedules: [], improvements: [], connectors: [], memories: [], evaluations: [], workers: [],
      stats: {}, system: { offline: !!options.offline }, budget: {},
    } });
    if (pathname === "/api/v2/casework/catalog") return route.fulfill({ json: options.published ? [published] : [profile, otherCase] });
    if (pathname === "/api/v2/capabilities") return route.fulfill({ json: { capabilities, tools: [] } });
    if (pathname === "/api/v2/casework/G03/demo-sample") {
      lookups.push(searchParams.get("project_id") || "");
      if (fail) {
        fail = false;
        return route.fulfill({ status: 503, json: { detail: "暂不可用" } });
      }
      return route.fulfill({ json: selected === project
        ? { available: true, verified_task_id: "verified-task", request: sample }
        : { available: false, verified_task_id: null, request: null },
      });
    }
    if (pathname === "/api/v2/tasks/preflight") {
      return route.fulfill({ json: { ready: true, checks: [], selected_agents: [], warnings: [] } });
    }
    if (route.request().method() === "POST") submissions++;
    if (pathname === "/api/v2/tasks" && route.request().method() === "POST") {
      return route.fulfill({ json: { id: "synthetic-task", status: "accepted" } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  return { lookups, submissions: () => submissions };
}

async function submit(page: Page, form: Locator) {
  const [preflight, task] = await Promise.all([
    page.waitForRequest(r => r.method() === "POST" && r.url().endsWith("/api/v2/tasks/preflight")),
    page.waitForRequest(r => r.method() === "POST" && r.url().endsWith("/api/v2/tasks")),
    form.getByRole("button", { name: "提交并自动执行" }).click(),
  ]);
  expect(task.postDataJSON()).toEqual(preflight.postDataJSON());
  return task.postDataJSON();
}

for (const view of ["casework", "tasks"]) {
  test(`${view} 手动提交新版用例仅使用随包原件`, async ({ page }) => {
    await setup(page, { published: true });
    await page.goto(`/manage?view=${view}&project=${project.id}`);
    const form = page.getByRole("dialog");
    if (view === "casework") {
      await page.locator(".cc-card").filter({ hasText: published.title }).getByRole("button", { name: "创建用例任务" }).click();
    } else {
      await page.getByRole("main").getByRole("button", { name: "新建任务" }).click();
      await form.getByLabel("任务类型").selectOption("text_analysis");
      await form.getByLabel(knowledgeLabel).check();
      await form.getByLabel("任务类型").selectOption("case_analysis");
      await form.getByLabel("分析用例").selectOption(published.id);
    }
    await expect(form.getByLabel("验收范围")).toHaveValue("project");
    await expect(form.getByLabel("执行模式")).toHaveValue("cloud");
    await expect(form.getByLabel(knowledgeLabel)).toHaveCount(0);
    expect(await submit(page, form)).toMatchObject({
      project_id: project.id, case_id: published.id, case_scope: "project", mode: "cloud",
      use_project_knowledge: false, input_refs: [asset.id], workflow_id: published.workflow_id,
    });
  });
}

test("普通模型任务仍可显式启用项目公开知识", async ({ page }) => {
  await setup(page, { published: true });
  await page.goto(`/manage?view=tasks&project=${project.id}`);
  await page.getByRole("main").getByRole("button", { name: "新建任务" }).click();
  const form = page.getByRole("dialog");
  await form.getByLabel("任务类型").selectOption("text_analysis");
  await form.getByLabel("任务名称").fill("合成文本比较");
  await form.getByLabel("任务目标").fill("使用本项目公开文本核对月份。");
  await expect(form.getByLabel(knowledgeLabel)).not.toBeChecked();
  await form.getByLabel(knowledgeLabel).check();
  expect(await submit(page, form)).toMatchObject({ task_type: "text_analysis", mode: "cloud", use_project_knowledge: true });
});

for (const view of ["casework", "tasks"]) {
  test(`${view} 普通创建使用当前项目的可追溯演示样本`, async ({ page }) => {
    const fixture = await setup(page);
    await page.goto(`/manage?view=${view}&project=${project.id}`);
    if (view === "casework") {
      await page.locator(".cc-card").filter({ hasText: profile.title }).getByRole("button", { name: "创建用例任务" }).click();
    } else {
      await page.getByRole("main").getByRole("button", { name: "新建任务" }).click();
      await page.getByLabel("任务类型").selectOption("case_analysis");
      await page.getByLabel("分析用例").selectOption(profile.id);
    }
    const form = page.getByRole("dialog", { name: "提交用例任务" });
    await expect(form.getByRole("link", { name: "查看来源任务" })).toHaveAttribute(
      "href", `/manage?view=tasks&project=${project.id}&task=verified-task`,
    );
    await expect(form.getByLabel("任务目标")).toHaveValue(sample.goal);
    await expect(form.getByLabel("验收范围")).toHaveValue("sample");
    await expect(form.getByLabel("执行模式")).toHaveValue("cloud");
    await expect(form.getByRole("checkbox", { name: /合成海冰年表/ })).toBeChecked();
    const request = page.waitForRequest((r) => r.url().endsWith("/tasks/preflight"));
    await form.getByRole("button", { name: "检查匹配与授权" }).click();
    const payload = (await request).postDataJSON();
    expect(payload).toMatchObject({ ...sample, project_id: project.id, case_id: profile.id,
      case_scope: "sample", mode: "cloud", allow_partial: false, use_project_knowledge: false });
    expect(payload.idempotency_key).toMatch(/^[\da-f-]{36}$/i);
    await expect(form.getByText("预检查通过", { exact: true })).toBeVisible();
    await form.getByLabel("验收范围").selectOption("reference");
    await expect(form.getByLabel("验收范围")).toHaveValue("reference");
    await expect(form.getByRole("link", { name: "查看来源任务" })).toHaveCount(0);
    await expect(form.getByLabel("任务目标")).not.toHaveValue(sample.goal);
    await expect(form.getByRole("checkbox", { name: /合成海冰年表/ })).not.toBeChecked();
    await form.getByLabel("分析用例").selectOption(otherCase.id);
    await expect(form.getByLabel("验收范围")).toHaveValue("reference");
    await expect(form.getByLabel("任务名称")).toHaveValue(otherCase.title);
    expect(fixture.lookups).toEqual([project.id]);
    expect(fixture.submissions()).toBe(0);
  });
}

test("另一项目没有样本且接口错误支持重试，须明确选择自定义资料", async ({ page }) => {
  const fixture = await setup(page, { error: true });
  await page.goto(`/manage?view=casework&project=${other.id}`);
  await page.locator(".cc-card").filter({ hasText: profile.title }).getByRole("button", { name: "创建用例任务" }).click();
  const form = page.getByRole("dialog");
  await expect(form.getByRole("alert")).toContainText("演示样本读取失败");
  await form.getByRole("button", { name: "重试" }).click();
  await expect(form.getByText(/本项目还没有可复用/)).toBeVisible();
  await expect(form.getByRole("button", { name: "提交并自动执行" })).toHaveCount(0);
  await form.getByRole("button", { name: "自定义项目资料" }).click();
  await expect(form.getByLabel("验收范围")).toHaveValue("project");
  await expect(form.getByRole("checkbox", { name: /合成海冰年表/ })).toHaveCount(0);
  expect(fixture.lookups).toEqual([other.id, other.id]);
});

test("更换场景后选择自定义资料，迟到的样本响应不能覆盖表单", async ({ page }) => {
  await setup(page);
  let release!: () => void, completed!: () => void;
  const delayed = new Promise<void>((resolve) => { release = resolve; });
  const finished = new Promise<void>((resolve) => { completed = resolve; });
  await page.route("**/casework/G07/demo-sample?**", async (route) => {
    await delayed;
    try {
      await route.fulfill({ json: { available: true, verified_task_id: "other-task", request: sample } });
    } finally {
      completed();
    }
  });
  try {
    await page.goto(`/manage?view=casework&project=${project.id}`);
    await page.locator(".cc-card").filter({ hasText: profile.title }).getByRole("button", { name: "创建用例任务" }).click();
    const form = page.getByRole("dialog");
    await expect(form.getByLabel("任务目标")).toHaveValue(sample.goal);
    const requested = page.waitForRequest((r) => new URL(r.url()).pathname.endsWith("/G07/demo-sample"));
    await form.getByLabel("分析用例").selectOption(otherCase.id);
    await requested;
    await form.getByRole("button", { name: "自定义项目资料" }).click();
    await form.getByLabel("任务目标").fill("核查新的合成环境观测。");
    release();
    await finished;
    await expect(form.getByLabel("分析用例")).toHaveValue(otherCase.id);
    await expect(form.getByLabel("验收范围")).toHaveValue("project");
    await expect(form.getByLabel("任务目标")).toHaveValue("核查新的合成环境观测。");
    await expect(form.getByRole("checkbox", { name: /合成海冰年表/ })).not.toBeChecked();
    await expect(form.getByRole("link", { name: "查看来源任务" })).toHaveCount(0);
  } finally {
    release();
  }
});

test("离线部署可明确选择参考批次和本地演练", async ({ page }) => {
  const fixture = await setup(page, { offline: true });
  await page.goto(`/manage?view=casework&project=${project.id}`);
  await page.locator(".cc-card").filter({ hasText: profile.title }).getByRole("button", { name: "创建用例任务" }).click();
  const form = page.getByRole("dialog");
  await expect(form.getByText(/当前为离线部署/)).toBeVisible();
  await form.getByRole("button", { name: "使用参考批次" }).click();
  await expect(form.getByLabel("验收范围")).toHaveValue("reference");
  await expect(form.getByLabel("执行模式")).toHaveValue("local");
  await expect(form.getByLabel("执行模式").locator('option[value="cloud"]')).toHaveJSProperty("disabled", true);
  expect(fixture.lookups).toEqual([]);
});
