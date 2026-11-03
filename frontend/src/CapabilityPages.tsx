import { useEffect, useState } from "react";
import { Boxes, Plus, RefreshCw } from "lucide-react";
import { api, idPath, post } from "./api";
import { parseObject, splitList, time } from "./domain";
import type { Item, Mutation, Notify } from "./types";
import {
  Badge,
  Empty,
  ErrorLine,
  Field,
  FormModal,
  JsonView,
  Modal,
  Panel,
  SectionTitle,
} from "./ui";

type Props = {
  projectId: string;
  canEdit: boolean;
  isAdmin: boolean;
  mutate: Mutation;
  notify: Notify;
};

const kindNames: Record<string, string> = {
  skill: "文档技能包",
  agno_toolkit: "Agno 工具包",
  http_tool: "HTTP JSON 工具",
  mcp_server: "MCP 服务",
};

export default function CapabilityPages({
  projectId,
  canEdit,
  isAdmin,
  mutate,
  notify,
}: Props) {
  const [definitions, setDefinitions] = useState<Item[]>([]),
    [catalog, setCatalog] = useState<Item>({ tools: [], skills: [] }),
    [error, setError] = useState(""),
    [create, setCreate] = useState<string | null>(null),
    [publish, setPublish] = useState<Item | null>(null),
    [versions, setVersions] = useState<Item | null>(null),
    [mapping, setMapping] = useState("{}"),
    [form, setForm] = useState<Item>({});

  async function load() {
    try {
      const query = encodeURIComponent(projectId);
      const [items, available] = await Promise.all([
        api<Item[]>(`/v2/capability-definitions?project_id=${query}`),
        api<Item>(`/v2/capabilities?project_id=${query}`),
      ]);
      setDefinitions(items);
      setCatalog(available);
      setError("");
    } catch (e) {
      setError((e as Error).message);
    }
  }
  useEffect(() => {
    void load();
  }, [projectId]);

  function open(kind: string) {
    setForm({
      name: "",
      description: "",
      provider: "calculator",
      include_tools: "",
      transport: "stdio",
      url: "",
      launcher_id: "",
      secret_ref: "",
      input_schema: '{"type":"object","properties":{}}',
      output_schema: '{"type":"object"}',
    });
    setCreate(kind);
  }

  async function action(item: Item, name: string) {
    await mutate(
      () => post(`${idPath("capability-definitions", item.id)}/${name}`),
      `接入能力${name === "revoke" ? "撤销" : "状态"}已更新`,
    );
    await load();
  }

  return (
    <>
      <SectionTitle
        eyebrow="NATIVE CAPABILITIES"
        title="扩展接入"
        description="优先使用文档技能包、Agno 工具包与 MCP 服务；项目负责版本、权限、审计和撤销。"
        actions={
          <>
            <button aria-label="刷新接入能力目录" onClick={() => void load()}>
              <RefreshCw size={17} />
            </button>
            {canEdit && (
              <button onClick={() => open("skill")}>导入文档技能包</button>
            )}
            {isAdmin && (
              <button className="primary" onClick={() => open("agno_toolkit")}>
                <Plus size={17} /> 扩展接入
              </button>
            )}
          </>
        }
      />
      {error && <ErrorLine>{error}</ErrorLine>}
      <div className="metric-grid three">
        <div className="metric">
          <span>已登记接入能力</span>
          <strong>{definitions.length}</strong>
        </div>
        <div className="metric">
          <span>可授权工具 / MCP</span>
          <strong>{catalog.tools?.length || 0}</strong>
        </div>
        <div className="metric">
          <span>可授权技能（Skill）</span>
          <strong>{catalog.skills?.length || 0}</strong>
        </div>
      </div>
      <Panel title="项目接入能力">
        {definitions.length ? (
          <div className="asset-grid">
            {definitions.map((item) => (
              <Panel key={item.id}>
                <div className="row-between">
                  <span className="asset-icon">
                    <Boxes size={23} />
                  </span>
                  <Badge status={item.status} />
                </div>
                <h3>{item.name}</h3>
                <p>{kindNames[item.kind] || item.kind}</p>
                <p className="muted">
                  平台版本 v{item.current_version || "—"} ·{" "}
                  {item.native_driver || "Agno"}
                </p>
                {item.upstream_version && (
                  <small>上游版本 {item.upstream_version}</small>
                )}
                <div className="actions">
                  {item.kind === "mcp_server" &&
                    isAdmin &&
                    item.status !== "revoked" && (
                      <button onClick={() => void action(item, "discover")}>
                        原生发现
                      </button>
                    )}
                  {canEdit &&
                    (item.kind === "skill" || isAdmin) &&
                    item.status !== "revoked" && (
                      <button
                        onClick={() => {
                          setPublish(item);
                          setMapping("{}");
                        }}
                      >
                        发布新版本
                      </button>
                    )}
                  <button
                    onClick={async () => {
                      const history = await api<Item[]>(
                        `${idPath("capability-definitions", item.id)}/versions`,
                      );
                      setVersions({ ...item, history });
                    }}
                  >
                    版本
                  </button>
                  {canEdit && item.status === "published" && (
                    <button onClick={() => void action(item, "disable")}>
                      停用
                    </button>
                  )}
                  {canEdit && item.status === "disabled" && (
                    <button onClick={() => void action(item, "enable")}>
                      启用
                    </button>
                  )}
                  {canEdit &&
                    !["revoked", "destroyed"].includes(item.status) && (
                      <button
                        className="danger subtle"
                        onClick={() => void action(item, "revoke")}
                      >
                        撤销
                      </button>
                    )}
                </div>
                <JsonView value={item} label="定义与来源元数据" />
              </Panel>
            ))}
          </div>
        ) : (
          <Empty
            title="尚未接入外部能力"
            description="可导入文档技能包，或由管理员登记 Agno 工具包、只读 HTTP JSON 工具和 MCP 服务。"
          />
        )}
      </Panel>

      {create && (
        <FormModal
          wide
          title={create === "skill" ? "导入文档技能包" : "接入外部能力"}
          onClose={() => setCreate(null)}
          onSubmit={async () => {
            if (create === "skill") {
              if (!form.file) throw new Error("请选择文档技能包 ZIP 文件");
              const body = new FormData();
              body.append("project_id", projectId);
              body.append("file", form.file);
              await mutate(
                () =>
                  api("/v2/capability-definitions/skills/import", {
                    method: "POST",
                    body,
                  }),
                "文档技能包已导入",
              );
            } else {
              const base = {
                project_id: projectId,
                name: form.name,
                description: form.description,
              };
              const path =
                create === "agno_toolkit"
                  ? "toolkits"
                  : create === "http_tool"
                    ? "tools"
                    : "mcp";
              const body =
                create === "agno_toolkit"
                  ? {
                      ...base,
                      provider: form.provider,
                      include_tools: splitList(form.include_tools),
                    }
                  : create === "http_tool"
                    ? {
                        ...base,
                        url: form.url,
                        secret_ref: form.secret_ref || null,
                        input_schema: parseObject(
                          form.input_schema,
                          "输入 Schema",
                        ),
                        output_schema: parseObject(
                          form.output_schema,
                          "输出 Schema",
                        ),
                      }
                    : {
                        ...base,
                        transport: form.transport,
                        url: form.url,
                        launcher_id: form.launcher_id,
                        secret_ref: form.secret_ref || null,
                      };
              await mutate(
                () => post(`/v2/capability-definitions/${path}`, body),
                "接入能力已保存",
              );
            }
            await load();
          }}
        >
          {create === "skill" ? (
            <Field
              label="文档技能包 ZIP"
              hint="仅 SKILL.md 与 references 文本；由 Agno LocalSkills 校验，不执行脚本。"
            >
              <input
                required
                type="file"
                accept=".zip"
                onChange={(e) =>
                  setForm({ ...form, file: e.target.files?.[0] })
                }
              />
            </Field>
          ) : (
            <>
              {create !== "agno_toolkit" && (
                <Field label="接入类型">
                  <select
                    value={create}
                    onChange={(e) => setCreate(e.target.value)}
                  >
                    <option value="agno_toolkit">Agno 工具包</option>
                    <option value="http_tool">只读 HTTP JSON 工具</option>
                    <option value="mcp_server">MCP 服务</option>
                  </select>
                </Field>
              )}
              <Field label="名称">
                <input
                  required
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                />
              </Field>
              <Field label="说明">
                <textarea
                  value={form.description}
                  onChange={(e) =>
                    setForm({ ...form, description: e.target.value })
                  }
                />
              </Field>
              {create === "agno_toolkit" && (
                <>
                  <Field label="Agno 工具包">
                    <select
                      value={form.provider}
                      onChange={(e) =>
                        setForm({ ...form, provider: e.target.value })
                      }
                    >
                      <option value="calculator">CalculatorTools · 计算</option>
                      <option value="duckduckgo">
                        DuckDuckGoTools · 公开搜索
                      </option>
                    </select>
                  </Field>
                  <Field
                    label="允许功能"
                    hint="英文逗号分隔；留空表示该工具包的默认功能。发布前由 Agno 校验。"
                  >
                    <input
                      value={form.include_tools}
                      onChange={(e) =>
                        setForm({ ...form, include_tools: e.target.value })
                      }
                      placeholder="add, multiply"
                    />
                  </Field>
                </>
              )}
              {create === "http_tool" && (
                <>
                  <Field label="固定 POST URL">
                    <input
                      required
                      type="url"
                      value={form.url}
                      onChange={(e) =>
                        setForm({ ...form, url: e.target.value })
                      }
                    />
                  </Field>
                  <Field
                    label="密钥引用"
                    hint="只填服务端环境变量名，不填密钥值。"
                  >
                    <input
                      value={form.secret_ref}
                      onChange={(e) =>
                        setForm({ ...form, secret_ref: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="输入 JSON Schema">
                    <textarea
                      className="mono"
                      rows={5}
                      value={form.input_schema}
                      onChange={(e) =>
                        setForm({ ...form, input_schema: e.target.value })
                      }
                    />
                  </Field>
                  <Field label="输出 JSON Schema">
                    <textarea
                      className="mono"
                      rows={5}
                      value={form.output_schema}
                      onChange={(e) =>
                        setForm({ ...form, output_schema: e.target.value })
                      }
                    />
                  </Field>
                </>
              )}
              {create === "mcp_server" && (
                <>
                  <Field label="传输">
                    <select
                      value={form.transport}
                      onChange={(e) =>
                        setForm({ ...form, transport: e.target.value })
                      }
                    >
                      <option value="stdio">stdio · 管理员预装 launcher</option>
                      <option value="streamable_http">Streamable HTTP</option>
                    </select>
                  </Field>
                  {form.transport === "stdio" ? (
                    <Field label="Launcher ID">
                      <input
                        required
                        value={form.launcher_id}
                        onChange={(e) =>
                          setForm({ ...form, launcher_id: e.target.value })
                        }
                      />
                    </Field>
                  ) : (
                    <Field label="MCP URL">
                      <input
                        required
                        type="url"
                        value={form.url}
                        onChange={(e) =>
                          setForm({ ...form, url: e.target.value })
                        }
                      />
                    </Field>
                  )}
                  <Field label="密钥引用">
                    <input
                      value={form.secret_ref}
                      onChange={(e) =>
                        setForm({ ...form, secret_ref: e.target.value })
                      }
                    />
                  </Field>
                </>
              )}
            </>
          )}
        </FormModal>
      )}

      {publish && (
        <FormModal
          title={`发布「${publish.name}」新版本`}
          onClose={() => setPublish(null)}
          onSubmit={async () => {
            await mutate(
              () =>
                post(
                  `${idPath("capability-definitions", publish.id)}/publish`,
                  {
                    tool_bindings:
                      publish.kind === "skill"
                        ? parseObject(mapping, "工具映射")
                        : {},
                  },
                ),
              "新版本已发布",
            );
            await load();
          }}
        >
          {publish.kind === "skill" ? (
            <Field
              label="工具映射 JSON"
              hint="将技能（Skill）的 allowed-tools 名称映射到平台工具 ID；不会静默扩大权限。"
            >
              <textarea
                className="mono"
                rows={5}
                value={mapping}
                onChange={(e) => setMapping(e.target.value)}
              />
            </Field>
          ) : (
            <p className="note">
              发布会冻结当前定义或原生 MCP 发现快照，历史版本保持不变。
            </p>
          )}
        </FormModal>
      )}

      {versions && (
        <Modal
          wide
          title={`${versions.name} · 版本`}
          onClose={() => setVersions(null)}
        >
          <div className="modal-body">
            {versions.history?.length ? (
              versions.history.map((item: Item) => (
                <article className="record" key={item.id}>
                  <strong>v{item.version}</strong>
                  <p className="muted">
                    {time(item.created_at)} · {item.native_driver || "Agno"} ·{" "}
                    {item.checksum?.slice(0, 16)}
                  </p>
                  <JsonView value={item.spec} label="不可变定义或发现快照" />
                </article>
              ))
            ) : (
              <Empty title="尚未发布版本" />
            )}
          </div>
        </Modal>
      )}
    </>
  );
}
