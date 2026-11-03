import { test, expect } from "@playwright/test";

test("任务详情展示条款匹配改道、复用的解析节点和独立复核结果", async ({
  page,
}) => {
  const login = await page.request.post("/api/auth/login", {
    data: {
      username: process.env.HANGZHI_SMOKE_USERNAME || "admin",
      password:
        process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password",
    },
  });
  expect(login.ok()).toBeTruthy();
  const post = async (path: string, data: Record<string, unknown>) => {
    const res = await page.request.post("/api/v2/" + path, { data });
    expect(res.ok()).toBeTruthy();
    return res.json();
  };
  const p = await post("projects", { name: "条款修复验收-" + Date.now() });
  await post("casework/install?project_id=" + p.id, {});
  const a = await post("data-assets", {
    project_id: p.id,
    name: "合成条款",
    source_id: "fixture",
    provenance: "synthetic",
    modality: "structured",
    media_type: "application/json",
    content: [
      {
        side: "before",
        clause_id: "1",
        topic: "交付期限",
        clause_text: "交付记录。",
      },
      {
        side: "after",
        clause_id: "3",
        topic: "交付期限",
        clause_text: "交付记录并注明日期。",
      },
    ],
  });
  await post("tasks", {
    project_id: p.id,
    name: "核对两版条款",
    goal: "给出有双边证据的对应关系",
    input_refs: [a.id],
    case_id: "T06",
    case_scope: "sample",
    task_type: "case_analysis",
    idempotency_key: "repair",
    allow_partial: false,
    max_revisions: 2,
  });
  await page.goto("/manage");
  await page.getByLabel("当前项目").selectOption(p.id);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "任务运行" })
    .click();
  await page.getByRole("button", { name: "核对两版条款", exact: true }).click();
  await expect(page.locator(".task-strip .badge")).toHaveText("成功", {
    timeout: 30_000,
  });
  await page.getByRole("button", { name: "执行图与实例", exact: true }).click();
  const record = page.locator(".case-repair").first();
  await expect(record).toContainText("按唯一主题核对未匹配条款");
  await expect(record).toContainText("交付期限");
  await expect(record).toContainText("已解决");
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "复用第 1 轮" }),
  ).toHaveCount(1);
  await page.getByRole("button", { name: "审核与迭代" }).click();
  const history = page.locator(".trace-review-row");
  await expect(history.filter({ hasText: "第 1 轮" })).toContainText("未通过");
  await expect(history.filter({ hasText: "第 2 轮" })).toContainText("通过");
  await page.getByRole("button", { name: "查看问题发生轮次" }).click();
  await expect(page.getByLabel("执行轮次")).toHaveValue("0");
  await expect(page.locator(".react-flow__node")).toHaveCount(3);
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "复用第 1 轮" }),
  ).toHaveCount(0);
  await page.getByLabel("执行轮次").selectOption("1");
  await expect(
    page.locator(".react-flow__node").filter({ hasText: "复用第 1 轮" }),
  ).toHaveCount(1);
});
