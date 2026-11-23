import { idPath } from "./api";
import type { Item } from "./types";

function originalHref(asset?: Item | null) {
  if (
    !asset?.original_file ||
    asset.status !== "active" ||
    !(Date.parse(asset.expires_at) > Date.now())
  )
    return;
  return `/api${idPath("data-assets", asset.id)}/original`;
}

export function pdfPreviewHref(asset?: Item | null) {
  const href = originalHref(asset);
  if (href && /\.pdf$/i.test(asset?.original_file.filename))
    return href + "?disposition=inline";
}

export function OriginalDownload({ asset }: { asset?: Item }) {
  const href = originalHref(asset);
  if (!href) return null;
  const preview = pdfPreviewHref(asset);
  return (
    <span className="actions wrap">
      {preview && (
        <a className="button" href={preview} target="_blank" rel="noopener">
          打开 PDF
        </a>
      )}
      <a className="button" href={href} download>
        下载原件
      </a>
    </span>
  );
}
