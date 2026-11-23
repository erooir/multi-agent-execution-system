import { useRef, useState } from "react";
import { Upload } from "lucide-react";
import { api } from "./api";
import type { Item, Mutation } from "./types";
import { Field, FormModal } from "./ui";

const mediaExtensions = [
  ".mp3",
  ".mp4",
  ".wav",
  ".flac",
  ".mov",
  ".webm",
  ".m4a",
  ".ogg",
];
const extensions = [
  ".txt",
  ".md",
  ".csv",
  ".json",
  ".jsonl",
  ".pdf",
  ".docx",
  ".xlsx",
  ".geojson",
  ".srt",
  ".vtt",
  ".png",
  ".jpg",
  ".jpeg",
  ".gif",
  ".webp",
  ".bmp",
  ".tif",
  ".tiff",
  ...mediaExtensions,
];

type Props = {
  projectId: string;
  mutate: Mutation;
  onUploaded?: (asset: Item) => void;
};

export function ProjectFileUpload(props: Props) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button className="primary" onClick={() => setOpen(true)}>
        <Upload size={16} /> 上传本地文件
      </button>
      {open && <UploadForm {...props} onClose={() => setOpen(false)} />}
    </>
  );
}

function UploadForm({
  projectId,
  mutate,
  onUploaded,
  onClose,
}: Props & { onClose: () => void }) {
  const input = useRef<HTMLInputElement>(null);
  const [extension, setExtension] = useState("");
  const [sourceId] = useState(() => "upload-" + crypto.randomUUID());

  async function upload() {
    const field = input.current;
    const file = field?.files?.[0];
    if (!field?.form || !file) throw new Error("请选择本地文件");
    if (!extensions.includes(extension)) throw new Error("此文件格式暂不支持");
    const maxMB = mediaExtensions.includes(extension) ? 128 : 10;
    if (!file.size || file.size > maxMB * 1_000_000) {
      throw new Error(`请选择非空文件，此类型文件上限为 ${maxMB} MB`);
    }
    const body = new FormData(field.form);
    body.set("project_id", projectId);
    body.set("name", String(body.get("name") || "").trim());
    body.set(
      "source_id",
      String(body.get("source_id") || "").trim() || sourceId,
    );
    body.set(
      "source_version",
      String(body.get("source_version") || "").trim() || "1",
    );
    body.set(
      "provenance",
      String(body.get("provenance") || "").trim() || "工作空间本地上传",
    );
    const asset = await mutate(
      () =>
        api<Item>("/v2/upstream/data-assets/files", { method: "POST", body }),
      "文件已上传到当前工作空间",
    );
    if (field.isConnected && asset.project_id === projectId)
      onUploaded?.(asset);
  }

  return (
    <FormModal
      title="上传本地文件"
      submit="上传文件"
      onSubmit={upload}
      onClose={onClose}
    >
      <Field
        label="本地文件"
        hint="PDF、DOCX、XLSX 等普通文件上限 10 MB，音视频上限 128 MB。文本、JSON 等内联内容上限 2 MB，超过 1.9 MB 的 CSV 按文件保存。"
      >
        <input
          ref={input}
          name="file"
          type="file"
          required
          accept={extensions.join(",")}
          onChange={(event) =>
            setExtension(
              event.target.files?.[0]?.name
                .match(/\.[^.]+$/)?.[0]
                .toLowerCase() || "",
            )
          }
        />
      </Field>
      <Field label="访问范围">
        <select name="classification" defaultValue="local">
          <option value="local">仅本地</option>
          <option value="public">公开资料，可用于已授权的云模型任务</option>
        </select>
      </Field>
      {extension === ".csv" && (
        <Field label="CSV 编码">
          <select name="csv_encoding" defaultValue="utf-8">
            <option value="utf-8">UTF-8</option>
            <option value="windows-1252">Windows-1252</option>
          </select>
        </Field>
      )}
      <details>
        <summary>来源、版本与保留期限</summary>
        <Field label="资料名称">
          <input name="name" maxLength={150} placeholder="默认使用文件名" />
        </Field>
        <Field label="来源说明">
          <input
            name="provenance"
            maxLength={1000}
            placeholder="工作空间本地上传"
          />
        </Field>
        <Field label="来源标识">
          <input
            name="source_id"
            maxLength={200}
            placeholder="留空自动生成；上传新版本时可填写已有标识"
          />
        </Field>
        <Field label="来源版本">
          <input name="source_version" maxLength={100} defaultValue="1" />
        </Field>
        <Field label="保留天数">
          <input
            name="retention_days"
            type="number"
            defaultValue={30}
            min={1}
            max={3650}
            required
          />
        </Field>
      </details>
    </FormModal>
  );
}
