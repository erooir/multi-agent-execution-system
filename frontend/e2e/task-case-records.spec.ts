import { test, expect, type Page } from "@playwright/test";

// Synthetic browser contracts. These fixtures never create or delete real tasks.
const goal =
  "📋请依据上传的联合国公开报告，整理统计期内每月的平民死亡、受伤及合计人数，区分完整月和部分月，复算月度加总与报告总数是否一致，并说明统计范围、核验限制。每项结论注明原件页码；若正文缺少月度数值，请检查报告图表，不得按总数倒推月份数据。";
const makeTask = (id: string, status = "awaiting_data") => ({
  id,
  project_id: "project",
  title: Array.from(goal).slice(0, 80).join(""),
  goal,
  status,
  mode: "cloud",
  max_cost_cny: 2,
  public_egress_authorized: false,
  requirements: [],
  progress: [],
  created_at: "2026-11-13T00:00:00Z",
  updated_at: "2026-11-13T00:00:00Z",
});

async function records(
  page: Page,
  initial = [makeTask("first")],
  rejectFirst = false,
) {
  let rows = initial;
  let deletionCount = 0;
  await page.route("**/api/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const json = (value: unknown, status = 200) =>
      route.fulfill({ json: value, status });
    if (url.pathname === "/api/auth/me")
      return json({ id: "owner", username: "owner", role: "requester" });
    if (url.pathname === "/api/v2/question-projects")
      return json([{ id: "project", name: "新任务工作空间" }]);
    if (url.pathname === "/api/v2/task-cases") return json(rows);
    const id = url.pathname.match(/^\/api\/v2\/task-cases\/([^/]+)$/)?.[1];
    if (id && request.method() === "DELETE") {
      deletionCount++;
      if (rejectFirst && deletionCount === 1)
        return json(
          {
            detail: {
              code: "task_case_cleanup_pending",
              message: "cleanup pending",
            },
          },
          409,
        );
      rows = rows.filter((row) => row.id !== id);
      return route.fulfill({ status: 204 });
    }
    if (id && request.method() === "GET") {
      const row = rows.find((task) => task.id === id);
      return row ? json(row) : json({ detail: "not found" }, 404);
    }
    return json({ detail: "Unexpected synthetic request" }, 400);
  });
  return {
    get deletionCount() {
      return deletionCount;
    },
  };
}

test("资料准备显示完整目标，去除重复说明并保留新名称", async ({ page }) => {
  await records(page);
  await page.goto("/?project=project");
  const overview = page.locator(".taskcase-overview");
  await expect(
    overview.getByRole("heading", { name: goal, exact: true }),
  ).toBeVisible();
  await expect(overview.locator(".taskcase-goal")).toHaveCount(1);
  await expect(page.locator(".taskcase-history strong")).toHaveAttribute(
    "title",
    goal,
  );
  await expect(page.getByText("上游提交资料", { exact: true })).toBeVisible();
  await expect(
    page.getByText("规划、执行与审核记录", { exact: true }),
  ).toHaveCount(1);
  await expect(page.locator("body")).not.toContainText("平台如何完成这项任务");
  await expect(page.locator("body")).not.toContainText(
    "后续的规划、执行与审核由平台自动完成",
  );
  await page.setViewportSize({ width: 390, height: 844 });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await expect(
    overview.getByRole("heading", { name: goal, exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await page.getByText("执行设置", { exact: true }).click();
  for (const removed of [
    "从问题到可核验的交付",
    "描述你希望完成的工作，平台会判断所需资料，安排智能体和工具，并自动审核结果。",
    "先说明目标。平台会告诉你是否需要补充资料以及需要什么资料。",
    "审核与修订共享此额度，并受项目历史累计预算约束。仅本地模式需要相应能力已就绪。",
    "未授权时可先查看资料需求，执行前需完成授权。请勿填写私密内容。",
    "无需预选案例、智能体或工具",
  ])
    await expect(page.locator("body")).not.toContainText(removed);
});

test("删除需确认，成功后选择下一条且刷新不恢复已删任务", async ({ page }) => {
  const second = {
    ...makeTask("second", "succeeded"),
    title: "另一个自定义任务",
  };
  const calls = await records(page, [makeTask("first"), second]);
  await page.goto("/?project=project&task_case=first");
  await page.getByRole("button", { name: "删除任务记录", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("历史费用仍保留");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect(calls.deletionCount).toBe(0);
  await page.getByRole("button", { name: "删除任务记录", exact: true }).click();
  await dialog.getByRole("button", { name: "删除记录", exact: true }).click();
  await expect(page.locator(".taskcase-overview h2")).toHaveText(
    "另一个自定义任务",
  );
  await expect(page.locator(".taskcase-overview p.taskcase-goal")).toHaveText(
    goal,
  );
  await expect(page.locator(".taskcase-history li")).toHaveCount(1);
  await expect(page).toHaveURL(/\?project=project$/);
  expect(calls.deletionCount).toBe(1);
  await page.reload();
  await expect(page.locator(".taskcase-overview h2")).toHaveText(
    "另一个自定义任务",
  );
  await expect(page.locator(".taskcase-history li")).toHaveCount(1);
});

test("删除被后台拒绝时保留任务与错误，重试成功后最后一条进入新建", async ({
  page,
}) => {
  const calls = await records(page, [makeTask("last", "succeeded")], true);
  await page.goto("/?project=project&task_case=last");
  await page.getByRole("button", { name: "删除任务记录", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "删除记录", exact: true }).click();
  await expect(dialog.getByRole("alert")).toHaveText(
    "任务正在完成执行清理，请稍后再删除记录。",
  );
  await expect(page.locator(".taskcase-history li")).toHaveCount(1);
  await dialog.getByRole("button", { name: "删除记录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "你希望完成什么工作？" }),
  ).toBeVisible();
  await expect(page.locator(".taskcase-history li")).toHaveCount(0);
  expect(calls.deletionCount).toBe(2);
  await expect(page).toHaveURL(/\?project=project$/);
});

test("执行中的任务不可直接删除", async ({ page }) => {
  const calls = await records(page, [makeTask("active", "running")]);
  await page.goto("/?project=project");
  await expect(
    page.getByRole("button", { name: "删除任务记录", exact: true }),
  ).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "取消任务", exact: true }),
  ).toBeEnabled();
  expect(calls.deletionCount).toBe(0);
});
