import { test, expect } from "@playwright/test";
import { writeFileSync } from "node:fs";

test("云端浏览器：公开文件上传后无人值守完成证据分析", async ({ page }) => {
  test.skip(
    process.env.HANGZHI_BROWSER_CLOUD_SMOKE !== "1",
    "仅由 tools/smoke.py 在开发预算预留后启动",
  );
  test.setTimeout(360000);
  const name = `云端浏览器验收-${Date.now()}`;
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD!);
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await page.getByLabel("当前项目").selectOption({ label: "本轮真实验收" });
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "数据资产", exact: true })
    .click();
  await page.getByRole("button", { name: "登记 / 上传数据" }).click();
  let dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "上传文件", exact: true }).click();
  await dialog
    .getByLabel("选择文件")
    .setInputFiles({
      name: `${name}.txt`,
      mimeType: "text/plain",
      buffer: Buffer.from(
        "公开合成记录：星航项目负责人是李华，2026年10月启动，目标是验证无人值守资料分析。交付日期未确定。",
      ),
    });
  await dialog
    .getByLabel("来源标识", { exact: true })
    .fill(`synthetic:browser-cloud:${name}`);
  await dialog
    .getByRole("combobox", { name: "数据范围", exact: true })
    .selectOption("public");
  await dialog
    .getByLabel("来源及授权说明", { exact: true })
    .fill("脚本生成的公开合成资料，允许本次真实模型验收外发。");
  await dialog.getByRole("button", { name: "验证并登记" }).click();
  await expect(dialog).not.toBeVisible();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "任务运行", exact: true })
    .click();
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  dialog = page.getByRole("dialog");
  await dialog.getByLabel("任务名称", { exact: true }).fill(name);
  await dialog
    .getByLabel("任务目标", { exact: true })
    .fill(
      "使用证据分析技能，提取负责人、启动时间与目标并总结，引用原文；交付日期未知请明确说明。",
    );
  await dialog
    .getByRole("combobox", { name: "任务类型", exact: true })
    .selectOption("text_analysis");
  await dialog
    .getByRole("combobox", { name: "执行模式", exact: true })
    .selectOption("cloud");
  await dialog.getByRole("checkbox", { name: new RegExp(name) }).check();
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
