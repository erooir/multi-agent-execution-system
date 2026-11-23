import { idPath } from "./api";
import type { Item } from "./types";

export function OriginalDownload({ asset }: { asset?: Item }) {
  if (!asset?.original_file || asset.status !== "active" || Date.parse(asset.expires_at) <= Date.now()) {
    return null;
  }
  return (
    <a className="button" href={`/api${idPath("data-assets", asset.id)}/original`} download>
      下载原件
    </a>
  );
}
