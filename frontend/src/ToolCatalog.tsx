import { useEffect, useState } from "react";
import {
  ArrowUpRight,
  BookOpen,
  ChevronRight,
  Search,
  ShieldCheck,
  Wrench,
} from "lucide-react";
import { api } from "./api";
import type { Item } from "./types";
import { Empty, JsonView, SectionTitle, Tabs } from "./ui";
import { parameterNames } from "./agentPreviewModel";
import {
  catalogMatches,
  eligibleAgents,
  toolGroup,
  toolGroups,
  type CatalogKind,
} from "./toolCatalogModel";
import "./tool-catalog.css";

type Catalog = { tools: Item[]; skills: Item[]; mcp_servers: Item[] };
type Props = {
  projectId: string;
  agents: Item[];
  canEdit: boolean;
  onAgent: (id: string, toolId?: string) => void;
};

const baseParameters: Record<string, string> = {
  query: "检索关键词",
  quote: "待定位的原文",
  operation: "表格操作",
  sort_by: "排序列名",
  descending: "降序排序",
  sort_type: "排序类型",
  metric: "统计方式",
  group_by: "分组列名",
};

export default function ToolCatalog({
  projectId,
  agents,
  canEdit,
  onAgent,
}: Props) {
  const [response, setResponse] = useState<{
    project: string;
    catalog?: Catalog;
    error?: string;
  }>();
  const [revision, setRevision] = useState(0);
  const [kind, setKind] = useState<CatalogKind>("tools");
  const [query, setQuery] = useState("");
  const [group, setGroup] = useState("");
  const [onlyGranted, setOnlyGranted] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setResponse(undefined);
    api<Catalog>(
      "/v2/capabilities?project_id=" + encodeURIComponent(projectId),
      { signal: controller.signal },
    )
      .then((catalog) => {
        if (!controller.signal.aborted)
          setResponse({ project: projectId, catalog });
      })
      .catch((error: Error) => {
        if (!controller.signal.aborted)
          setResponse({ project: projectId, error: error.message });
      });
    return () => controller.abort();
  }, [projectId, revision]);
  const current = response?.project === projectId ? response : undefined;
  const catalog = current?.catalog;
  const tools = catalog?.tools || [];
  const skills = catalog?.skills || [];
  const items = kind === "tools" ? tools : skills;
  const grants = new Map(
    items.map((item) => [
      item.id,
      eligibleAgents(agents, projectId, item, kind),
    ]),
  );
  const visible = items.filter(
    (item) =>
      (kind === "skills" || !group || toolGroup(item) === group) &&
      (!onlyGranted || grants.get(item.id)?.length) &&
      catalogMatches(
        item,
        query,
        kind === "tools" ? toolGroups[toolGroup(item)] : undefined,
      ),
  );
  const selected = visible.find((item) => item.id === selectedId) || visible[0];
  const holders = selected ? grants.get(selected.id) || [] : [];
  function resetFilters() {
    setQuery("");
    setGroup("");
    setOnlyGranted(false);
    setSelectedId("");
  }
  function chooseKind(next: CatalogKind) {
    resetFilters();
    setKind(next);
  }
  return (
    <div className="tool-catalog">
      <SectionTitle
        eyebrow="项目能力"
        title="工具目录"
        description="查找工具，查看参数和当前项目中有权调用该版本的智能体。"
      />
      {current?.error ? (
        <div role="alert" className="error-line">
          工具目录未能读取。{current.error}{" "}
          <button onClick={() => setRevision((n) => n + 1)}>重新加载</button>
        </div>
      ) : !catalog ? (
        <p role="status" className="loading">
          正在读取工具目录…
        </p>
      ) : (
        <>
          <Tabs
            value={kind}
            onChange={(id) => chooseKind(id as CatalogKind)}
            items={[
              { id: "tools", label: "工具 · " + tools.length },
              { id: "skills", label: "文档技能包 · " + skills.length },
            ]}
          />
          <div className="toolbar catalog-controls">
            <label className="search">
              <Search size={17} aria-hidden="true" />
              <input
                aria-label="搜索工具或技能（Skill）"
                placeholder="搜索名称、分类或标识"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </label>
            {kind === "tools" && (
              <select
                aria-label="工具分类"
                value={group}
                onChange={(e) => setGroup(e.target.value)}
              >
                <option value="">全部分类</option>
                {Object.entries(toolGroups).map(([id, label]) => {
                  const count = tools.filter(
                    (tool) => toolGroup(tool) === id,
                  ).length;
                  return (
                    count > 0 && (
                      <option key={id} value={id}>
                        {label} · {count}
                      </option>
                    )
                  );
                })}
              </select>
            )}
            <label className="catalog-authorized">
              <input
                type="checkbox"
                checked={onlyGranted}
                onChange={(e) => setOnlyGranted(e.target.checked)}
              />
              仅看本项目已授权
            </label>
            <span role="status" className="muted">
              {visible.length} 项匹配
            </span>
          </div>
          {!selected ? (
            <Empty
              title="没有匹配项"
              description="可调整关键词或项目授权筛选。"
              action={<button onClick={resetFilters}>清除筛选</button>}
            />
          ) : (
            <div className="catalog-layout">
              <div className="catalog-index" aria-label="目录条目">
                {visible.map((item) => (
                  <button
                    key={item.id}
                    className="catalog-entry"
                    aria-pressed={item.id === selected.id}
                    onClick={() => setSelectedId(item.id)}
                  >
                    <span>
                      <small>
                        {kind === "tools"
                          ? toolGroups[toolGroup(item)]
                          : "文档技能包"}
                      </small>
                      <strong>{item.label || item.name}</strong>
                      <code>{item.id}</code>
                    </span>
                    <ChevronRight size={16} aria-hidden="true" />
                  </button>
                ))}
              </div>
              <section
                className="panel catalog-detail"
                aria-label="所选目录条目"
                key={selected.id}
              >
                <span className="catalog-kicker">
                  {kind === "tools" ? (
                    <Wrench size={16} />
                  ) : (
                    <BookOpen size={16} />
                  )}
                  {kind === "tools"
                    ? toolGroups[toolGroup(selected)]
                    : "文档技能包"}
                </span>
                <h2>{selected.label || selected.name}</h2>
                <p className="catalog-identifier">
                  <code>{selected.id}</code>
                  <span>v{selected.version}</span>
                </p>
                {kind === "tools" ? (
                  <>
                    <div className="tags">
                      {selected.read_only && <span>只读</span>}
                      <span>
                        {selected.local_preview
                          ? "支持本地试用"
                          : "通过任务调用"}
                      </span>
                    </div>
                    {Object.keys(selected.input_schema?.properties || {})
                      .length ? (
                      <details className="catalog-parameters">
                        <summary>
                          输入参数 ·{" "}
                          {Object.keys(selected.input_schema.properties).length}{" "}
                          项，其中 {selected.input_schema.required?.length || 0}{" "}
                          项必填
                        </summary>
                        <ul className="catalog-fields">
                          {Object.entries<Item>(
                            selected.input_schema.properties,
                          ).map(([name, field]) => (
                            <li key={name}>
                              <span>
                                <strong>
                                  {parameterNames[name] ||
                                    baseParameters[name] ||
                                    field.title ||
                                    name}
                                </strong>
                                <code>{name}</code>
                              </span>
                              <span>
                                {selected.input_schema.required?.includes(name)
                                  ? "必填"
                                  : "可选"}
                                {field.description && " · " + field.description}
                              </span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    ) : (
                      <p className="muted">无需输入参数。</p>
                    )}
                    <JsonView
                      label="查看输入与输出契约"
                      value={{
                        input: selected.input_schema,
                        output: selected.output_schema,
                      }}
                    />
                    {selected.kind === "mcp" &&
                      catalog.mcp_servers.length > 0 && (
                        <p className="catalog-note">
                          已登记 MCP 服务 ·{" "}
                          {catalog.mcp_servers
                            .map(
                              (s) =>
                                s.id +
                                " v" +
                                s.version +
                                " (" +
                                s.transport +
                                ")",
                            )
                            .join("、")}
                        </p>
                      )}
                  </>
                ) : (
                  <>
                    <p>{selected.description}</p>
                    <h3>文档技能包依赖的工具</h3>
                    <div className="catalog-dependencies">
                      {selected.required_tools?.map((name: string) => {
                        const tool = tools.find((item) => item.name === name);
                        return tool ? (
                          <button
                            key={name}
                            onClick={() => {
                              chooseKind("tools");
                              setSelectedId(tool.id);
                            }}
                          >
                            {tool.label}
                            <ArrowUpRight size={13} />
                          </button>
                        ) : (
                          <code key={name}>{name}</code>
                        );
                      })}
                      {!selected.required_tools?.length && (
                        <span className="muted">
                          此文档技能包未声明依赖工具。
                        </span>
                      )}
                    </div>
                    {selected.references?.length > 0 && (
                      <p className="catalog-note">
                        参考文件 · {selected.references.join("、")}
                      </p>
                    )}
                  </>
                )}
                <section
                  className="catalog-holders"
                  aria-label="本项目授权智能体"
                >
                  <h3>
                    <ShieldCheck size={17} />
                    本项目授权智能体 <span>{holders.length}</span>
                  </h3>
                  <p className="muted">
                    按当前启用的发布版本核对授权。每次任务实际调用的工具可在任务详情查看。
                  </p>
                  {holders.length ? (
                    <ul>
                      {holders.map((agent) => (
                        <li key={agent.id}>
                          <span>
                            <strong>{agent.name}</strong>
                            <small>智能体 v{agent.current_version}</small>
                          </span>
                          <div>
                            <button
                              aria-label={"查看" + agent.name + "智能体"}
                              onClick={() => onAgent(agent.id)}
                            >
                              查看智能体
                              <ArrowUpRight size={13} />
                            </button>
                            {canEdit &&
                              kind === "tools" &&
                              selected.local_preview && (
                                <button
                                  aria-label={"用" + agent.name + "试用"}
                                  onClick={() => onAgent(agent.id, selected.id)}
                                >
                                  本地试用
                                  <ChevronRight size={13} />
                                </button>
                              )}
                          </div>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="catalog-note">
                      本项目暂无启用的智能体获授这个版本。
                    </p>
                  )}
                </section>
                {selected.checksum && (
                  <details className="catalog-checksum">
                    <summary>版本校验值</summary>
                    <code>{selected.checksum}</code>
                  </details>
                )}
              </section>
            </div>
          )}
        </>
      )}
    </div>
  );
}
