import { test, expect, type Page } from "@playwright/test";

async function management(page: Page) {
  const user = {
    id: "u",
    username: "admin",
    name: "合成管理员",
    role: "admin",
  };
  const project = { id: "p", name: "候选界面契约项目", members: ["u"] };
  const cases = [
    {
      id: "source",
      title: "来源任务",
      status: "succeeded",
      candidate_eligible: true,
      evaluation_eligible: true,
    },
    {
      id: "dev",
      title: "独立开发任务",
      status: "succeeded",
      candidate_eligible: true,
      evaluation_eligible: true,
    },
    {
      id: "holdout",
      title: "独立留出任务",
      status: "failed",
      candidate_eligible: false,
      evaluation_eligible: true,
    },
  ];
  let candidate: any = null,
    evaluations = 0;
  const mutations: { path: string; body: any }[] = [];
  await page.route("**/api/**", async (route) => {
    const request = route.request(),
      path = new URL(request.url()).pathname;
    const json = (value: unknown, status = 200) =>
      route.fulfill({ status, json: value });
    if (path === "/api/auth/me") return json(user);
    if (path === "/api/v2/bootstrap")
      return json({
        user,
        projects: [project],
        current_project_id: "p",
        agents: [
          {
            id: "scoped",
            name: "本任务分析员",
            project_id: "p",
            scope: "task",
            task_case_id: "source",
            status: "published",
            current_version: 1,
            capabilities: ["autonomous_analysis"],
            modality: ["text"],
            structure: "atomic",
            execution_strategy: "tool_agent",
          },
          {
            id: "evaluation",
            name: "独立评测分析员",
            project_id: "p",
            scope: "evaluation",
            status: "published",
            current_version: 1,
            capabilities: ["autonomous_analysis"],
            modality: ["text"],
            structure: "atomic",
            execution_strategy: "tool_agent",
          },
        ],
        workflows: [],
        tasks: [],
        data_assets: [],
        schedules: [],
        improvements: [],
        connectors: [],
        capabilities: [],
        workers: [],
        budget: {},
        stats: {},
        system: {},
        memories: [],
        evaluations: [],
      });
    if (path === "/api/v2/capabilities")
      return json({
        tools: [
          {
            id: "data.profile",
            name: "data_profile",
            label: "资料结构分析",
            version: 1,
            input_schema: { properties: {} },
          },
          {
            id: "script.execute",
            name: "script_execute",
            label: "隔离执行脚本",
            version: 1,
            input_schema: { properties: {} },
          },
        ],
        skills: [],
        mcp_servers: [],
        readiness: {
          tools: {
            "data.profile": {
              available: true,
              status: "ready",
              reason: "依赖已安装",
            },
            "script.execute": {
              available: false,
              status: "unavailable",
              reason: "合成验收：隔离执行镜像尚未配置",
            },
          },
          skills: {},
        },
      });
    if (path === "/api/v2/metrics")
      return json({ detail: { message: "No synthetic metrics fixture" } }, 503);
    if (/\/api\/v2\/agents\/[^/]+\/(versions|instances)$/.test(path))
      return json([]);
    if (path === "/api/v2/task-cases") return json(cases);
    if (path === "/api/v2/agent-candidates")
      return json(candidate ? [candidate] : []);
    if (path === "/api/v2/task-cases/source/candidates") {
      mutations.push({ path, body: request.postDataJSON() });
      candidate = {
        id: "candidate",
        project_id: "p",
        name: "合成工具流程候选",
        status: "candidate",
        source_task_case_id: "source",
        source_task_id: "source-run",
        steps: [
          {
            name: "提取与复核",
            tool_ids: ["data.profile"],
            skill_ids: ["evidence-analysis"],
          },
        ],
        limitations: ["独立重放通过前仅是候选"],
        checksum: "synthetic-contract",
        evaluation: null,
      };
      return json(candidate);
    }
    if (path === "/api/v2/agent-candidates/candidate/evaluate") {
      mutations.push({ path, body: request.postDataJSON() });
      evaluations++;
      const passed = evaluations > 1;
      candidate = {
        ...candidate,
        status: passed ? "evaluated" : "candidate",
        evaluation_verdict: passed ? "passed" : "failed",
        evaluation: {
          id: "evaluation-" + evaluations,
          status: "completed",
          verdict: passed ? "passed" : "failed",
          checks: [{ name: "all_candidate_quality_passed", passed }],
          samples: [
            {
              task_case_id: "dev",
              split: "development",
              baseline: "base-dev",
              candidate: "cand-dev",
            },
            {
              task_case_id: "holdout",
              split: "holdout",
              baseline: "base-holdout",
              candidate: "cand-holdout",
            },
          ],
          observations: [
            {
              task_case_id: "dev",
              results: {
                baseline: {
                  passed: true,
                  cost_cny: 0.03,
                  hard_failures: 0,
                  accounting_complete: true,
                },
                candidate: {
                  passed,
                  cost_cny: 0.02,
                  hard_failures: passed ? 0 : 1,
                  accounting_complete: true,
                },
              },
            },
          ],
        },
      };
      return json(candidate);
    }
    if (path === "/api/v2/agent-candidates/candidate/promote") {
      mutations.push({ path, body: request.postDataJSON() });
      candidate = { ...candidate, status: "promoted" };
      return json(candidate);
    }
    if (path === "/api/v2/agent-candidates/candidate") return json(candidate);
    return json({ detail: { message: "Unexpected fixture request" } }, 404);
  });
  await page.goto("/manage");
  await expect(page.getByLabel("当前项目")).toHaveValue("p");
  return mutations;
}

test("任务专属与独立评测快照不冒充复用资产，工具缺依赖明确展示", async ({
  page,
}) => {
  await management(page);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "智能体资产", exact: true })
    .click();
  await expect(
    page
      .locator(".agent-registry-counts")
      .getByText("可复用 · 已发布", { exact: true })
      .locator(".."),
  ).toContainText("0");
  await page.getByLabel("智能体使用范围").selectOption("task");
  await expect(page.locator(".asset-card")).toHaveCount(1);
  await page.locator(".asset-card").click();
  const dialog = page.getByRole("dialog");
  await expect(
    dialog.getByRole("heading", { name: "任务专属智能体" }),
  ).toBeVisible();
  await expect(
    dialog.getByRole("button", { name: "编辑定义", exact: true }),
  ).toHaveCount(0);
  await page.keyboard.press("Escape");
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "工具箱", exact: true })
    .click();
  await expect(page.getByText(/当前部署已就绪 1 \/ 2 项/)).toBeVisible();
  await page.getByLabel("工具分类").selectOption("scripts");
  await expect(
    page.getByText("当前部署尚不可用", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("合成验收：隔离执行镜像尚未配置", { exact: true }),
  ).toBeVisible();
});

test("候选独立评测未通过时不能晋升，真实契约通过后才出现入库操作", async ({
  page,
}) => {
  const mutations = await management(page);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "效果评测", exact: true })
    .click();
  await page.getByLabel("候选来源任务").selectOption("source");
  await page.getByRole("button", { name: "提取去数据化候选" }).click();
  await expect(
    page
      .locator(".candidate-detail")
      .getByRole("heading", { name: "合成工具流程候选", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "运行真实对照评测" }),
  ).toBeDisabled();
  await page.getByRole("checkbox", { name: /^独立开发任务/ }).check();
  await expect(
    page.getByRole("button", { name: "运行真实对照评测" }),
  ).toBeDisabled();
  await expect(
    page.getByRole("checkbox", { name: /^独立留出任务 未能完成/ }),
  ).toBeVisible();
  await expect(
    page
      .getByLabel("候选来源任务")
      .getByRole("option", { name: "独立留出任务", exact: true }),
  ).toHaveCount(0);
  await page.getByRole("checkbox", { name: /^独立留出任务/ }).check();
  await page.getByRole("button", { name: "运行真实对照评测" }).click();
  await expect(
    page.getByText("评测未通过，候选仍不能进入复用库。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "将通过评测的候选加入复用库" }),
  ).toHaveCount(0);
  await expect(page.getByText("硬性问题：1 项", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: /^独立开发任务/ }).check();
  await page.getByRole("checkbox", { name: /^独立留出任务/ }).check();
  await page.getByRole("button", { name: "运行真实对照评测" }).click();
  await expect(
    page.getByRole("button", { name: "将通过评测的候选加入复用库" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "将通过评测的候选加入复用库" })
    .click();
  await expect(
    page.getByText("已入库。新任务可匹配这一能力，既有任务保留原快照。", {
      exact: true,
    }),
  ).toBeVisible();
  expect(
    mutations.filter((r) => r.path.endsWith("/evaluate")).map((r) => r.body),
  ).toEqual([
    { task_case_ids: ["dev", "holdout"], max_cost_cny: 4 },
    { task_case_ids: ["dev", "holdout"], max_cost_cny: 4 },
  ]);
  expect(mutations.at(-1)?.path).toBe(
    "/api/v2/agent-candidates/candidate/promote",
  );
});
