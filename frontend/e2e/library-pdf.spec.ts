import { expect, test } from "@playwright/test";

test("知识库 PDF 深链登录后打开原件，主要资料和全部资料可切换", async ({
  context,
  page,
}) => {
  const user = { id: "reader", username: "reader", role: "reviewer" };
  const project = {
    id: "fixture-project",
    name: "合成资料项目",
    members: [user.id],
  };
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
  await context.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/login") {
      signedIn = true;
      return route.fulfill({ json: { user } });
    }
    if (!signedIn)
      return route.fulfill({ status: 401, json: { detail: "请登录" } });
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: user });
    if (
      url.pathname === "/api/v2/bootstrap" &&
      url.searchParams.get("project_id") === project.id
    ) {
      return route.fulfill({
        json: {
          user,
          projects: [project],
          current_project_id: project.id,
          data_assets: [asset, supplement],
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

  await page.goto(
    `/manage?view=library&tab=data&project=${project.id}&asset=${asset.id}`,
  );
  await page.getByLabel("用户名").fill(user.username);
  await page.getByLabel("密码").fill("synthetic-password");
  const inlineResponse = page.waitForResponse((r) => r.url().endsWith(preview));
  await page.getByRole("button", { name: "登录", exact: true }).click();
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
});
