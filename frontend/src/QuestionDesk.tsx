import { useEffect, useRef, useState, type FormEvent } from "react";
import { MessageSquareText, RefreshCw, Send } from "lucide-react";
import { api, ApiError, post } from "./api";
import { time } from "./domain";
import {
  QuestionProducts,
  type AnswerMap,
  type AnswerTable,
} from "./QuestionProducts";
import "./question-desk.css";

type Question = {
  id: string;
  question: string;
  status: string;
  outcome?: string | null;
  answer?: string | null;
  missing?: string[];
  citations?: { source: string; location: string; excerpt: string }[];
  maps?: AnswerMap[];
  tables?: AnswerTable[];
  created_at: string;
  updated_at: string;
  content_withheld?: boolean;
};

const active = new Set([
  "accepted",
  "planning",
  "running",
  "evaluating",
  "revising",
]);
const statusText: Record<string, string> = {
  accepted: "已收到",
  planning: "处理中",
  running: "处理中",
  evaluating: "正在核对答案",
  revising: "正在补充核对",
  succeeded: "已完成",
  partial: "部分完成",
  failed: "未能完成",
  cancelled: "已取消",
};

function stateOf(q: Question) {
  if (q.content_withheld) return "内容已受限";
  if (q.outcome === "unsupported") return "未能识别问题";
  if (q.outcome === "missing_inputs") return "缺少材料";
  if (q.outcome === "ambiguous") return "问题范围不明确";
  if (q.outcome === "scope_mismatch") return "资料范围不符";
  if (q.outcome === "unavailable") return "暂不可处理";
  return statusText[q.status] || "状态待确认";
}

function userError(e: unknown) {
  if (e instanceof ApiError) {
    if (e.status === 401) return "登录已过期，请重新登录。";
    if (e.status === 403) return "当前账号没有访问权限。";
    if (e.status === 404) return "这条问题已不可查看。";
    if (e.status === 409)
      return "提交内容发生变化。请刷新问题列表查看最新记录。";
    if (e.status === 422) return "请用完整文字说明希望解答的问题。";
    if (e.status === 429) return "请求过于频繁，请稍后重试。";
  }
  return "暂时未能连接服务，请稍后重试。";
}

function storedChoice(key: string) {
  try {
    return sessionStorage.getItem(key);
  } catch {
    return null;
  }
}

export default function QuestionDesk(props: {
  projectId: string;
  canSubmit: boolean;
}) {
  return <ProjectQuestions key={props.projectId} {...props} />;
}

function ProjectQuestions({
  projectId,
  canSubmit,
}: {
  projectId: string;
  canSubmit: boolean;
}) {
  const selectionKey = "hangzhi.question.selection." + projectId;
  const [text, setText] = useState("");
  const [questions, setQuestions] = useState<Question[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Question | null>(null);
  const [listError, setListError] = useState("");
  const [detailError, setDetailError] = useState("");
  const [sendError, setSendError] = useState("");
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [listTick, setListTick] = useState(0);
  const [detailTick, setDetailTick] = useState(0);
  const submission = useRef<{ text: string; key: string } | null>(null);
  const submitting = useRef(false);
  const listSequence = useRef(0);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!projectId) return;
    const controller = new AbortController();
    const sequence = ++listSequence.current;
    api<Question[]>(
      "/v2/questions?project_id=" + encodeURIComponent(projectId),
      { signal: controller.signal },
    )
      .then((rows) => {
        if (controller.signal.aborted || sequence !== listSequence.current)
          return;
        setQuestions(rows);
        setSelected((old) => {
          const preferred = old || storedChoice(selectionKey);
          return rows.some((q) => q.id === preferred)
            ? preferred!
            : rows[0]?.id || "";
        });
        setListError("");
      })
      .catch((e) => {
        if (!controller.signal.aborted && sequence === listSequence.current) {
          setListError(userError(e));
          if (e instanceof ApiError && [401, 403, 404].includes(e.status)) {
            setSelected("");
            setDetail(null);
            setQuestions([]);
          }
        }
      })
      .finally(() => !controller.signal.aborted && setLoading(false));
    return () => controller.abort();
  }, [projectId, selectionKey, listTick]);

  const otherQuestionsInProgress = questions.some(
    (row) => row.id !== selected && active.has(row.status),
  );
  useEffect(() => {
    if (!otherQuestionsInProgress) return;
    const timer = window.setInterval(() => setListTick((v) => v + 1), 6000);
    return () => window.clearInterval(timer);
  }, [otherQuestionsInProgress]);

  useEffect(() => {
    if (!selected) return;
    try {
      sessionStorage.setItem(selectionKey, selected);
    } catch {
      /* The history remains available on the server. */
    }
    const controller = new AbortController();
    let timer: number;
    async function read() {
      let delay = 10000;
      try {
        const row = await api<Question>(
          "/v2/questions/" + encodeURIComponent(selected),
          { signal: controller.signal },
        );
        if (controller.signal.aborted || row.id !== selected) return;
        setDetail(row);
        setQuestions((rows) =>
          rows.map((q) =>
            q.id === row.id
              ? {
                  ...q,
                  status: row.status,
                  outcome: row.outcome,
                  content_withheld: row.content_withheld,
                  updated_at: row.updated_at,
                }
              : q,
          ),
        );
        setDetailError("");
        if (active.has(row.status)) delay = 1600;
      } catch (e) {
        if (controller.signal.aborted) return;
        setDetail(null);
        setDetailError(userError(e));
        if (e instanceof ApiError && [401, 403, 404].includes(e.status)) return;
      }
      if (!controller.signal.aborted) timer = window.setTimeout(read, delay);
    }
    setDetailError("");
    void read();
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [selected, selectionKey, detailTick]);

  async function send(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const question = text.trim();
    if (!canSubmit || !projectId || !question || submitting.current) return;
    if (submission.current?.text !== question)
      submission.current = { text: question, key: crypto.randomUUID() };
    submitting.current = true;
    setSending(true);
    setSendError("");
    try {
      const row: Question = await post("/v2/questions", {
        project_id: projectId,
        question,
        idempotency_key: submission.current.key,
      });
      if (!mounted.current) return;
      ++listSequence.current;
      setLoading(false);
      setQuestions((rows) => [row, ...rows.filter((q) => q.id !== row.id)]);
      setSelected(row.id);
      setDetail(row);
      setDetailError("");
      setListError("");
      setText("");
      submission.current = null;
      setListTick((v) => v + 1);
    } catch (e) {
      if (mounted.current) setSendError(userError(e));
    } finally {
      submitting.current = false;
      if (mounted.current) setSending(false);
    }
  }

  const summary = questions.find((q) => q.id === selected);
  const current =
    detail?.id === selected
      ? summary?.content_withheld
        ? summary
        : detail
      : null;
  const waiting = current && active.has(current.status);
  return (
    <section className="question-desk" aria-label="问题工作台">
      <header className="question-intro">
        <span className="question-icon">
          <MessageSquareText size={24} aria-hidden="true" />
        </span>
        <div>
          <h1>问题工作台</h1>
          <p>写下你的问题，系统会结合当前项目已有的资料作答。</p>
        </div>
      </header>
      {canSubmit && projectId ? (
        <form className="question-compose" onSubmit={send}>
          <label htmlFor="question-text">你的问题</label>
          <textarea
            id="question-text"
            rows={3}
            maxLength={8000}
            value={text}
            onChange={(e) => {
              setText(e.target.value);
              setSendError("");
            }}
            disabled={sending}
            placeholder="请说明想了解的对象、时间范围，以及希望得到什么答案。"
            required
          />
          <div className="question-submit">
            <small>
              {text.length >= 7500
                ? `已输入 ${text.length} / 8000 字。`
                : "提交后可在下方查看进展和答案。"}
            </small>
            <button
              className="primary"
              disabled={sending || !text.trim()}
              type="submit"
            >
              <Send size={16} aria-hidden="true" />
              {sending ? "正在提交…" : "提交问题"}
            </button>
          </div>
          {sendError && (
            <p role="alert" className="error-line">
              {sendError}
            </p>
          )}
        </form>
      ) : (
        <p className="note">
          {projectId
            ? "当前账号可以查看自己的历史问题。"
            : "当前账号尚无可访问的项目。"}
        </p>
      )}
      {projectId && (
        <div className="question-work">
          <section className="question-history" aria-label="我的问题">
            <div className="question-history-header">
              <h2>我的问题</h2>
              <button
                onClick={() => {
                  setListTick((v) => v + 1);
                  setDetailTick((v) => v + 1);
                }}
                aria-label="刷新我的问题"
              >
                <RefreshCw size={15} aria-hidden="true" />
              </button>
            </div>
            {listError && (
              <p role="alert" className="error-line">
                {listError}
              </p>
            )}
            {!questions.length ? (
              <p className="muted">
                {loading
                  ? "正在读取历史…"
                  : listError
                    ? "历史暂不可用。"
                    : "当前项目还没有你的问题。"}
              </p>
            ) : (
              <ul>
                {questions.map((q) => (
                  <li key={q.id}>
                    <button
                      type="button"
                      aria-current={q.id === selected ? "true" : undefined}
                      onClick={() => setSelected(q.id)}
                    >
                      <span>{q.question}</span>
                      <small>
                        {stateOf(q)} · {time(q.created_at)}
                      </small>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
          <section className="question-response" aria-label="问题答复">
            <div className="question-response-heading">
              <h2>问题答复</h2>
              {current && (
                <span
                  className="question-state"
                  role="status"
                  data-tone={
                    waiting
                      ? "progress"
                      : current.status === "succeeded" &&
                          !current.content_withheld
                        ? "done"
                        : "gap"
                  }
                >
                  {stateOf(current)}
                </span>
              )}
            </div>
            {detailError ? (
              <>
                <p role="alert" className="error-line">
                  {detailError}
                </p>
                <button
                  className="question-retry"
                  onClick={() => setDetailTick((v) => v + 1)}
                >
                  重新读取
                </button>
              </>
            ) : !current ? (
              <p>
                {selected
                  ? "正在读取这条问题…"
                  : "提交问题后，答复会显示在这里。"}
              </p>
            ) : (
              <>
                <h3>{current.question}</h3>
                <time dateTime={current.updated_at}>
                  {time(current.updated_at)}
                </time>
                {current.content_withheld ? (
                  <p>相关资料已不可访问，答复和出处已收起。</p>
                ) : (
                  <>
                    {current.answer && (
                      <div className="question-answer">{current.answer}</div>
                    )}
                    <QuestionProducts
                      tables={current.tables}
                      maps={current.maps}
                    />
                    {!current.answer && waiting && (
                      <p>
                        系统正在处理，进展会自动更新。你也可以稍后回来查看。
                      </p>
                    )}
                    {!current.answer &&
                      !waiting &&
                      !current.tables?.length &&
                      !current.maps?.length &&
                      !current.missing?.length && (
                        <p>这条问题没有可显示的答复。</p>
                      )}
                    {!!current.missing?.length && (
                      <>
                        <h3>尚未解决</h3>
                        <ul className="answer-list">
                          {current.missing.map((item, i) => (
                            <li key={i}>{item}</li>
                          ))}
                        </ul>
                      </>
                    )}
                    {!!current.citations?.length && (
                      <details>
                        <summary>
                          查看引用资料（{current.citations.length}）
                        </summary>
                        <ul className="answer-list">
                          {current.citations.map((cite, i) => (
                            <li key={i}>
                              <strong>{cite.source}</strong>
                              {cite.location && " · " + cite.location}
                              {cite.excerpt && (
                                <blockquote>{cite.excerpt}</blockquote>
                              )}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </>
                )}
              </>
            )}
          </section>
        </div>
      )}
    </section>
  );
}
