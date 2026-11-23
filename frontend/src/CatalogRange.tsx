import type { Item } from "./types";

export function CatalogRange({
  assets,
  includeSupplementary,
  onChange,
  disabled = false,
}: {
  assets: Item[];
  includeSupplementary: boolean;
  onChange: (include: boolean) => void;
  disabled?: boolean;
}) {
  if (!assets.some((asset) => asset.case_library_role === "supplementary"))
    return null;
  return (
    <select
      aria-label="资料范围"
      value={includeSupplementary ? "all" : "primary"}
      onChange={(event) => onChange(event.target.value === "all")}
      disabled={disabled}
    >
      <option value="primary">主要资料</option>
      <option value="all">全部资料</option>
    </select>
  );
}
