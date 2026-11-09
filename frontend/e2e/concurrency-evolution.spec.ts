import { expect, test, type APIResponse, type Page } from "@playwright/test";
import { randomUUID } from "node:crypto";
import { deflateSync } from "node:zlib";

// These checks need a real API, database and Worker. All inputs are synthetic;
// no route interception, model doubles, paid calls or precomputed task results.
test.setTimeout(120000);

async function json(response: APIResponse) {
  expect(
    response.ok(),
    `HTTP ${response.status()}: ${await response.text()}`,
  ).toBeTruthy();
  return response.json();
}

async function createProject(page: Page, name: string) {
  const project = await json(
    await page.request.post("/api/v2/projects", {
      data: {
        name,
        description:
          "Synthetic browser acceptance for plugins and workflow evolution",
      },
    }),
  );
  await page.goto("/manage");
  await expect(page.getByLabel("当前项目")).toBeVisible();
  await page.getByLabel("当前项目").selectOption(project.id);
  return project;
}

async function navigate(page: Page, name: string) {
  await page
    .getByRole("navigation")
    .getByRole("button", { name, exact: true })
    .click();
  await expect(
    page.getByRole("main").getByRole("heading", { name, exact: true }),
  ).toBeVisible();
}

async function terminal(page: Page, taskId: string) {
  let detail: any;
  await expect
    .poll(
      async () => {
        detail = await json(await page.request.get(`/api/v2/tasks/${taskId}`));
        return detail.status;
      },
      { timeout: 45000, intervals: [250, 500, 1000] },
    )
    .toMatch(/^(succeeded|partial|failed|cancelled)$/);
  return detail;
}

async function openTask(page: Page, projectId: string, task: any) {
  await page.goto("/manage");
  await page.getByLabel("当前项目").selectOption(projectId);
  await navigate(page, "任务中心");
  await page
    .locator("tr")
    .filter({ hasText: task.id })
    .getByRole("button", { name: task.name, exact: true })
    .click();
  await page.getByRole("button", { name: "执行图与实例", exact: true }).click();
}

function syntheticPng(width: number, height: number): Buffer {
  function chunk(type: string, data: Buffer) {
    const payload = Buffer.concat([Buffer.from(type), data]);
    let crc = 0xffffffff;
    for (const byte of payload) {
      crc ^= byte;
      for (let bit = 0; bit < 8; bit++)
        crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
    }
    const length = Buffer.alloc(4),
      checksum = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    checksum.writeUInt32BE((crc ^ 0xffffffff) >>> 0);
    return Buffer.concat([length, payload, checksum]);
  }
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2; // RGB, 8 bits per channel; each row starts with filter 0.
  const pixels = Buffer.alloc(height * (width * 3 + 1), 0);
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const index = y * (width * 3 + 1) + 1 + x * 3;
      pixels[index] = 100;
      pixels[index + 1] = 30;
      pixels[index + 2] = 170;
    }
  }
  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    chunk("IHDR", header),
    chunk("IDAT", deflateSync(pixels)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

test("任务插件：管理端发布、真实 PNG 自动执行与隔离上下文", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const project = await createProject(page, `插件浏览器验收-${suffix}`);
  const pluginName = `合成 PNG 插件-${suffix}`;
  await navigate(page, "扩展接入");
  await page
    .getByRole("main")
    .getByRole("button", { name: "扩展接入", exact: true })
    .click();
  let dialog = page.getByRole("dialog");
  await dialog
    .getByRole("combobox", { name: "接入类型", exact: true })
    .selectOption("task_plugin");
  await dialog.getByLabel("名称", { exact: true }).fill(pluginName);
  await dialog
    .getByLabel("说明", { exact: true })
    .fill("只验证合成图片的真实解码与元数据。");
  await dialog
    .getByRole("combobox", { name: "插件执行器", exact: true })
    .selectOption("image_metadata");
  const createdResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v2/capability-definitions/task-plugins") &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  const definition = await json(await createdResponse);
  await expect(dialog).not.toBeVisible();
  const card = page.locator(".asset-grid > section.panel").filter({
    has: page.getByRole("heading", { name: pluginName, exact: true }),
  });
  await card.getByRole("button", { name: "发布新版本", exact: true }).click();
  dialog = page.getByRole("dialog");
  const publishedResponse = page.waitForResponse(
    (response) =>
      response
        .url()
        .endsWith(`/capability-definitions/${definition.id}/publish`) &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  const published = await json(await publishedResponse);
  expect(published.current_version).toBe(1);
  await expect(dialog).not.toBeVisible();
  await expect(card).toContainText("平台版本 v1");

  const asset = await json(
    await page.request.post("/api/v2/data-assets/upload", {
      multipart: {
        project_id: project.id,
        name: `合成图片-${suffix}`,
        source_id: `synthetic:browser-plugin:${suffix}`,
        source_version: "1",
        provenance:
          "Generated 13 by 7 RGB fixture; contains no private material",
        classification: "public",
        file: {
          name: "synthetic.png",
          mimeType: "image/png",
          buffer: syntheticPng(13, 7),
        },
      },
    }),
  );
  const task = await json(
    await page.request.post("/api/v2/tasks", {
      data: {
        project_id: project.id,
        name: `真实图片检查-${suffix}`,
        goal: "验证图片实际尺寸和格式",
        task_type: "image_inspect",
        input_refs: [asset.id],
        mode: "local",
        max_cost_cny: 0,
        max_revisions: 0,
        idempotency_key: randomUUID(),
      },
    }),
  );
  expect(task.request.plugin_id).toBeNull();
  const finished = await terminal(page, task.id);
  expect(finished.status, finished.reason_code).toBe("succeeded");
  expect(finished.snapshot.task_plugin.id).toBe(definition.id);
  expect(finished.snapshot.task_plugin.version).toBe(1);
  expect(finished.snapshot.task_plugin.checksum).toMatch(/^[a-f0-9]{64}$/);
  expect(finished.result.usage.request_count).toBe(0);
  const artifact = await json(
    await page.request.get(
      `/api/v2/artifacts/${finished.result.artifacts[0].id}`,
    ),
  );
  expect(artifact.content.images).toHaveLength(1);
  expect(artifact.content.images[0]).toMatchObject({
    source_id: asset.id,
    width: 13,
    height: 7,
    decoded: true,
    format: "PNG",
  });
  expect(artifact.task_plugin).toMatchObject({ id: definition.id, version: 1 });
  const instance = finished.instances.find((item: any) => item.context);
  expect(instance.context.input_refs).toEqual([asset.id]);
  expect(instance.context.inbox).toEqual([]);
  expect(instance.context.policy_version).toBe("directed-context-v1");
  await openTask(page, project.id, task);
  const communication = page.locator("section.panel").filter({
    has: page.getByRole("heading", { name: "智能体协作与上下文", exact: true }),
  });
  await expect(communication).toContainText(instance.context_id);
  await expect(communication).toContainText("授权来源 1 项");
  await expect(communication).toContainText("收到依赖 0 项");
  await page.screenshot({
    path: "../.local/browser-plugin-context.png",
    fullPage: true,
  });
});

test("流程迭代：真实失败样本、完整重放、晋升与回滚保持版本", async ({
  page,
}) => {
  const suffix = randomUUID().slice(0, 8);
  const project = await createProject(page, `流程迭代浏览器验收-${suffix}`);
  const agent = await json(
    await page.request.post("/api/v2/agents", {
      data: {
        project_id: project.id,
        name: `本地来源专员-${suffix}`,
        capabilities: ["source_summary"],
        modalities: ["text", "structured"],
        tool_ids: ["source_summary"],
        model_policy: "local",
        execution_strategy: "fixed",
      },
    }),
  );
  await json(await page.request.post(`/api/v2/agents/${agent.id}/publish`));
  const nodes = ["extract", "deliver"].map((id, index) => ({
    id,
    label: id,
    agent_id: agent.id,
    depends_on: index ? ["extract"] : [],
    config: { max_chars: 100 },
  }));
  const flow = await json(
    await page.request.post("/api/v2/workflows", {
      data: {
        project_id: project.id,
        name: `两节点来源流程-${suffix}`,
        task_type: "source_summary",
        nodes,
      },
    }),
  );
  const baseline = await json(
    await page.request.post(`/api/v2/workflows/${flow.id}/publish`),
  );
  expect(baseline.current_version).toBe(1);
  const samples = await Promise.all(
    ["A", "B"].map(async (marker) => {
      const source = await json(
        await page.request.post("/api/v2/data-assets", {
          data: {
            project_id: project.id,
            name: `独立合成来源-${marker}-${suffix}`,
            modality: "text",
            media_type: "text/plain",
            content: marker.repeat(400),
            source_id: `synthetic:workflow:${suffix}:${marker}`,
            source_version: "1",
            provenance: "Disjoint 400-character synthetic fixture",
            classification: "public",
          },
        }),
      );
      return json(
        await page.request.post("/api/v2/tasks", {
          data: {
            project_id: project.id,
            name: `覆盖失败样本-${marker}-${suffix}`,
            goal: "至少保留300字原文",
            task_type: "source_summary",
            workflow_id: flow.id,
            input_refs: [source.id],
            mode: "local",
            max_cost_cny: 0,
            max_revisions: 0,
            quality: { min_length: 300 },
            idempotency_key: randomUUID(),
          },
        }),
      );
    }),
  );
  const failed = await Promise.all(
    samples.map((task) => terminal(page, task.id)),
  );
  for (const detail of failed) {
    expect(detail.status, detail.reason_code).toBe("failed");
    expect(detail.snapshot.workflow.version).toBe(1);
    expect(detail.nodes).toHaveLength(2);
    expect(detail.result.usage.request_count).toBe(0);
    expect(detail.result.quality_verdict.passed).toBe(false);
    expect(
      detail.nodes.every(
        (node: any) => node.output.content.fragments[0].text.length === 100,
      ),
    ).toBe(true);
    const delivery = detail.instances.find(
      (item: any) => item.node_id === "deliver",
    );
    const extraction = detail.instances.find(
      (item: any) => item.node_id === "extract",
    );
    expect(delivery.context_id).not.toBe(extraction.context_id);
    expect(delivery.context.inbox).toHaveLength(1);
    expect(delivery.context.inbox[0].sender_instance_id).toBe(extraction.id);
  }
  expect(failed[0].instances[0].context_id).not.toBe(
    failed[1].instances[0].context_id,
  );
  await openTask(page, project.id, samples[0]);
  await expect(
    page.getByRole("button", { name: "查看来源步骤 extract", exact: true }),
  ).toBeVisible();

  await navigate(page, "效果评测");
  await page.getByRole("button", { name: "创建改进候选", exact: true }).click();
  let dialog = page.getByRole("dialog");
  const candidateName = `增加覆盖范围-${suffix}`;
  await dialog
    .getByRole("combobox", { name: "迭代对象", exact: true })
    .selectOption("workflow");
  await dialog.getByLabel("候选名称", { exact: true }).fill(candidateName);
  await dialog
    .getByRole("combobox", { name: "基线工作流", exact: true })
    .selectOption(flow.id);
  await dialog
    .getByRole("textbox", { name: "候选流程节点 JSON", exact: true })
    .fill(
      JSON.stringify(
        nodes.map((node) => ({ ...node, config: { max_chars: 300 } })),
        null,
        2,
      ),
    );
  for (const sample of samples)
    await dialog
      .getByRole("checkbox", { name: new RegExp(sample.name) })
      .check();
  const candidateResponse = page.waitForResponse(
    (response) =>
      response.url().endsWith("/api/v2/workflow-improvements") &&
      response.request().method() === "POST",
  );
  await dialog.getByRole("button", { name: "保存候选", exact: true }).click();
  const candidate = await json(await candidateResponse);
  expect(candidate.target_kind).toBe("workflow");
  await expect(dialog).not.toBeVisible();
  const row = page.locator("tr").filter({
    has: page.getByRole("button", { name: candidateName, exact: true }),
  });
  await expect(row).toContainText(`流程 · ${flow.name}`);

  async function runAction(label: string, action: string) {
    await row.getByRole("button", { name: label, exact: true }).click();
    const response = page.waitForResponse(
      (item) =>
        item.url().endsWith(`/improvements/${candidate.id}/${action}`) &&
        item.request().method() === "POST",
    );
    const confirmation = page.getByRole("dialog");
    await confirmation
      .getByRole("button", { name: "确认", exact: true })
      .click();
    const result = await json(await response);
    await expect(confirmation).not.toBeVisible();
    return result;
  }

  const evaluated = await runAction("评测", "evaluate");
  expect(evaluated.verdict).toMatchObject({
    passed: true,
    baseline_passed: 0,
    candidate_passed: 2,
    sample_count: 2,
    holdout_passed: true,
    regression: false,
  });
  const evaluations = await json(await page.request.get("/api/v2/evaluations"));
  const evidence = evaluations.find(
    (item: any) => item.id === evaluated.evaluation_id,
  );
  expect(evidence.method).toBe("frozen_local_workflow_replay");
  expect(evidence.samples.map((sample: any) => sample.split)).toEqual([
    "development",
    "holdout",
  ]);
  for (const pair of evidence.samples) {
    expect(pair.baseline.quality.passed).toBe(false);
    expect(pair.candidate.quality.passed).toBe(true);
    expect(pair.candidate.history[0].nodes).toHaveLength(2);
  }
  await row.getByRole("button", { name: "查看评测记录", exact: true }).click();
  dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("工作流版本");
  await expect(dialog).toContainText("候选通过 2 / 2 个样本");
  await expect(dialog).toContainText("独立留出");
  await page.screenshot({
    path: "../.local/browser-workflow-evolution.png",
    fullPage: true,
  });
  await page.keyboard.press("Escape");
  await expect(dialog).not.toBeVisible();

  const promoted = await runAction("晋升", "promote");
  expect(promoted.candidate_version).toBe(2);
  expect(
    (await json(await page.request.get(`/api/v2/workflows/${flow.id}`)))
      .current_version,
  ).toBe(2);
  expect(
    (await json(await page.request.get(`/api/v2/agents/${agent.id}`)))
      .current_version,
  ).toBe(1);
  const unseenSource = await json(
    await page.request.post("/api/v2/data-assets", {
      data: {
        project_id: project.id,
        name: `晋升后独立来源-${suffix}`,
        modality: "text",
        media_type: "text/plain",
        content: "C".repeat(400),
        source_id: `synthetic:workflow:${suffix}:C`,
        source_version: "1",
        provenance: "Synthetic source not used in candidate evaluation",
        classification: "public",
      },
    }),
  );
  const fresh = await json(
    await page.request.post("/api/v2/tasks", {
      data: {
        ...samples[0].request,
        input_refs: [unseenSource.id],
        name: `晋升后真实任务-${suffix}`,
        idempotency_key: randomUUID(),
      },
    }),
  );
  const freshResult = await terminal(page, fresh.id);
  expect(freshResult.status, freshResult.reason_code).toBe("succeeded");
  expect(freshResult.snapshot.workflow.version).toBe(2);
  const rolledBack = await runAction("回滚", "rollback");
  expect(rolledBack.status).toBe("rolled_back");
  expect(
    (await json(await page.request.get(`/api/v2/workflows/${flow.id}`)))
      .current_version,
  ).toBe(1);
  const versions = await json(
    await page.request.get(`/api/v2/workflows/${flow.id}/versions`),
  );
  expect(versions.map((version: any) => version.version)).toEqual(
    expect.arrayContaining([1, 2]),
  );
  const preserved = await json(
    await page.request.get(`/api/v2/tasks/${samples[0].id}`),
  );
  expect(preserved.snapshot.workflow).toEqual(failed[0].snapshot.workflow);
  await expect(row.locator(".badge")).toHaveText("已回滚");
});
