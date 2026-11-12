import { test, expect, type Page } from "@playwright/test";

// Synthetic API contracts only: these tests never create a real task or charge.
const projectId = "target-project";
const entry = `/?project=${projectId}`;

async function management(page: Page) {
  const user = {
    id: "u",
    username: "admin",
    name: "合成管理员",
    role: "admin",
  };
  const projects = [
    { id: "other-project", name: "其他项目", members: ["u"] },
    { id: projectId, name: "入口契约项目", members: ["u"] },
  ];
  const request = {
    project_id: projectId,
    name: "普通任务记录",
    goal: "整理合成表格",
    task_type: "structured_transform",
    mode: "local",
    input_refs: ["asset"],
    service_class: "batch",
    deadline_seconds: 300,
    max_cost_cny: 0,
    max_revisions: 1,
    quality: { min_records: 1, min_length: 1, required_fields: [] },
  };
  const run = (id: string, body: any) => ({
    id,
    project_id: projectId,
    name: body.name,
    task_type: body.task_type,
    status: "succeeded",
    request: body,
    created_at: "2026-11-13T00:00:00Z",
    updated_at: "2026-11-13T00:00:01Z",
    finished_at: "2026-11-13T00:00:01Z",
    nodes: [],
    artifacts: [],
    events: [],
    instances: [],
    tool_calls: [],
    snapshot: {},
    result: {},
  });
  const tasks = [
    run("ordinary", request),
    run("autonomous", {
      ...request,
      name: "自主任务记录",
      task_type: "autonomous_analysis",
      mode: "cloud",
      task_case_id: "source-case",
    }),
  ];
  const mutations: { path: string; body: any }[] = [];
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const json = (value: unknown, status = 200) =>
      route.fulfill({ status, json: value });
    if (req.method() === "POST") {
      const body = req.postDataJSON();
      mutations.push({ path, body });
      if (path === "/api/v2/tasks/preflight")
        return json({ selected_agents: [] });
      if (path === "/api/v2/tasks") {
        const task = run("created-" + tasks.length, body);
        tasks.push(task);
        return json(task);
      }
      if (path === "/api/v2/schedules")
        return json({ ...body, id: "schedule" });
      return json(
        { detail: { message: "Unexpected synthetic mutation" } },
        400,
      );
    }
    if (path === "/api/auth/me") return json(user);
    if (path === "/api/v2/question-projects") return json(projects);
    if (path === "/api/v2/task-cases") return json([]);
    if (path === "/api/v2/bootstrap")
      return json({
        user,
        projects,
        current_project_id: projectId,
        tasks,
        agents: [],
        workflows: [],
        schedules: [],
        improvements: [],
        connectors: [],
        capabilities: [],
        workers: [],
        memories: [],
        evaluations: [],
        budget: {},
        stats: {},
        system: {},
        data_assets: [
          {
            id: "asset",
            project_id: projectId,
            name: "合成表格",
            modality: "structured",
            classification: "local",
            status: "active",
            expires_at: "2099-01-01T00:00:00Z",
          },
        ],
      });
    if (path === "/api/v2/capabilities")
      return json({
        capabilities: [
          {
            id: "structured_transform",
            available: true,
            modes: ["local"],
            modalities: ["structured"],
          },
          {
            id: "autonomous_analysis",
            available: true,
            modes: ["cloud"],
            modalities: ["text", "structured"],
          },
        ],
        tools: [],
        skills: [],
        mcp_servers: [],
      });
    const task = tasks.find((item) => path === "/api/v2/tasks/" + item.id);
    if (task) return json(task);
    return json({ detail: { message: "No synthetic fixture" } }, 404);
  });
  await page.goto("/manage");
  await expect(page.getByLabel("当前项目")).toHaveValue(projectId);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "任务中心", exact: true })
    .click();
  return mutations;
}

test("旧手工和计划仅提供支持的类型，普通任务仍走原接口并提供同项目新入口", async ({
  page,
}) => {
  const mutations = await management(page);
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("任务类型")).toHaveValue(
    "structured_transform",
  );
  await expect(
    dialog.locator('option[value="autonomous_analysis"]'),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("link", { name: "前往任务工作台新建" }),
  ).toHaveAttribute("href", entry);
  await dialog.getByLabel("任务名称", { exact: true }).fill("普通手工任务");
  await dialog.getByLabel("任务目标", { exact: true }).fill("整理合成表格");
  await dialog.getByRole("checkbox", { name: /合成表格/ }).check();
  await dialog.getByRole("button", { name: "提交并自动执行" }).click();
  await expect(
    page.getByRole("heading", { name: "普通手工任务", exact: true }),
  ).toBeVisible();
  expect(mutations.map((item) => item.path)).toEqual([
    "/api/v2/tasks/preflight",
    "/api/v2/tasks",
  ]);
  expect(mutations[1].body).toMatchObject({
    project_id: projectId,
    task_type: "structured_transform",
    input_refs: ["asset"],
  });

  await page.getByRole("button", { name: "返回任务列表", exact: true }).click();
  await page.getByRole("button", { name: "创建计划", exact: true }).click();
  await expect(
    dialog.locator('option[value="autonomous_analysis"]'),
  ).toHaveCount(0);
  await expect(
    dialog.getByRole("link", { name: "前往任务工作台新建" }),
  ).toHaveAttribute("href", entry);
  await dialog.getByLabel("任务名称", { exact: true }).fill("普通计划任务");
  await dialog.getByLabel("任务目标", { exact: true }).fill("定期整理合成表格");
  await dialog.getByRole("checkbox", { name: /合成表格/ }).check();
  await dialog.getByRole("button", { name: "创建计划", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(mutations.map((item) => item.path)).toEqual([
    "/api/v2/tasks/preflight",
    "/api/v2/tasks",
    "/api/v2/tasks/preflight",
    "/api/v2/schedules",
  ]);
  expect(mutations[3].body.task_template).toMatchObject({
    project_id: projectId,
    task_type: "structured_transform",
  });
});

test("自主任务详情转入同项目工作台，不复制到旧提交接口或默默变更类型", async ({
  page,
}) => {
  const mutations = await management(page);
  await page.getByRole("button", { name: "自主任务记录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "自主任务记录", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "复制为新任务", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByText("自主任务分析", { exact: true })).toBeVisible();
  const link = page.getByRole("link", { name: "前往任务工作台新建" });
  await expect(link).toHaveAttribute("href", entry);
  await link.click();
  await expect(page).toHaveURL(new RegExp("/\\?project=" + projectId + "$"));
  await expect(page.getByLabel("当前项目")).toHaveValue(projectId);
  await expect(
    page.getByRole("button", { name: "新建任务", exact: true }),
  ).toBeVisible();
  expect(mutations).toEqual([]);
});

test("普通任务复制保留类型和资料，仍可通过旧提交接口创建", async ({ page }) => {
  const mutations = await management(page);
  await page.getByRole("button", { name: "普通任务记录", exact: true }).click();
  await page.getByRole("button", { name: "复制为新任务", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("任务类型")).toHaveValue(
    "structured_transform",
  );
  await expect(dialog.getByLabel("任务名称", { exact: true })).toHaveValue(
    "普通任务记录（重试）",
  );
  await expect(
    dialog.getByRole("checkbox", { name: /合成表格/ }),
  ).toBeChecked();
  await dialog.getByRole("button", { name: "提交并自动执行" }).click();
  await expect(
    page.getByRole("heading", { name: "普通任务记录（重试）", exact: true }),
  ).toBeVisible();
  expect(mutations.map((item) => item.path)).toEqual([
    "/api/v2/tasks/preflight",
    "/api/v2/tasks",
  ]);
  expect(mutations[1].body).toMatchObject({
    project_id: projectId,
    task_type: "structured_transform",
    input_refs: ["asset"],
  });
});
