import { test, expect, type Page } from "@playwright/test";

async function openTools(page: Page, projectId: string) {
  await page.goto("/manage");
  await page.getByLabel("当前项目").selectOption(projectId);
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "工具目录", exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "工具目录", exact: true }),
  ).toBeVisible();
}

test("工具目录检索与技能依赖可追到发布智能体，试用与只读权限可区分", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(90_000);
  const login = await page.request.post("/api/auth/login", {
    data: {
      username: process.env.HANGZHI_SMOKE_USERNAME || "admin",
      password:
        process.env.HANGZHI_SMOKE_PASSWORD || "synthetic-browser-password",
    },
  });
  expect(login.ok()).toBeTruthy();
  const stamp = Date.now();
  const projectResponse = await page.request.post("/api/v2/projects", {
    data: { name: "工具目录验收-" + stamp },
  });
  expect(projectResponse.ok()).toBeTruthy();
  const p = await projectResponse.json();
  expect(
    (
      await page.request.post("/api/v2/casework/install?project_id=" + p.id)
    ).ok(),
  ).toBeTruthy();
  await openTools(page, p.id);
  const details = page.getByRole("region", { name: "所选目录条目" });
  await page.getByLabel("工具分类").selectOption("mcp");
  await expect(details).toContainText("已登记 MCP 服务");
  await page.getByLabel("搜索工具或技能（Skill）").fill("baseline_anomaly");
  await expect(page.getByRole("heading", { name: "没有匹配项" })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await page.getByRole("button", { name: /^文档技能包 · / }).click();
  await details
    .getByRole("button", { name: "授权资料目录", exact: true })
    .click();
  await expect(details.locator(".catalog-identifier")).toContainText(
    "data.list_sources",
  );
  await page.getByLabel("搜索工具或技能（Skill）").fill("extract_quantities");
  await page.getByLabel("仅看本项目已授权").check();
  const specialist = details.getByRole("button", {
    name: "用试验研究证据阅读卡专员试用",
  });
  await expect(specialist).toHaveCount(1);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(details).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBeTruthy();
  await specialist.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("试用工具")).toHaveValue(
    "evidence.extract_quantities",
  );
  await expect(
    dialog.getByRole("button", { name: "执行本地试用" }),
  ).toBeDisabled();

  const username = "catalog_reader_" + stamp;
  const password = "synthetic-reader-" + stamp;
  const created = await page.request.post("/api/users", {
    data: { username, name: "目录只读验收", password, role: "reviewer" },
  });
  expect(created.ok()).toBeTruthy();
  const viewer = await created.json();
  const session = await browser.newContext({ baseURL });
  try {
    expect(
      (
        await page.request.put("/api/v2/projects/" + p.id + "/members", {
          data: { members: [viewer.id] },
        })
      ).ok(),
    ).toBeTruthy();
    const viewerPage = await session.newPage();
    expect(
      (
        await viewerPage.request.post("/api/auth/login", {
          data: { username, password },
        })
      ).ok(),
    ).toBeTruthy();
    await openTools(viewerPage, p.id);
    await viewerPage
      .getByLabel("搜索工具或技能（Skill）")
      .fill("series.baseline_anomaly");
    const holders = viewerPage.getByRole("region", {
      name: "本项目授权智能体",
    });
    await expect(
      holders.getByRole("button", { name: /查看.*海冰.*智能体/ }),
    ).toHaveCount(1);
    await expect(holders.getByRole("button", { name: /试用/ })).toHaveCount(0);
    await holders.getByRole("button", { name: /查看.*海冰.*智能体/ }).click();
    await expect(viewerPage.getByRole("dialog")).toContainText(
      "已发布用例岗位合同",
    );
    await expect(
      viewerPage.getByRole("dialog").getByLabel("试用工具"),
    ).toHaveCount(0);
  } finally {
    await session.close();
    expect(
      (
        await page.request.put("/api/users/" + viewer.id, {
          data: { enabled: false },
        })
      ).ok(),
    ).toBeTruthy();
  }
});
