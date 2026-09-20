type Citation = { id?: string } | string;
type MarkdownNode = {
  type: string;
  value?: string;
  children?: MarkdownNode[];
  position?: { start: { offset?: number }; end: { offset?: number } };
  [key: string]: unknown;
};

const escapeRegExp = (value: string) =>
  value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const protectedNodes = new Set([
  "link",
  "linkReference",
  "image",
  "imageReference",
  "code",
  "inlineCode",
  "html",
  "definition",
]);

/** Transform display-only text nodes; never rewrite the stored Markdown source. */
export function remarkReportCitations({
  citations,
}: {
  citations: Citation[];
}) {
  const known = new Map<string, number>();
  citations.forEach((citation, index) => {
    if (
      typeof citation !== "string" &&
      citation.id &&
      !known.has(`[${citation.id}]`)
    )
      known.set(`[${citation.id}]`, index + 1);
  });
  const pattern = [...known.keys()]
    .map(escapeRegExp)
    .concat("\\[doc_[^\\]\\s]+\\]")
    .join("|");

  return (tree: MarkdownNode, file: { value?: unknown }) => {
    const source = typeof file?.value === "string" ? file.value : undefined;
    function splitText(node: MarkdownNode): MarkdownNode[] {
      const text = node.value || "";
      const raw =
        source !== undefined &&
        node.position?.start.offset !== undefined &&
        node.position.end.offset !== undefined
          ? source.slice(node.position.start.offset, node.position.end.offset)
          : text;
      // Markdown escapes/entities are intentional literal text, not citation markup.
      const rawOccurrences = new Map<string, boolean[]>();
      for (const match of raw.matchAll(new RegExp(pattern, "g"))) {
        let slashes = 0;
        for (let i = match.index! - 1; i >= 0 && raw[i] === "\\"; i--)
          slashes++;
        rawOccurrences.set(match[0], [
          ...(rawOccurrences.get(match[0]) || []),
          slashes % 2 === 0,
        ]);
      }
      const result: MarkdownNode[] = [];
      let cursor = 0;
      for (const match of text.matchAll(new RegExp(pattern, "g"))) {
        if (!rawOccurrences.get(match[0])?.shift()) continue;
        if (match.index! > cursor)
          result.push({ type: "text", value: text.slice(cursor, match.index) });
        const number = known.get(match[0]);
        result.push({
          type: "link",
          url: number
            ? `#report-citation-${number}`
            : "#report-citation-unresolved",
          data: {
            hProperties: {
              "data-report-citation": number ? String(number) : "unresolved",
            },
          },
          children: [
            { type: "text", value: number ? `[${number}]` : match[0] },
          ],
        });
        cursor = match.index! + match[0].length;
      }
      if (!result.length) return [node];
      if (cursor < text.length)
        result.push({ type: "text", value: text.slice(cursor) });
      return result;
    }
    function visit(node: MarkdownNode) {
      if (protectedNodes.has(node.type) || !node.children) return;
      node.children = node.children.flatMap((child) => {
        if (child.type === "text") return splitText(child);
        visit(child);
        return [child];
      });
    }
    visit(tree);
  };
}
