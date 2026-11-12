import { test, expect, type Page } from "@playwright/test";

// Contract fixtures exercise browser behavior; they are not execution or model evidence.
const stamp = "2026-11-12T12:00:00Z";
const initial = (overrides: Record<string, unknown> = {}) => ({
  id: "case-synthetic",
  project_id: "project-synthetic",
  title: "合成资料核查",
  goal: "核查合成月度统计并交付表格",
  status: "awaiting_data",
  requirements: [
    {
      id: "monthly-data",
      title: "月度原始资料",
      required: true,
      description: "需要完整月份、数值、单位与来源。",
      formats: ["CSV", "PDF"],
      fields: ["月份", "数值", "来源"],
      status: "missing",
      matched_asset_ids: [],
    },
  ],
  available_assets: [],
  submissions: [],
  plans: [],
  runs: [],
  task_id: null,
  result: null,
  review: null,
  progress: [
    { key: "requirements", label: "明确需求", status: "completed" },
    { key: "data", label: "补充资料", status: "active" },
    { key: "execute", label: "执行与审核", status: "pending" },
    { key: "result", label: "交付结果", status: "pending" },
  ],
  missing: ["请提供月度原始资料"],
  mode: "cloud",
  max_cost_cny: 2,
  public_egress_authorized: false,
  created_at: stamp,
  updated_at: stamp,
  ...overrides,
});

async function install(
  page: Page,
  options: {
    noData?: boolean;
    privateAsset?: boolean;
    asyncRequirements?: boolean;
    script?: boolean;
    existingStatus?: "failed" | "partial" | "cancelled" | "succeeded";
    firstRunPartial?: boolean;
    rejectedPlan?: boolean;
    wideTable?: boolean;
    citationRepair?: boolean;
  } = {},
) {
  let task: any = options.existingStatus
    ? initial({
        status: options.existingStatus,
        task_id: "prior-run",
        missing: [],
        public_egress_authorized: true,
        requirements: [
          {
            id: "monthly-data",
            title: "月度原始资料",
            required: true,
            description: "需提供完整月份",
            formats: ["CSV"],
            fields: ["month", "value"],
            status: "provided",
            matched_asset_ids: [],
          },
        ],
        submissions: [
          {
            id: "prior-submission",
            input_refs: ["prior-asset"],
            created_at: stamp,
          },
        ],
        runs: [
          {
            id: "prior-run",
            status: options.existingStatus,
            created_at: stamp,
            finished_at: stamp,
          },
        ],
        usage: { cost_cny: 0.3, request_count: 2, accounting_complete: true },
        result:
          options.existingStatus === "partial"
            ? {
                answer: "上一轮部分交付，仍缺一月。",
                citations: [],
                tables: [],
                missing: ["仍缺一月"],
              }
            : null,
      })
    : null;
  const posted: { path: string; data: any }[] = [];
  const calls: string[] = [];
  let finished = false;
  let requirementPolls = 0;
  let starts = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const pathname = url.pathname;
    calls.push(pathname);
    const json = (data: unknown, status = 200) =>
      route.fulfill({ status, json: data });
    if (pathname === "/api/auth/me")
      return json({
        id: "requester",
        username: "requester",
        role: "requester",
        name: "合成验收用户",
      });
    if (pathname === "/api/v2/question-projects")
      return json([{ id: "project-synthetic", name: "合成验收项目" }]);
    if (pathname === "/api/v2/task-cases" && request.method() === "GET")
      return json(task ? [task] : []);
    if (pathname === "/api/v2/task-cases" && request.method() === "POST") {
      const data = request.postDataJSON();
      posted.push({ path: pathname, data });
      task = initial({
        goal: data.goal,
        public_egress_authorized: data.public_egress_authorized,
        ...(options.noData
          ? { status: "ready", requirements: [], missing: [] }
          : {}),
        ...(options.privateAsset
          ? {
              available_assets: [
                {
                  id: "local-source",
                  name: "仅本地资料",
                  modality: "text",
                  classification: "local",
                  bytes: 30,
                },
              ],
            }
          : {}),
      });
      if (options.asyncRequirements)
        task = { ...task, status: "analyzing_requirements" };
      return json(task, 201);
    }
    if (pathname === "/api/v2/task-cases/case-synthetic/upload") {
      posted.push({ path: pathname, data: request.postData() });
      task = {
        ...task,
        status: "ready",
        missing: [],
        profiles: [
          {
            asset_id: "uploaded",
            name: "synthetic.csv",
            readable: true,
            record_count: 2,
            fields: ["month", "value"],
          },
        ],
        requirements: task.requirements.map((r: any) => ({
          ...r,
          status: "satisfied",
        })),
        submissions: [
          ...task.submissions,
          {
            id: "submission-file",
            input_refs: ["uploaded"],
            created_at: stamp,
          },
        ],
      };
      return json(task);
    }
    if (pathname === "/api/v2/task-cases/case-synthetic/submissions") {
      const data = request.postDataJSON();
      posted.push({ path: pathname, data });
      task = {
        ...task,
        public_egress_authorized: data.public_egress_authorized,
        ...(options.existingStatus ||
        ["failed", "partial", "cancelled"].includes(task.status)
          ? { status: "ready", task_id: null }
          : {}),
      };
      return json(task);
    }
    if (pathname === "/api/v2/task-cases/case-synthetic/start") {
      posted.push({ path: pathname, data: request.postDataJSON() });
      starts++;
      task = { ...task, status: "running", task_id: `run-synthetic-${starts}` };
      finished = true;
      return json(task);
    }
    if (
      pathname === "/api/v2/task-cases/case-synthetic" &&
      request.method() === "GET"
    ) {
      if (
        options.asyncRequirements &&
        task.status === "analyzing_requirements"
      ) {
        requirementPolls++;
        if (requirementPolls < 2) return json(task);
        task = {
          ...task,
          requirements_analysis: {
            summary: "这是概念解释任务，无需用户提供外部数据。",
            assumptions: ["提供一般说明，不针对具体数据计算。"],
          },
        };
        finished = true;
      }
      if (finished)
        task = {
          ...task,
          status:
            options.firstRunPartial && starts === 1 ? "partial" : "succeeded",
          result: {
            answer: "合成验收结果：共 2 个月，合计 30。",
            tables: [
              {
                title: "合成月度明细",
                columns: options.wideTable
                  ? [
                      "月份",
                      "开始日期",
                      "结束日期",
                      "覆盖",
                      "死亡人数",
                      "受伤人数",
                      "合计",
                      "统计口径",
                    ]
                  : ["月份", "数值"],
                rows: options.wideTable
                  ? [
                      [
                        "2023-01",
                        "2023-01-15",
                        "2023-01-31",
                        "部分月份",
                        3,
                        7,
                        10,
                        "合成演示数据，仅包含已提供日期范围内的记录；部分月份不能作为完整月份比较。",
                      ],
                      [
                        "2023-02",
                        "2023-02-01",
                        "2023-02-28",
                        "完整月份",
                        8,
                        12,
                        20,
                        "合成演示数据，覆盖本月全部日期，按同一统计口径汇总。",
                      ],
                    ]
                  : [
                      ["2023-01", 10],
                      ["2023-02", 20],
                    ],
                total_rows: 2,
              },
            ],
            citations: [
              {
                source: "synthetic.csv",
                location: "第 2–3 行",
                excerpt: "2023-01,10\n2023-02,20",
              },
            ],
            artifacts: options.script
              ? [
                  {
                    id: "legacy-unreviewed-output",
                    name: "old-unreviewed.json",
                    type: "script_output",
                    quality_status: "execution_output",
                    download_url:
                      "/api/v2/task-cases/case-synthetic/sandbox-runs/old-sandbox/outputs/0",
                  },
                ]
              : [],
            ...(options.script
              ? {
                  generated_files: [
                    {
                      name: "summary.json",
                      media_type: "application/json",
                      bytes: 12,
                      checksum: "fixture-final-output-checksum",
                      download_url:
                        "/api/v2/task-cases/case-synthetic/sandbox-runs/final-sandbox/outputs/0",
                      quality_status: "referenced_output",
                      task_review_passed: !(
                        options.firstRunPartial && starts === 1
                      ),
                    },
                  ],
                }
              : {}),
          },
          review: {
            passed: !options.citationRepair,
            verdict: options.citationRepair ? "failed" : "passed",
            checks: [{ name: "合计复算", passed: !options.citationRepair }],
            issues: [],
          },
          trace: {
            ...(options.script
              ? {
                  scripts: [
                    {
                      id: "script-v1",
                      purpose: "读取合成表格并计算合计",
                      code: "print(10 + 20)",
                      revision: 0,
                      checksum: "fixture-checksum",
                    },
                  ],
                  sandbox_runs: [
                    {
                      id: "sandbox",
                      script_version_id: "script-v1",
                      status: "succeeded",
                      returncode: 0,
                      revision: 0,
                      isolation: "synthetic-contract-runner",
                      stdout: "30",
                      outputs: [
                        {
                          name: "summary.csv",
                          preview: "total\n30",
                          preview_truncated: true,
                          bytes: 9,
                          media_type: "text/csv",
                          download_url:
                            "/api/v2/task-cases/case-synthetic/sandbox-runs/sandbox/outputs/0",
                        },
                      ],
                    },
                  ],
                }
              : {}),
            agents: [
              {
                id: "a",
                name: "合成统计核查员",
                version: 1,
                scope: "task",
                instances: 2,
                status: "released",
              },
            ],
            models: [
              {
                model: "synthetic-contract-model",
                requests: 2,
                cost_cny: 0.01,
              },
            ],
            tools: [
              {
                id: "tool",
                capability_id: "stats.reconcile_total",
                status: "completed",
                revision: 1,
                duration_ms: 20,
              },
            ],
            revisions: [
              ...(options.citationRepair
                ? [
                    {
                      revision: 0,
                      action: "citation_span_realign",
                      label: "按唯一逐字原文修正引用边界",
                      repairs: [
                        {
                          citation_index: 0,
                          original: {
                            start: 3,
                            end: 10,
                            source_id: "INTERNAL_SOURCE_MUST_NOT_RENDER",
                            quote: "RAW_QUOTE_MUST_NOT_RENDER",
                          },
                          corrected: {
                            start: 4,
                            end: 11,
                            source_id: "INTERNAL_SOURCE_MUST_NOT_RENDER",
                            quote: "RAW_QUOTE_MUST_NOT_RENDER",
                          },
                        },
                      ],
                      outcome: { passed: true, scope: "source_span_only" },
                    },
                  ]
                : []),
              {
                revision: 1,
                action: "extract_monthly_rows",
                label: "补充读取逐月记录",
                reason: "首轮缺少逐月证据",
                failed_checks: ["month_coverage"],
                rerun_stages: ["prepare", "analyze", "review"],
                reused_nodes: [],
                before: { monthly_rows: 0 },
                after: { monthly_rows: 2 },
              },
            ],
          },
          plans: [
            ...(options.rejectedPlan
              ? [
                  {
                    id: "rejected-plan",
                    version: 1,
                    status: "rejected",
                    attempt: 1,
                    reason: "规划合同校验失败",
                    steps: [
                      {
                        label: "未执行的错误读取",
                        tool_ids: ["invented.reader"],
                      },
                    ],
                    validation_errors: [
                      { step: 1, code: "unknown_tool", id: "invented.reader" },
                    ],
                    raw_response: "RAW_RESPONSE_MUST_NOT_RENDER",
                    proposed_plan: {
                      instructions: "PROPOSED_PLAN_MUST_NOT_RENDER",
                    },
                  },
                ]
              : []),
            {
              id: "plan",
              version: options.rejectedPlan ? 2 : 1,
              status: "frozen",
              ...(options.rejectedPlan
                ? {
                    rejected_attempts: ["rejected-plan"],
                    normalizations: [
                      {
                        step: 1,
                        action: "canonical_tool_id",
                        from: "script_execute",
                        to: "script.execute",
                      },
                      {
                        step: 1,
                        action: "skill_dependency_added",
                        skill_id: "table-analysis",
                        tool_id: "data.read_source",
                      },
                    ],
                    raw_response: "FROZEN_RAW_MUST_NOT_RENDER",
                  }
                : {}),
              reason: "按任务资料生成",
              steps: [
                {
                  id: "analyze",
                  label: "逐月复算",
                  tool_ids: ["stats.reconcile_total"],
                  success_criteria: "月度合计与总数一致",
                },
              ],
            },
          ],
        };
      return json(task);
    }
    return json(
      { detail: { message: "Unexpected request", code: "test_unexpected" } },
      404,
    );
  });
  return { posted, calls };
}

test("任务从创建、资料上传到自动执行，展示真实契约中的结果和定向修订", async ({
  page,
}, testInfo) => {
  const contract = await install(page, { script: true });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("核查合成月度统计并交付表格");
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "这项任务需要的资料" }),
  ).toBeVisible();
  await expect(
    page.getByText("需要完整月份、数值、单位与来源。", { exact: true }),
  ).toBeVisible();
  expect(contract.posted.some((p) => p.path.endsWith("/start"))).toBeFalsy();
  await page.getByLabel("上传任务资料", { exact: true }).setInputFiles({
    name: "synthetic.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("month,value\n2023-01,10\n2023-02,20"),
  });
  await page.getByLabel("这些文件是允许用于本任务的公开资料").check();
  await page
    .getByLabel("授权本任务通过统一网关使用云模型处理任务说明及所选公开资料")
    .check();
  await page.getByRole("button", { name: "提交资料并开始分析" }).click();
  await expect(page.getByRole("heading", { name: "交付结果" })).toBeVisible();
  await expect(
    page.getByText("合成验收结果：共 2 个月，合计 30。", { exact: true }),
  ).toBeVisible();
  const result = page.locator(".taskcase-result");
  await expect(
    result.getByRole("link", { name: "下载 summary.json", exact: true }),
  ).toHaveAttribute(
    "href",
    "/api/v2/task-cases/case-synthetic/sandbox-runs/final-sandbox/outputs/0",
  );
  await expect(
    result.getByText("12 B · application/json", { exact: true }),
  ).toBeVisible();
  await expect(
    result.getByText("最终结果引用的脚本产物", { exact: true }),
  ).toBeVisible();
  await expect(result).not.toContainText("summary.csv");
  await expect(result).not.toContainText("old-unreviewed.json");
  await expect(
    page.locator(".taskcase-content > .taskcase-card").nth(1),
  ).toHaveClass(/taskcase-result/);
  const inputRecord = page.locator(".taskcase-input-record");
  await expect(inputRecord).not.toHaveAttribute("open", "");
  await expect(
    page.getByRole("heading", { name: "这项任务需要的资料", exact: true }),
  ).toBeHidden();
  await inputRecord.locator(":scope > summary").click();
  await expect(
    inputRecord.getByRole("heading", {
      name: "这项任务需要的资料",
      exact: true,
    }),
  ).toBeVisible();
  await inputRecord
    .getByText("已读取资料的结构 · 1 项", { exact: true })
    .click();
  await expect(
    inputRecord.getByText("synthetic.csv", { exact: true }),
  ).toBeVisible();
  await page.getByText("合成月度明细 · 2 行", { exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "2023-01", exact: true }),
  ).toBeVisible();
  await page.getByText("平台如何完成这项任务", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "审核不通过后的针对性调整" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "隔离脚本与执行产物" }),
  ).toBeVisible();
  await page.getByText("查看输出预览", { exact: true }).click();
  await expect(
    page.getByText("仅显示预览，下载查看完整文件。", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("首轮缺少逐月证据", { exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "synthetic-contract-model" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "stats.reconcile_total", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("link", { name: "管理工作台" })).toHaveCount(0);
  expect(contract.posted.map((p) => p.path)).toEqual([
    "/api/v2/task-cases",
    "/api/v2/task-cases/case-synthetic/upload",
    "/api/v2/task-cases/case-synthetic/submissions",
    "/api/v2/task-cases/case-synthetic/start",
  ]);
  expect(contract.posted[1].data).toContain(
    'name="classification"\r\n\r\npublic',
  );
  expect(contract.posted[2].data.public_egress_authorized).toBe(true);
  expect(
    contract.calls.every(
      (p) =>
        p === "/api/auth/me" ||
        p === "/api/v2/question-projects" ||
        p.startsWith("/api/v2/task-cases"),
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("task-case-result.png"),
    fullPage: true,
  });
});

test("异步资料需求分析完成后由服务自动续跑，浏览器只轮询且无需上传", async ({
  page,
}) => {
  const contract = await install(page, {
    noData: true,
    asyncRequirements: true,
  });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("解释平均值与中位数的区别，不需要数据例子");
  await page
    .getByLabel(
      "允许平台通过统一网关，使用云模型处理本任务说明及我明确授权的公开资料。",
    )
    .check();
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(page.locator(".taskcase-overview .taskcase-status")).toHaveText(
    "正在分析资料需求",
  );
  await expect(
    page.getByRole("heading", { name: "交付结果", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("这是概念解释任务，无需用户提供外部数据。", { exact: true }),
  ).toBeHidden();
  await page.getByText("资料需求与输入记录", { exact: true }).click();
  await expect(
    page.getByText("这是概念解释任务，无需用户提供外部数据。", { exact: true }),
  ).toBeVisible();
  expect(contract.posted.map((p) => p.path)).toEqual(["/api/v2/task-cases"]);
});

test("无需参考数据的任务授权后直接执行，不要求上传空资料", async ({ page }) => {
  const contract = await install(page, { noData: true });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("用通俗语言解释中位数和平均数的区别");
  await page
    .getByLabel(
      "允许平台通过统一网关，使用云模型处理本任务说明及我明确授权的公开资料。",
    )
    .check();
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: "交付结果" })).toBeVisible();
  expect(contract.posted.map((p) => p.path)).toEqual([
    "/api/v2/task-cases",
    "/api/v2/task-cases/case-synthetic/start",
  ]);
  expect(contract.posted[0].data).not.toHaveProperty("case_id");
  expect(contract.posted[0].data).not.toHaveProperty("workflow_id");
  expect(contract.posted[0].data).not.toHaveProperty("agent_id");
});

test("被拒绝的计划保持未执行，显示具体纠正和依赖补齐但不渲染原始模型响应", async ({
  page,
}) => {
  await install(page, { noData: true, rejectedPlan: true });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("说明合成统计分析方法");
  await page
    .getByLabel(
      "允许平台通过统一网关，使用云模型处理本任务说明及我明确授权的公开资料。",
    )
    .check();
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: "交付结果" })).toBeVisible();
  await page.getByText("平台如何完成这项任务", { exact: true }).click();
  const rejected = page
    .locator(".taskcase-plan")
    .filter({ hasText: "计划 v1 · 已拒绝 · 未执行" });
  await rejected.locator("summary").click();
  await expect(
    rejected.getByText("步骤 1：工具不在可用目录中 · invented.reader", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    rejected.getByText("未执行的拟定步骤", { exact: true }),
  ).toBeVisible();
  await expect(
    rejected.getByText("计划工具：invented.reader", { exact: true }),
  ).toBeVisible();
  const frozen = page
    .locator(".taskcase-plan")
    .filter({ hasText: "计划 v2 · 已冻结 · 可执行" });
  await expect(
    frozen.getByText("已针对 计划 v1 的拒绝原因重新规划并通过校验。", {
      exact: true,
    }),
  ).toBeVisible();
  await expect(
    frozen.getByText(
      "步骤 1：工具标识规范化：script_execute → script.execute",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    frozen.getByText(
      "步骤 1：补齐技能 table-analysis 所需工具 data.read_source",
      { exact: true },
    ),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "invented.reader", exact: true }),
  ).toHaveCount(0);
  await expect(page.locator("body")).not.toContainText(
    "RAW_RESPONSE_MUST_NOT_RENDER",
  );
  await expect(page.locator("body")).not.toContainText(
    "PROPOSED_PLAN_MUST_NOT_RENDER",
  );
  await expect(page.locator("body")).not.toContainText(
    "FROZEN_RAW_MUST_NOT_RENDER",
  );
});

test("云任务不会将本地资料误当作已授权公开输入，移动端无横向溢出", async ({
  page,
}) => {
  const contract = await install(page, { privateAsset: true });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("分析已登记资料并说明证据来源");
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await page.getByLabel("仅本地资料").check();
  await page
    .getByLabel("授权本任务通过统一网关使用云模型处理任务说明及所选公开资料")
    .check();
  await expect(
    page.getByRole("button", { name: "提交资料并开始分析" }),
  ).toBeDisabled();
  expect(contract.posted).toHaveLength(1);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

for (const status of ["failed", "partial", "cancelled"] as const) {
  test(`${status} 可补充资料继续原任务，原额度和历史不由前端重置`, async ({
    page,
  }) => {
    const contract = await install(page, { existingStatus: status });
    await page.goto("/?project=project-synthetic&task_case=case-synthetic");
    await expect(
      page.getByRole("heading", { name: "补充资料并继续", exact: true }),
    ).toBeVisible();
    await expect(
      page.getByText(/新一轮与历史运行共享本任务累计费用上限/),
    ).toBeVisible();
    if (status === "partial")
      await expect(
        page.getByText("上一轮部分交付，仍缺一月。", { exact: true }),
      ).toBeVisible();
    if (status !== "cancelled") {
      await page.getByLabel("上传任务资料", { exact: true }).setInputFiles({
        name: "additional.csv",
        mimeType: "text/csv",
        buffer: Buffer.from("month,value\n2023-02,20"),
      });
      await page.getByLabel("这些文件是允许用于本任务的公开资料").check();
      await page
        .getByRole("button", { name: "补充资料并继续此任务", exact: true })
        .click();
    } else
      await page
        .getByRole("button", { name: "沿用已提交资料继续", exact: true })
        .click();
    await expect(
      page.locator(".taskcase-overview .taskcase-status"),
    ).toHaveText("已完成");
    expect(
      contract.posted.every((entry) =>
        entry.path.startsWith("/api/v2/task-cases/case-synthetic/"),
      ),
    ).toBe(true);
    const submission = contract.posted.find((entry) =>
      entry.path.endsWith("/submissions"),
    )!;
    expect(submission.data).not.toHaveProperty("max_cost_cny");
    expect(submission.data.input_refs).toEqual([]);
    expect(contract.posted.at(-1)?.path).toBe(
      "/api/v2/task-cases/case-synthetic/start",
    );
    await expect(page.getByLabel("上传任务资料", { exact: true })).toHaveCount(
      0,
    );
  });
}

test("成功任务保持冻结只读，不能从前端重新提交资料", async ({ page }) => {
  const contract = await install(page, { existingStatus: "succeeded" });
  await page.goto("/");
  await expect(page.locator(".taskcase-overview .taskcase-status")).toHaveText(
    "已完成",
  );
  await expect(page.getByLabel("上传任务资料", { exact: true })).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: /继续此任务|沿用已提交资料继续/ }),
  ).toHaveCount(0);
  expect(contract.posted).toEqual([]);
});

test("同一页面部分交付后沿用资料续跑使用新提交标识，不重放上一轮确认", async ({
  page,
}) => {
  const contract = await install(page, { firstRunPartial: true, script: true });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("分析合成月度资料并核验完整覆盖");
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await page.getByLabel("上传任务资料", { exact: true }).setInputFiles({
    name: "synthetic.csv",
    mimeType: "text/csv",
    buffer: Buffer.from("month,value\n2023-01,10"),
  });
  await page.getByLabel("这些文件是允许用于本任务的公开资料").check();
  await page
    .getByLabel("授权本任务通过统一网关使用云模型处理任务说明及所选公开资料")
    .check();
  await page
    .getByRole("button", { name: "提交资料并开始分析", exact: true })
    .click();
  await expect(page.locator(".taskcase-overview .taskcase-status")).toHaveText(
    "部分完成",
  );
  await expect(page.locator(".taskcase-input-record")).toHaveCount(0);
  await expect(
    page.getByRole("heading", { name: "补充资料并继续", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "部分交付文件", exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".taskcase-result")
      .getByText("最终结果引用的脚本产物 · 部分交付", { exact: true }),
  ).toBeVisible();
  await expect(
    page
      .locator(".taskcase-result")
      .getByRole("link", { name: "下载 summary.json", exact: true }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "沿用已提交资料继续", exact: true })
    .click();
  await expect(page.locator(".taskcase-overview .taskcase-status")).toHaveText(
    "已完成",
  );
  const confirmations = contract.posted.filter((entry) =>
    entry.path.endsWith("/submissions"),
  );
  expect(confirmations).toHaveLength(2);
  expect(confirmations[0].data.input_refs).toEqual(
    confirmations[1].data.input_refs,
  );
  expect(confirmations[0].data.idempotency_key).not.toBe(
    confirmations[1].data.idempotency_key,
  );
  expect(
    contract.posted.filter((entry) => entry.path === "/api/v2/task-cases"),
  ).toHaveLength(1);
});

for (const width of [920, 390]) {
  test(`${width}px 交付宽表在容器内横向滚动，日期与覆盖不竖排且整页不溢出`, async ({
    page,
  }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await install(page, { noData: true, wideTable: true });
    await page.goto("/");
    await page
      .getByLabel("任务目标", { exact: true })
      .fill("展示合成月度交付表用于界面布局验证");
    await page
      .getByLabel(
        "允许平台通过统一网关，使用云模型处理本任务说明及我明确授权的公开资料。",
      )
      .check();
    await page.getByRole("button", { name: "创建任务", exact: true }).click();
    await expect(
      page.getByRole("heading", { name: "交付结果", exact: true }),
    ).toBeVisible();
    await page.getByText("合成月度明细 · 2 行", { exact: true }).click();
    const region = page.getByRole("region", {
      name: "合成月度明细",
      exact: true,
    });
    const layout = await region.evaluate((element) => {
      const table = element.querySelector("table")!;
      const cells = table.querySelectorAll("tbody tr:first-child td");
      return {
        pageWidth: document.documentElement.scrollWidth,
        viewport: innerWidth,
        regionWidth: element.clientWidth,
        contentWidth: element.scrollWidth,
        tableWidth: table.getBoundingClientRect().width,
        compactCells: Array.from(cells)
          .slice(0, 7)
          .map((cell) => getComputedStyle(cell).whiteSpace),
        noteWidth: cells[7].getBoundingClientRect().width,
        noteWrap: getComputedStyle(cells[7]).whiteSpace,
      };
    });
    expect(layout.pageWidth).toBeLessThanOrEqual(layout.viewport);
    expect(layout.contentWidth).toBeGreaterThan(layout.regionWidth);
    expect(layout.tableWidth).toBeGreaterThanOrEqual(900);
    expect(layout.compactCells.every((value) => value === "nowrap")).toBe(true);
    expect(layout.noteWidth).toBeGreaterThanOrEqual(256);
    expect(layout.noteWrap).toBe("normal");
    await region.evaluate((element) => {
      element.scrollLeft = element.scrollWidth;
    });
    expect(
      await region.evaluate((element) => element.scrollLeft),
    ).toBeGreaterThan(0);
    await region.screenshot({
      path: testInfo.outputPath(`wide-table-${width}.png`),
    });
  });
}

test("引用边界修复仅显示位置校正，不能替代未通过的业务审核", async ({
  page,
}) => {
  await install(page, {
    noData: true,
    firstRunPartial: true,
    citationRepair: true,
  });
  await page.goto("/");
  await page
    .getByLabel("任务目标", { exact: true })
    .fill("验证合成引用校正记录与业务审核的区分");
  await page
    .getByLabel(
      "允许平台通过统一网关，使用云模型处理本任务说明及我明确授权的公开资料。",
    )
    .check();
  await page.getByRole("button", { name: "创建任务", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "交付结果", exact: true }),
  ).toBeVisible();
  await page.getByText("平台如何完成这项任务", { exact: true }).click();
  const repair = page.locator(".taskcase-citation-repair");
  await expect(
    repair.getByRole("heading", { name: "引用边界已校正", exact: true }),
  ).toBeVisible();
  await expect(
    repair.getByRole("cell", { name: "3–10", exact: true }),
  ).toBeVisible();
  await expect(
    repair.getByRole("cell", { name: "4–11", exact: true }),
  ).toBeVisible();
  await expect(repair).toContainText("不表示业务结论通过审核");
  await expect(repair.locator("pre")).toHaveCount(0);
  await expect(
    page.getByText("结果尚未通过审核", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText("结果通过自动审核", { exact: true })).toHaveCount(
    0,
  );
  await expect(page.locator("body")).not.toContainText(
    "INTERNAL_SOURCE_MUST_NOT_RENDER",
  );
  await expect(page.locator("body")).not.toContainText(
    "RAW_QUOTE_MUST_NOT_RENDER",
  );
});
