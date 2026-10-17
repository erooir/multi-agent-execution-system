import test from "node:test";
import assert from "node:assert/strict";
import { api, ApiError, idPath, post, readAssetContent } from "../src/api.ts";

test("API request includes same-origin credentials and JSON content type", async () => {
  const old = globalThis.fetch;
  let call;
  globalThis.fetch = async (url, options) => {
    call = { url, options };
    return new Response('{"id":"saved"}', { status: 201 });
  };
  try {
    assert.deepEqual(await post("/v2/tasks", { name: "public-safe" }), {
      id: "saved",
    });
    assert.equal(call.url, "/api/v2/tasks");
    assert.equal(call.options.credentials, "same-origin");
    assert.equal(call.options.headers.get("Content-Type"), "application/json");
  } finally {
    globalThis.fetch = old;
  }
});
test("domain errors preserve status and stable code for honest UI failures", async () => {
  const old = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(
      JSON.stringify({
        detail: { code: "reference_protected", message: "仍有引用" },
      }),
      { status: 409 },
    );
  try {
    await assert.rejects(
      () => api("/v2/agents/one"),
      (error) =>
        error instanceof ApiError &&
        error.status === 409 &&
        error.code === "reference_protected" &&
        error.message === "仍有引用",
    );
  } finally {
    globalThis.fetch = old;
  }
});
test("uploaded FormData leaves multipart boundary to the browser", async () => {
  const old = globalThis.fetch;
  let headers;
  globalThis.fetch = async (_, options) => {
    headers = options.headers;
    return new Response("{}");
  };
  try {
    await api("/v2/data-assets/upload", {
      method: "POST",
      body: new FormData(),
    });
    assert.equal(headers.has("Content-Type"), false);
  } finally {
    globalThis.fetch = old;
  }
});
test("path ids cannot alter route hierarchy", () =>
  assert.equal(idPath("agents", "one/two"), " /v2/agents/one%2Ftwo".trim()));

test("asset previews support authenticated JSON, text and binary responses", async () => {
  const old = globalThis.fetch;
  try {
    globalThis.fetch = async () =>
      new Response("city,count\nA,12", {
        headers: { "content-type": "text/csv" },
      });
    assert.equal(await readAssetContent("one"), "city,count\nA,12");
    globalThis.fetch = async () =>
      new Response('{"content":[1]}', {
        headers: { "content-type": "application/json" },
      });
    assert.deepEqual(await readAssetContent("one"), { content: [1] });
    globalThis.fetch = async () =>
      new Response(new Uint8Array([0, 1]), {
        headers: { "content-type": "image/png" },
      });
    assert.deepEqual(await readAssetContent("one"), {
      binary: true,
      media_type: "image/png",
    });
  } finally {
    globalThis.fetch = old;
  }
});
