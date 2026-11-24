import { expect, test, type Page } from "@playwright/test";

async function mockLibrary(page: Page) {
  const user = { id: "reader", username: "reader", role: "reviewer" };
  const project = {
    id: "fixture-project",
    name: "合成资料项目",
    members: [user.id],
  };
  const otherProject = { ...project, id: "other-project", name: "另一个合成项目" };
  const pdf = await page.pdf();
  const original = "/api/v2/data-assets/fixture-pdf/original";
  const preview = original + "?disposition=inline";
  const asset = {
    id: "fixture-pdf",
    project_id: project.id,
    name: "合成资料",
    source_id: "fixture",
    status: "active",
    expires_at: "2099-01-01T00:00:00Z",
    media_type: "application/pdf",
    original_file: { filename: "sample.pdf", bytes: pdf.length },
    case_library_role: "primary",
  };
  const supplement = {
    ...asset,
    id: "fixture-index",
    name: "合成 PDF 资料索引",
    case_library_role: "supplementary",
    original_file: { filename: "sample.pdf.md", bytes: 10 },
    is_file_index: true,
  };
  let signedIn = false;
  await page.context().route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/login") {
      signedIn = true;
      return route.fulfill({ json: { user } });
    }
    if (!signedIn)
      return route.fulfill({ status: 401, json: { detail: "请登录" } });
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: user });
    const projectId = url.searchParams.get("project_id") ?? project.id;
    const activeProject = [project, otherProject].find((item) => item.id === projectId);
    if (url.pathname === "/api/v2/bootstrap" && activeProject) {
      return route.fulfill({
        json: {
          user,
          projects: [project, otherProject],
          current_project_id: activeProject.id,
          data_assets: activeProject.id === project.id ? [asset, supplement] : [],
          tasks: [],
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
        },
      });
    }
    if (url.pathname + url.search === preview) {
      return route.fulfill({
        contentType: "application/pdf",
        headers: { "content-disposition": "inline" },
        body: pdf,
      });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  return { user, project, otherProject, asset, supplement, original, preview };
}

async function signIn(page: Page, username: string) {
  await page.getByLabel("用户名").fill(username);
  await page.getByLabel("密码").fill("synthetic-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
}

async function expectWorkdeskOverview(page: Page) {
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { name: "工作台", exact: true })).toBeVisible();
  const topbar = page.getByRole("banner");
  await expect(topbar.getByRole("button", { name: "工作台", exact: true })).toHaveClass(/is-active/);
}

test("直接打开管理入口登录后进入工作台总览", async ({ page }) => {
  const { user, project } = await mockLibrary(page);
  await page.goto("/manage");
  await signIn(page, user.username);
  await expectWorkdeskOverview(page);
  await expect(page.getByRole("button", { name: new RegExp(project.name) })).toHaveAttribute("aria-current", "true");
});

test("知识库 PDF 深链登录后打开原件，可切换资料范围，刷新回到当前项目工作台", async ({ page }) => {
  const { user, project, otherProject, asset, supplement, original, preview } = await mockLibrary(page);
  await page.goto(
    `/manage?view=library&tab=data&project=${project.id}&asset=${asset.id}`,
  );
  const inlineResponse = page.waitForResponse((r) => r.url().endsWith(preview));
  await signIn(page, user.username);
  const detail = page.getByRole("dialog", { name: asset.name, exact: true });
  await expect(detail.locator("iframe")).toHaveAttribute("src", preview);
  expect((await inlineResponse).ok()).toBeTruthy();
  const open = detail.getByRole("link", { name: "打开 PDF" });
  await expect(open).toHaveAttribute("href", preview);
  await expect(open).toHaveAttribute("target", "_blank");
  await expect(detail.getByRole("link", { name: "下载原件" })).toHaveAttribute(
    "href",
    original,
  );
  await detail.getByRole("button", { name: "关闭对话框" }).click();
  expect(new URL(page.url()).searchParams.has("asset")).toBe(false);

  const scope = page.getByLabel("资料范围");
  await expect(scope).toHaveValue("primary");
  const index = page.getByRole("row").filter({ hasText: supplement.name });
  await expect(index).toHaveCount(0);
  await scope.selectOption("all");
  await expect(index.getByRole("link", { name: "下载原件" })).toBeVisible();
  await expect(index.getByRole("link", { name: "打开 PDF" })).toHaveCount(0);
  await scope.selectOption("primary");
  await expect(index).toHaveCount(0);

  const nav = page.getByRole("navigation", { name: "主导航" });
  const topbar = page.getByRole("banner");
  await nav.getByRole("button", { name: "智能体·技能·连接器" }).click();
  await expect(topbar.getByRole("button", { name: "智能体", exact: true })).toBeVisible();
  const currentProject = page.getByRole("button", { name: new RegExp(otherProject.name) });
  const projectLoaded = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return url.pathname === "/api/v2/bootstrap"
      && url.searchParams.get("project_id") === otherProject.id && response.ok();
  });
  await currentProject.click();
  await projectLoaded;
  await expect(currentProject).toHaveAttribute("aria-current", "true");

  await page.reload();
  await expectWorkdeskOverview(page);
  await expect(currentProject).toHaveAttribute("aria-current", "true");
  await expect(detail).toHaveCount(0);
  const params = new URL(page.url()).searchParams;
  expect(params.get("view") ?? "workdesk").toBe("workdesk");
  expect(params.get("tab") ?? "overview").toBe("overview");
  expect(params.has("asset")).toBe(false);
});
