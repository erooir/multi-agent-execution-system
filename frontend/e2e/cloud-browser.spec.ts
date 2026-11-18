import { test, expect } from "@playwright/test";
import { writeFileSync } from "node:fs";

test("云端浏览器：公开资料通过上游 API 接入后无人值守完成证据分析", async ({
  page,
}) => {
  test.skip(
    process.env.HANGZHI_BROWSER_CLOUD_SMOKE !== "1",
    "仅由 tools/smoke.py 在开发预算预留后启动",
  );
  test.setTimeout(360000);
  const name = `云端浏览器验收-${Date.now()}`;
  await page.goto("/manage");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  const project = (
    await (await page.request.get("/api/v2/projects")).json()
  ).find((p: any) => p.name === "本轮真实验收");
  expect(project?.id).toBeTruthy();
  const source = await page.request.post("/api/v2/data-assets", {
    data: {
      project_id: project.id,
      name,
      source_id: name,
      source_version: "1",
      modality: "text",
      media_type: "text/plain",
      classification: "public",
      content:
        "公开合成记录：星航项目负责人是李华，2026年10月启动，目标是验证无人值守资料分析。交付日期未确定。",
      provenance: "脚本生成的公开合成资料，允许本次真实模型验收外发。",
    },
  });
  expect(source.ok()).toBeTruthy();
  await page.goto("/manage?view=tasks");
  await page.locator(".wb-group-row").filter({ hasText: project.name }).click();
  await page
    .locator("main")
    .getByRole("button", { name: "新建任务", exact: true })
    .click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("任务名称", { exact: true }).fill(name);
  await dialog
    .getByLabel("任务目标", { exact: true })
    .fill(
      `使用证据分析技能，分析来源 ${name}，提取负责人、启动时间与目标并总结，引用原文；交付日期未知请明确说明。`,
    );
  await dialog
    .getByRole("combobox", { name: "任务类型", exact: true })
    .selectOption("text_analysis");
  await dialog
    .getByRole("combobox", { name: "执行模式", exact: true })
    .selectOption("cloud");
  const accepted = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v2/tasks") &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "提交并自动执行" }).click();
  const task = await (await accepted).json();
  expect(task.id).toBeTruthy();
  writeFileSync(
    process.env.HANGZHI_BROWSER_TASK_FILE!,
    JSON.stringify({ task_id: task.id }),
  );
  let outcome: any;
  await expect
    .poll(
      async () => {
        outcome = await (
          await page.request.get(`/api/v2/tasks/${task.id}`)
        ).json();
        return ["succeeded", "partial", "failed", "cancelled"].includes(
          outcome.status,
        );
      },
      { timeout: 310000, intervals: [1000, 2000, 4000] },
    )
    .toBe(true);
  expect(outcome.status, outcome.reason_code).toBe("succeeded");
  expect(
    outcome.tool_calls.some(
      (call: any) => call.kind === "skill" && call.status === "completed",
    ),
  ).toBeTruthy();
  expect(outcome.quality_verdict.passed).toBeTruthy();
  await expect(page.locator(".task-strip .badge")).toHaveText("成功", {
    timeout: 10000,
  });
  await page.getByRole("button", { name: /结果与产物/ }).click();
  await page.getByRole("button", { name: "查看内容", exact: true }).click();
  await expect(page.getByRole("dialog").locator("pre.prose")).toContainText(
    "李华",
  );
  await page.screenshot({
    path: "../.local/browser-cloud.png",
    fullPage: true,
  });
});
