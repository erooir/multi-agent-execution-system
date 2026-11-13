import { test, expect } from "@playwright/test";
import { adminSession } from "./admin-session";

test("普通用户只提交一个问题，平台在已有资料上自动构建流程并交付答复", async ({
  page,
  browser,
  baseURL,
}) => {
  test.setTimeout(105_000);
  const manager = await browser.newContext({ baseURL });
  const stamp = Date.now();
  try {
    const login = await adminSession(manager);
    expect(login.ok()).toBeTruthy();
    const register = await page.request.post("/api/auth/register", {
      data: {
        username: "ask_" + stamp,
        name: "问答验收",
        password: "synthetic-question-" + stamp,
      },
    });
    expect(register.ok()).toBeTruthy();
    const user = (await register.json()).user;
    expect(user.role).toBe("requester");
    const projectResponse = await manager.request.post("/api/v2/projects", {
      data: { name: "自然语言验收-" + stamp },
    });
    expect(projectResponse.ok()).toBeTruthy();
    const project = await projectResponse.json();
    expect(
      (
        await manager.request.put(
          "/api/v2/projects/" + project.id + "/members",
          { data: { members: [user.id] } },
        )
      ).ok(),
    ).toBeTruthy();
    for (const [name, content] of [
      [
        "北极九月海冰年度观测",
        [
          {
            kind: "policy",
            start_year: 2000,
            target_year: 2004,
            baseline_years: [2000, 2002],
            extent_unit: "km²",
          },
          ...[10, 12, 14, 11, 9].map((extent, i) => ({
            year: 2000 + i,
            month: 9,
            hemisphere: "N",
            extent,
          })),
        ],
      ],
      ["内部办公用品清单", [{ item: "文件夹", quantity: 42 }]],
    ]) {
      expect(
        (
          await manager.request.post("/api/v2/data-assets", {
            data: {
              project_id: project.id,
              name,
              modality: "structured",
              media_type: "application/json",
              content,
              source_id: name,
              provenance: "synthetic",
              classification: "local",
            },
          })
        ).ok(),
      ).toBeTruthy();
    }
    const calls: string[] = [];
    page.on("request", (req) => {
      if (req.url().includes("/api/v2/"))
        calls.push(new URL(req.url()).pathname);
    });
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/questions");
    await expect(page.getByLabel("当前项目")).toHaveValue(project.id);
    await expect(page.getByRole("link", { name: "管理工作台" })).toHaveCount(0);
    const question =
      "2004 年北极 9 月海冰范围比 2000 至 2002 年平均少多少？在这五年中排第几？";
    await page.getByLabel("你的问题").fill(question);
    const posted = page.waitForRequest(
      (q) => q.method() === "POST" && q.url().endsWith("/api/v2/questions"),
    );
    await page.getByRole("button", { name: "提交问题" }).click();
    expect(Object.keys((await posted).postDataJSON()).sort()).toEqual([
      "idempotency_key",
      "project_id",
      "question",
    ]);
    const answer = page.getByRole("region", { name: "问题答复" });
    await expect(answer.getByRole("status")).toHaveText("已完成", {
      timeout: 60_000,
    });
    await expect(answer).toContainText(
      /(3|三).*?(平方|km|公里)|(少|低|差).*?(3|三)/,
      { timeout: 60_000 },
    );
    const own = await (
      await page.request.get("/api/v2/questions?project_id=" + project.id)
    ).json();
    expect(own).toHaveLength(1);
    const summary = await (
      await page.request.get("/api/v2/questions/" + own[0].id)
    ).json();
    expect(summary.status).toBe("succeeded");
    expect(summary.citations.length).toBeGreaterThan(0);
    expect(summary.answer).not.toContain("办公用品");
    await page.reload();
    await expect(answer).toContainText(summary.answer.slice(0, 15));
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBeTruthy();
    expect(
      calls.every(
        (path) =>
          path === "/api/v2/question-projects" ||
          path.startsWith("/api/v2/questions"),
      ),
    ).toBeTruthy();
    for (const path of [
      "bootstrap",
      "agents",
      "tasks",
      "data-assets",
      "workers",
    ])
      expect((await page.request.get("/api/v2/" + path)).status()).toBe(403);
    await page.goto("/manage");
    await expect(page).toHaveURL(new URL("/", baseURL).href);
    await expect(page.getByLabel("任务目标")).toBeVisible();
  } finally {
    await manager.close();
  }
});
