import { test } from "node:test";
import assert from "node:assert/strict";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { remarkReportCitations } from "../src/remarkReportCitations.ts";

const id1 = "doc_0123456789abcdef0123456789abcdef_c0";
const id2 = "doc_fedcba9876543210fedcba9876543210_c1";
function render(source, citations = [{ id: id1 }, { id: id2 }]) {
  return renderToStaticMarkup(
    createElement(
      ReactMarkdown,
      { remarkPlugins: [[remarkReportCitations, { citations }]] },
      source,
    ),
  );
}

test("known citations have compact stable numbers without changing the source", () => {
  const source = `研究结论 [${id2}][${id1}]，再次引用 [${id2}]。`;
  const before = source;
  const html = render(source);
  assert.equal((html.match(/data-report-citation="2"/g) || []).length, 2);
  assert.ok(
    html.includes('href="#report-citation-1" data-report-citation="1">[1]</a>'),
  );
  assert.ok(!html.includes(id1));
  assert.ok(!html.includes(id2));
  assert.equal(source, before);
});

test("links, images, code, reference definitions, and escaped literal brackets retain Markdown meaning", () => {
  const source = `[${id1}](https://example.com)\n\n![${id1}](https://example.com/image.png)\n\n\`[${id1}]\`\n\n\`\`\`text\n[${id1}]\n\`\`\`\n\n[ref][${id1}]\n\n[${id1}]: https://example.com/reference\n\n\\[${id2}]\n\n&#91;${id2}]`;
  const html = render(source);
  assert.ok(!html.includes("data-report-citation="));
  assert.ok(html.includes(`href="https://example.com">${id1}</a>`));
  assert.ok(html.includes(`alt="${id1}"`));
  assert.ok(html.includes(`<code>[${id1}]</code>`));
  assert.ok(html.includes('href="https://example.com/reference">ref</a>'));
  assert.ok(html.includes(`[${id2}]`));
});

test("known IDs are matched literally even when containing regular-expression punctuation", () => {
  const exact = "doc_a.b+c(2)_c1";
  const html = render(`[${exact}] [doc_aXbcccc2_c1]`, [{ id: exact }]);
  assert.equal((html.match(/data-report-citation="1"/g) || []).length, 1);
  assert.ok(
    html.includes('data-report-citation="unresolved">[doc_aXbcccc2_c1]'),
  );
});

test("unknown document IDs remain visible and are explicitly marked unresolved", () => {
  const missing = "doc_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa_c9";
  const html = render(`证据待核实 [${missing}]，普通括号 [说明] 保留。`);
  assert.ok(html.includes(`data-report-citation="unresolved">[${missing}]`));
  assert.ok(html.includes("[说明]"));
});
