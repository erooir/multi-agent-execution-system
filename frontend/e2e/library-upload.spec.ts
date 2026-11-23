import { readFile } from "node:fs/promises";
import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { adminSession } from "./admin-session";

const uploadPath = "/api/v2/upstream/data-assets/files";

async function uploadText(
  page: Page,
  filename: string,
  text: string,
  makePublic = false,
) {
  await page.getByRole("button", { name: "上传本地文件" }).click();
  const dialog = page.getByRole("dialog");
  const classification = dialog.getByRole("combobox", {
    name: "访问范围",
    exact: true,
  });
  await expect(classification).toHaveValue("local");
  if (makePublic) await classification.selectOption("public");
  await dialog.getByLabel("本地文件", { exact: true }).setInputFiles({
    name: filename,
    mimeType: "text/plain",
    buffer: Buffer.from(text, "utf8"),
  });
  const done = page.waitForResponse(
    (response) =>
      new URL(response.url()).pathname === uploadPath &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "上传文件", exact: true }).click();
  const response = await done;
  expect(response.status()).toBe(201);
  return response.json();
}

test("知识库两个入口可上传本地资料，补充检索和原件下载遵守项目权限", async ({
  page: adminPage,
  browser,
  baseURL,
}) => {
  test.setTimeout(90_000);
  expect((await adminSession(adminPage.context())).ok()).toBe(true);
  const admin = adminPage.request;
  const suffix = Date.now().toString(36);
  const createProject = async (name: string) => {
    const response = await admin.post("/api/v2/projects", { data: { name } });
    expect(response.status()).toBe(201);
    return response.json();
  };
  const project = await createProject("本地上传验证 " + suffix);
  const other = await createProject("独立工作空间 " + suffix);
  const createUser = async (role: "operator" | "reviewer") => {
    const login = {
      username: "up_" + role + "_" + suffix,
      password: "Test-upload-" + suffix,
    };
    const response = await admin.post("/api/users", {
      data: { ...login, name: role, role },
    });
    expect(response.ok()).toBe(true);
    return { id: (await response.json()).id, login };
  };
  const operator = await createUser("operator"),
    reviewer = await createUser("reviewer");
  expect(
    (
      await admin.put(`/api/v2/projects/${project.id}/members`, {
        data: { members: [operator.id, reviewer.id] },
      })
    ).ok(),
  ).toBe(true);

  const readOnly = await browser.newContext({ baseURL }),
    writable = await browser.newContext({ baseURL });
  const catalogUrl = `/manage?view=library&tab=data&project=${project.id}`;
  const searchUrl = `/manage?view=library&tab=knowledge&project=${project.id}`;
  const marker = "local-search-" + suffix;
  const deniedUpload = (request: APIRequestContext, target: string) =>
    request.post(uploadPath, {
      multipart: {
        project_id: target,
        name: "denied.txt",
        source_id: "denied-" + suffix,
        source_version: "1",
        provenance: "Synthetic upload test",
        classification: "local",
        file: {
          name: "denied.txt",
          mimeType: "text/plain",
          buffer: Buffer.from(marker),
        },
      },
    });
  try {
    expect(
      (
        await writable.request.post("/api/auth/login", { data: operator.login })
      ).ok(),
    ).toBe(true);
    const page = await writable.newPage();
    await page.goto(catalogUrl);
    const text = "合成资料 " + marker;
    const first = await uploadText(page, "catalog-note.txt", text);
    expect(first).toMatchObject({
      project_id: project.id,
      classification: "local",
      status: "active",
    });
    expect(first.original_file.filename).toBe("catalog-note.txt");
    const original = `/api/v2/data-assets/${first.id}/original`;
    const detail = page.getByRole("dialog");
    await expect(
      detail.getByRole("link", { name: "下载原件" }),
    ).toHaveAttribute("href", original);
    const received = page.waitForEvent("download");
    await detail.getByRole("link", { name: "下载原件" }).click();
    const download = await received;
    expect(download.suggestedFilename()).toBe("catalog-note.txt");
    const downloadedPath = await download.path();
    if (!downloadedPath) throw new Error("浏览器没有保存已下载的原件");
    expect(await readFile(downloadedPath)).toEqual(Buffer.from(text, "utf8"));

    await page.goto(searchUrl);
    await page.getByRole("textbox", { name: "知识检索内容" }).fill(marker);
    await page.getByRole("button", { name: "检索来源" }).click();
    await expect(
      page.getByRole("heading", { name: first.name, exact: true }),
    ).toBeVisible();
    const second = await uploadText(
      page,
      "search-note.txt",
      "第二份合成资料 " + marker,
      true,
    );
    expect(second).toMatchObject({
      project_id: project.id,
      classification: "public",
    });
    await expect(
      page.getByRole("heading", { name: second.name, exact: true }),
    ).toBeVisible();
    expect((await deniedUpload(writable.request, other.id)).status()).toBe(403);

    expect(
      (
        await readOnly.request.post("/api/auth/login", { data: reviewer.login })
      ).ok(),
    ).toBe(true);
    const reader = await readOnly.newPage();
    await reader.goto(catalogUrl);
    await expect(
      reader.getByRole("button", { name: first.name, exact: true }),
    ).toBeVisible();
    await expect(
      reader.getByRole("button", { name: "上传本地文件" }),
    ).toHaveCount(0);
    expect((await readOnly.request.get(original)).status()).toBe(200);
    expect((await deniedUpload(readOnly.request, project.id)).status()).toBe(
      403,
    );
    await reader.goto(searchUrl);
    await expect(
      reader.getByRole("textbox", { name: "知识检索内容" }),
    ).toBeVisible();
    await expect(
      reader.getByRole("button", { name: "上传本地文件" }),
    ).toHaveCount(0);
  } finally {
    await writable.close();
    await readOnly.close();
  }
});
