import { test, expect } from "@playwright/test";

test("读取指定真实任务的交付、来源与审核轨迹（不创建或执行任务）", async ({
  page,
}, testInfo) => {
  const task = process.env.HANGZHI_DEMO_TASKCASE;
  const project = process.env.HANGZHI_DEMO_PROJECT;
  test.skip(
    !task || !project,
    "Requires an explicitly selected completed task; never starts a paid job.",
  );
  const mutations: string[] = [];
  page.on("request", (request) => {
    const pathname = new URL(request.url()).pathname;
    if (
      pathname.startsWith("/api/") &&
      !["GET", "HEAD", "OPTIONS"].includes(request.method()) &&
      pathname !== "/api/auth/login"
    )
      mutations.push(pathname);
  });
  await page.goto(
    `/?project=${encodeURIComponent(project!)}&task_case=${encodeURIComponent(task!)}`,
  );
  await page
    .getByLabel("用户名", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_USERNAME || "admin");
  await page
    .getByLabel("密码", { exact: true })
    .fill(process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "交付结果", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".taskcase-answer")).toContainText(/8,?006/);
  await expect(page.locator(".taskcase-answer")).toContainText(/13,?287/);
  await expect(
    page.locator('.taskcase-monthly-chart rect[fill="#625bd7"]'),
  ).toHaveCount(13);
  await page.locator(".taskcase-citations > summary").click();
  const source = page.locator(".taskcase-source-link").first();
  await expect(source).toBeVisible();
  expect(await source.getAttribute("href")).toMatch(
    /^\/api\/v2\/task-cases\/.+#page=\d+$/,
  );
  const original = await page.request.get(
    (await source.getAttribute("href"))!.split("#")[0],
  );
  expect(original.ok()).toBe(true);
  expect(original.headers()["content-type"]).toContain("application/pdf");
  await page.getByText("平台如何完成这项任务", { exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "实际参与的智能体" }),
  ).toBeVisible();
  await expect(
    page.getByRole("cell", { name: "deepseek-flash", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "审核不通过后的针对性调整" }),
  ).toBeVisible();
  await expect(
    page.getByText("读取图上印刷月度标签并重新核算", { exact: false }),
  ).toBeVisible();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({
    path: testInfo.outputPath("b05-real-desktop.png"),
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(() => window.scrollTo(0, 0));
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: testInfo.outputPath("b05-real-mobile.png"),
    fullPage: true,
  });
  expect(mutations).toEqual([]);
});
