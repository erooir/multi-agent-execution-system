import { useState } from "react";
import type { Item } from "./types";
import { Field, Panel } from "./ui";

const stages: Record<string, string> = {
  prepare: "资料解析",
  analyze: "专业分析",
  review: "独立核查",
};
const productNames: Record<string, string> = {
  clause_map: "双边条款对照",
  unmatched: "无法配对的条款",
  commentary: "意见与说明",
  annual_series: "年度序列",
  anomaly: "基线差值与排名",
  trend_svg: "趋势图",
  od_comparison: "起终点方案对照",
  route_layer: "路线图层",
  exclusions: "不可通行项",
  notice_audit: "公告限制依据",
  map_view: "地图",
  literature_card: "研究阅读卡",
  bibliography: "书目信息",
  interpretation: "结果解读",
  reconciliation: "合计复核",
};

export function caseRoleAgent(agents: Item[], caseId: string, stage: string) {
  return agents.find(
    (agent) =>
      agent.case_role === stage &&
      agent.role_contract &&
      (stage === "analyze"
        ? agent.role_contract.case_id === caseId
        : !agent.parameters?.case_scope &&
          agent.role_contract.shared_cases?.includes(caseId)),
  );
}

export function RoleFacts({
  role,
  tools,
  agent,
  showDuty = true,
}: {
  role: Item;
  tools: Item[];
  agent?: Item;
  showDuty?: boolean;
}) {
  const labels = new Map(tools.map((t) => [t.id, t.label]));
  const local = agent?.executor_bindings?.local;
  const cloud = agent?.executor_bindings?.cloud;
  const dualMode = Boolean(local?.id && cloud?.id);
  return (
    <>
      {showDuty && <p>{role.duty}</p>}
      <dl className="role-facts">
        <dt>执行阶段</dt>
        <dd>{stages[role.stage] || role.stage}</dd>
        {dualMode && (
          <>
            <dt>双模式执行</dt>
            <dd>同一资产：本地固定 role_tools；云端领域 Agno 工具循环。</dd>
            <dt>本地执行器</dt>
            <dd>
              <code>{local.id}</code> · v{local.version}
            </dd>
            <dt>云端领域执行器</dt>
            <dd>
              <code>{cloud.id}</code> · v{cloud.version}
            </dd>
          </>
        )}
        <dt>主算法</dt>
        <dd>
          <code>{role.operation}</code>
        </dd>
        <dt>共享算法</dt>
        <dd>
          {role.shared_cases?.length > 1
            ? role.shared_cases.length > 10
              ? "共 " + role.shared_cases.length + " 种用例"
              : role.shared_cases.join("、")
            : "该用例专用"}
        </dd>
        <dt>输入</dt>
        <dd>{role.inputs?.join("；") || "—"}</dd>
        <dt>交付字段</dt>
        <dd>
          {role.outputs
            ?.map((id: string) => productNames[id] || id)
            .join("、") || "—"}
        </dd>
        <dt>验收检查</dt>
        <dd>
          {role.acceptance?.length ? (
            <ul className="role-checks">
              {role.acceptance.map((check: string) => (
                <li key={check}>{check}</li>
              ))}
            </ul>
          ) : (
            "未声明"
          )}
        </dd>
        <dt>用例岗位工具</dt>
        <dd>
          {role.grants?.length ? (
            <ul className="role-tools">
              {role.grants.map((id: string) => (
                <li key={id}>
                  <span>{labels.get(id) || id}</span>
                  <small>{id}</small>
                </li>
              ))}
            </ul>
          ) : (
            "无独立工具授权"
          )}
        </dd>
      </dl>
    </>
  );
}

export function CaseRoleComparison({
  catalog,
  tools,
  agents,
  initial,
}: {
  catalog: Item[];
  tools: Item[];
  agents: Item[];
  initial: string;
}) {
  const [left, setLeft] = useState(initial);
  const [right, setRight] = useState(initial === "T06" ? "B06" : "T06");
  if (!catalog.length)
    return (
      <Panel title="用例岗位对照">
        <p>尚无可见的用例契约。</p>
      </Panel>
    );
  const first = catalog.find((p) => p.id === left) || catalog[0];
  const second =
    catalog.find((p) => p.id === right && p.id !== first.id) ||
    catalog.find((p) => p.id !== first.id);
  if (!second)
    return (
      <Panel title="用例岗位对照">
        <p>需要两个不同的用例才能比较。</p>
      </Panel>
    );
  const sides = [first, second],
    a = first.role_contract,
    b = second.role_contract;
  const common =
    a?.grants?.filter((id: string) => b?.grants?.includes(id)) || [];
  return (
    <Panel title="用例岗位对照">
      <p className="muted">
        选择两个用例，对照实际处理函数、所需资料、交付检查与工具授权。
      </p>
      <div className="toolbar">
        <button
          onClick={() => {
            setLeft("B06");
            setRight("T06");
          }}
        >
          版本条款 · B06 / T06
        </button>
        <button
          onClick={() => {
            setLeft("G03");
            setRight("G09");
          }}
        >
          地理计算 · G03 / G09
        </button>
      </div>
      <div className="role-pair">
        {sides.map((p, index) => (
          <div key={index} className="role-column">
            <Field label={index === 0 ? "第一个用例" : "第二个用例"}>
              <select
                value={p.id}
                onChange={(e) =>
                  index === 0
                    ? setLeft(e.target.value)
                    : setRight(e.target.value)
                }
              >
                {catalog.map((row) => (
                  <option
                    key={row.id}
                    value={row.id}
                    disabled={row.id === sides[1 - index].id}
                  >
                    {row.id} · {row.title}
                  </option>
                ))}
              </select>
            </Field>
            <h3>{p.title}</h3>
            <small className={p.project_tools_current ? "muted" : "warning"}>
              {p.project_tools_current
                ? "当前发布流程已绑定用例岗位授权"
                : "用例岗位目录配置 · 当前项目尚未绑定此版合同"}
            </small>
            {p.role_contract ? (
              <RoleFacts
                role={p.role_contract}
                tools={tools}
                agent={caseRoleAgent(agents, p.id, "analyze")}
              />
            ) : (
              <p>尚无新版用例岗位合同。</p>
            )}
          </div>
        ))}
      </div>
      {a && b && (
        <div className="role-conclusion">
          <strong>
            {a.operation === b.operation ? "共用同一主算法" : "主算法不同"}
          </strong>
          <p>
            {a.operation === b.operation
              ? "两项均执行 " +
                a.operation +
                "。共享算法不等于相同用例岗位，职责、输入和验收仍按各自合同执行。"
              : a.operation + " 与 " + b.operation + " 分别处理各自的输入。"}
            {common.length
              ? "两份用例岗位合同共同列出 " + common.length + " 项工具。"
              : "两份用例岗位合同列出的独立工具没有重合。"}
          </p>
        </div>
      )}
      {first.roles?.prepare && first.roles?.review && (
        <details className="shared-roles">
          <summary>查看配合执行的解析员和核查员</summary>
          <div className="role-pair">
            {[first.roles.prepare, first.roles.review].map((role: Item) => (
              <section className="role-column" key={role.stage}>
                <h3>{stages[role.stage]}用例岗位</h3>
                <RoleFacts
                  role={role}
                  tools={tools}
                  agent={caseRoleAgent(agents, first.id, role.stage)}
                />
              </section>
            ))}
          </div>
        </details>
      )}
    </Panel>
  );
}
