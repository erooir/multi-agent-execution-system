import test from "node:test";
import assert from "node:assert/strict";
import {
  argumentTemplate,
  canPreviewSource,
  checkArguments,
  fieldSchema,
  parsePreviewArguments,
} from "../src/agentPreviewModel.ts";

const version = { project_id: "p", modalities: ["text", "structured"] };
const asset = {
  id: "one",
  project_id: "p",
  modality: "text",
  status: "active",
  media_type: "application/pdf",
};

test("preview chooser requires compatible, current, project-owned inputs", () => {
  assert.equal(canPreviewSource(asset, version, "evidence.pdf_page"), true);
  for (const patch of [
    { project_id: "other" },
    { status: "withdrawn" },
    { modality: "audio" },
    { expires_at: "2000-01-01" },
  ]) {
    assert.equal(
      canPreviewSource({ ...asset, ...patch }, version, "evidence.pdf_page"),
      false,
    );
  }
  assert.equal(canPreviewSource(asset, version, "evidence.select_cues"), false);
  const table = { ...asset, media_type: "text/csv", modality: "structured" };
  assert.equal(canPreviewSource(table, version, "table.audit_keys"), false);
  assert.equal(
    canPreviewSource(
      { ...table, parsed_content_available: true },
      version,
      "table.audit_keys",
    ),
    true,
  );
});

test("preview arguments retain side-specific choices and require valid selected sources", () => {
  const nullablePage = {
    anyOf: [{ type: "integer", minimum: 1 }, { type: "null" }],
  };
  const spec = {
    input_schema: {
      properties: {
        source_id: { type: "string" },
        other_source_id: { type: "string" },
        page: nullablePage,
        other_page: nullablePage,
        start: { type: "integer", default: 0 },
      },
      required: ["source_id", "other_source_id"],
    },
  };
  const second = { ...asset, id: "two", media_type: "text/plain" };
  const args = argumentTemplate(spec, ["one", "two"], [asset, second]);
  assert.deepEqual(args, {
    source_id: "one",
    other_source_id: "two",
    page: 1,
    start: 0,
  });
  assert.deepEqual(argumentTemplate(spec, ["two"], [second], args), {
    source_id: "",
    other_source_id: "two",
    start: 0,
  });
  assert.throws(() => checkArguments(spec, args, ["one"]), /勾选/);
  assert.throws(
    () => checkArguments(spec, { ...args, other_source_id: "" }, ["one"]),
    /对照资料/,
  );
  assert.throws(
    () => checkArguments(spec, { ...args, page: 0 }, ["one", "two"]),
    /页码/,
  );
  assert.doesNotThrow(() => checkArguments(spec, args, ["one", "two"]));
  assert.equal(fieldSchema(nullablePage).minimum, 1);
  assert.throws(() => parsePreviewArguments("[]"), /对象/);
});
