import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const USER_ID = "buyer_001";
const T = {
  brand: "知微 ZHIWEI",
  chat: "电商客服",
  knowledge: "手册入库",
  evaluation: "自动评测",
  chatTitle: "电商客服会话",
  knowledgeTitle: "Osmo Pocket 手册入库",
  evaluationTitle: "系统自动评测报告",
  greeting: "你好，我是知微电商智能客服。可以帮你查询订单、物流、售后和商品规则。",
  quick: "快捷场景",
  uploadManual: "批量导入手册",
  delete: "删除",
  reindex: "重建索引",
};

type View = "chat" | "knowledge" | "eval";
type Message = { role: "user" | "assistant"; content: string; route?: string };
type SpeechRecognitionLike = { lang: string; interimResults: boolean; continuous: boolean; onstart: (() => void) | null; onend: (() => void) | null; onerror: ((event: { error: string }) => void) | null; onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null; start: () => void; stop: () => void; abort: () => void };
type SpeechRecognitionFactory = new () => SpeechRecognitionLike;
type KnowledgeDocument = { document_id: string; filename: string; model?: string; manual_version?: string; status: string; page_count?: number; chapter_count?: number; parent_chunk_count?: number; child_chunk_count?: number; chunk_count?: number; parser_version?: string; error?: string; updated_at?: string };
type IngestionJob = { job_id: string; document_id: string; status: string; progress: number; error?: string | null };
type EvaluationRecord = { case_id: string; message: string; answer: string; duration_ms: number; judge_duration_ms?: number; scores: Record<string, boolean | null | number | string>; route: { intent?: string; action?: string }; citations: { title: string }[] };
type Evaluation = { generated_at?: string; total_cases?: number; passed?: boolean; failures?: unknown[]; metrics?: Record<string, { score: number; passed: number; total: number }>; records?: EvaluationRecord[]; coverage?: Record<string, number>; duration_ms?: number; average_duration_ms?: number; cost?: { total: number | null; currency: string } };

const metrics: [string, string][] = [
  ["意图路由准确率", "intent_routing_accuracy"],
  ["工具调用成功率", "tool_execution_success_rate"],
  ["检索召回率 Recall@2", "recall_at_2"],
  ["RAG 回答准确率", "rag_answer_accuracy"],
  ["任务完成率", "task_completion_rate"],
];
const applicableFields: Record<string, string> = { tool_execution_success_rate: "tool_applicable", recall_at_2: "recall_applicable", rag_answer_accuracy: "rag_applicable", task_completion_rate: "task_applicable" };
const scoreFields: Record<string, string> = { intent_routing_accuracy: "intent_passed", tool_execution_success_rate: "tool_passed", recall_at_2: "recall_passed", rag_answer_accuracy: "rag_passed", task_completion_rate: "task_passed" };

function Logo() {
  return <div className="logo-mark"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M12 21.5 2.5 16 12 10.5 21.5 16 12 21.5Z" fill="#d97706" fillOpacity=".2" stroke="#d97706" strokeWidth="1.5"/><path d="m12 16.5-9.5-5.5L12 5.5l9.5 5.5-9.5 5.5Z" fill="#d97706" fillOpacity=".5" stroke="#d97706" strokeWidth="1.5"/><path d="m12 11.5-9.5-5.5L12 .5 21.5 6l-9.5 5.5Z" fill="#d97706" stroke="#d97706" strokeWidth="1.5"/></svg></div>;
}

function ChatAvatar({ role }: { role: "user" | "assistant" }) {
  return <span className={`avatar ${role}`}>{role === "assistant" ? <svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="18" fill="#243238"/><path d="M12 20c0-4.4 3.6-8 8-8s8 3.6 8 8v4.5a3.5 3.5 0 0 1-3.5 3.5H23l-2.2 2.5a1 1 0 0 1-1.5 0L17.1 28h-1.6a3.5 3.5 0 0 1-3.5-3.5V20Z" fill="white"/><circle cx="17" cy="20" r="1.4" fill="#D97706"/><circle cx="23" cy="20" r="1.4" fill="#D97706"/></svg> : <svg viewBox="0 0 40 40" fill="none"><circle cx="20" cy="20" r="18" fill="#FFF7E8" stroke="#F2C77D"/><circle cx="20" cy="16" r="5" fill="#D97706"/><path d="M10.5 30c1.9-5.1 5.1-7.5 9.5-7.5s7.6 2.4 9.5 7.5" fill="#D97706"/></svg>}</span>;
}

function App() {
  const [view, setView] = useState<View>("chat");
  const [sessionId] = useState(crypto.randomUUID());
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [messages, setMessages] = useState<Message[]>([{ role: "assistant", content: T.greeting }]);
  const [documents, setDocuments] = useState<KnowledgeDocument[]>([]);
  const [jobs, setJobs] = useState<Record<string, IngestionJob>>({});
  const [knowledgeStatus, setKnowledgeStatus] = useState("");
  const [knowledgePage, setKnowledgePage] = useState(1);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [evaluationRunning, setEvaluationRunning] = useState(false);
  const [evaluationError, setEvaluationError] = useState("");
  const [selectedMetric, setSelectedMetric] = useState<string | null>(null);
  const recognition = useRef<SpeechRecognitionLike | null>(null);
  const [voiceListening, setVoiceListening] = useState(false);
  const [voiceStatus, setVoiceStatus] = useState("");
  const pageSize = 10;
  const manuals = useMemo(() => documents.filter((item) => item.filename), [documents]);
  const pageCount = Math.max(1, Math.ceil(manuals.length / pageSize));
  const visibleDocuments = manuals.slice((knowledgePage - 1) * pageSize, knowledgePage * pageSize);

  const loadDocuments = async () => {
    const response = await fetch(`${API}/api/v1/knowledge/documents`);
    if (response.ok) setDocuments(await response.json() as KnowledgeDocument[]);
  };
  useEffect(() => { void loadDocuments().catch(() => undefined); }, []);

  useEffect(() => () => recognition.current?.abort(), []);

  function toggleVoiceInput() {
    if (voiceListening) {
      recognition.current?.stop();
      return;
    }
    const speechWindow = window as Window & { SpeechRecognition?: SpeechRecognitionFactory; webkitSpeechRecognition?: SpeechRecognitionFactory };
    const Recognition = speechWindow.SpeechRecognition ?? speechWindow.webkitSpeechRecognition;
    if (!Recognition) {
      setVoiceStatus("\u5f53\u524d\u6d4f\u89c8\u5668\u4e0d\u652f\u6301\u8bed\u97f3\u8bc6\u522b\uff0c\u8bf7\u4f7f\u7528 Chrome \u6216 Edge\u3002");
      return;
    }
    const current = new Recognition();
    current.lang = "zh-CN";
    current.interimResults = false;
    current.continuous = false;
    current.onstart = () => {
      setVoiceListening(true);
      setVoiceStatus("\u6b63\u5728\u542c\uff0c\u8bf7\u8bf4\u8bdd...");
    };
    current.onresult = (event) => {
      const transcript = Array.from(event.results).map((result) => result[0]?.transcript ?? "").join("").trim();
      if (transcript) setInput((previous) => `${previous}${previous ? " " : ""}${transcript}`);
      setVoiceStatus(transcript ? "\u8bed\u97f3\u5df2\u8bc6\u522b\uff0c\u8bf7\u786e\u8ba4\u540e\u53d1\u9001\u3002" : "\u672a\u8bc6\u522b\u5230\u8bed\u97f3\u3002");
    };
    current.onerror = (event) => {
      setVoiceStatus(event.error === "not-allowed" ? "\u8bf7\u5141\u8bb8\u6d4f\u89c8\u5668\u4f7f\u7528\u9ea6\u514b\u98ce\u3002" : "\u8bed\u97f3\u8bc6\u522b\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5\u3002");
    };
    current.onend = () => setVoiceListening(false);
    recognition.current = current;
    current.start();
  }

  async function submit(event?: FormEvent, preset?: string) {
    event?.preventDefault();
    const message = (preset ?? input).trim();
    if (!message || loading) return;
    setInput(""); setLoading(true);
    setMessages((items) => [...items, { role: "user", content: message }]);
    try {
      const response = await fetch(`${API}/api/v1/sessions/${sessionId}/messages`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ message, user_id: USER_ID, session_id: sessionId }) });
      if (!response.ok) throw new Error();
      const data = await response.json();
      setMessages((items) => [...items, { role: "assistant", content: data.answer, route: `${data.route.intent} · ${data.route.action}` }]);
    } catch {
      setMessages((items) => [...items, { role: "assistant", content: "服务暂时不可用，请稍后重试。" }]);
    } finally { setLoading(false); }
  }

  async function pollJobs(jobIds: string[]) {
    const results = await Promise.all(jobIds.map(async (jobId) => {
      const response = await fetch(`${API}/api/v1/knowledge/ingestion-jobs/${jobId}`);
      return response.ok ? await response.json() as IngestionJob : null;
    }));
    const currentJobs = results.filter((item): item is IngestionJob => item !== null);
    setJobs((previous) => ({ ...previous, ...Object.fromEntries(currentJobs.map((item) => [item.document_id, item])) }));
    await loadDocuments();
    const active = currentJobs.filter((item) => !["ready", "failed"].includes(item.status));
    if (active.length) {
      setKnowledgeStatus(`后台解析中：${active.map((item) => `${item.status} ${item.progress}%`).join("、")}`);
      window.setTimeout(() => { void pollJobs(active.map((item) => item.job_id)); }, 1000);
      return;
    }
    const failed = currentJobs.filter((item) => item.status === "failed").length;
    setKnowledgeStatus(failed ? `解析完成，${failed} 份失败，请查看表格错误信息` : `解析完成，${currentJobs.length} 份手册已入库`);
  }

  async function uploadKnowledge(files: FileList) {
    const selected = Array.from(files).filter((file) => file.name.toLowerCase().endsWith(".pdf"));
    if (!selected.length) { setKnowledgeStatus("请选择 PDF 文件"); return; }
    setKnowledgeStatus(`正在提交 ${selected.length} 份手册...`);
    const results = await Promise.all(selected.map(async (file) => {
      const form = new FormData(); form.append("file", file); form.append("title", file.name);
      const response = await fetch(`${API}/api/v1/knowledge/ingestion-jobs`, { method: "POST", body: form });
      return response.ok ? await response.json() as { job_id: string | null } : null;
    }));
    const jobIds = results.flatMap((item) => item?.job_id ? [item.job_id] : []);
    await loadDocuments();
    if (jobIds.length) void pollJobs(jobIds);
    else setKnowledgeStatus("文件已存在或提交失败");
  }

  async function deleteDocument(documentId: string, filename: string) {
    if (!window.confirm(`确定要删除《${filename}》的入库记录吗？`)) return;
    const response = await fetch(`${API}/api/v1/knowledge/documents/${documentId}`, { method: "DELETE" });
    if (!response.ok) { setKnowledgeStatus("删除失败，请稍后重试"); return; }
    setKnowledgeStatus(`已删除 ${filename}`);
    await loadDocuments();
  }

  async function reindexDocument(documentId: string, filename: string) {
    const response = await fetch(`${API}/api/v1/knowledge/documents/${documentId}/reindex`, { method: "POST" });
    const result = await response.json().catch(() => ({})) as { job_id?: string; detail?: string };
    if (!response.ok) { setKnowledgeStatus(result.detail || "重建索引失败，请稍后重试"); return; }
    setKnowledgeStatus(`已提交《${filename}》的父子块重建任务`);
    await loadDocuments();
    if (result.job_id) void pollJobs([result.job_id]);
  }
  async function runEvaluation() {
    if (evaluationRunning) return;
    setEvaluationRunning(true); setEvaluationError("");
    try {
      const response = await fetch(`${API}/api/v1/evaluation/run`, { method: "POST" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      setEvaluation(await response.json() as Evaluation);
    } catch (error) { setEvaluationError(error instanceof Error ? error.message : "评测执行失败"); }
    finally { setEvaluationRunning(false); }
  }

  const examples = ["查询物流进度", "申请仅退款", "拼单优惠券规则"];
  return <main className="shell">
    <aside className="sidebar"><div className="brand"><Logo/><div><strong>{T.brand}</strong><small>PDD CUSTOMER AGENT</small></div></div><nav className="nav"><button className={view === "chat" ? "active" : ""} onClick={() => setView("chat")}><span>◉</span>{T.chat}</button><button className={view === "knowledge" ? "active" : ""} onClick={() => { setView("knowledge"); void loadDocuments(); }}><span>◉</span>{T.knowledge}</button><button className={view === "eval" ? "active" : ""} onClick={() => setView("eval")}><span>◉</span>{T.evaluation}</button></nav><p className="session">{sessionId.slice(0, 8)}</p></aside>
    <section className="workspace"><header><h1>{view === "chat" ? T.chatTitle : view === "knowledge" ? T.knowledgeTitle : T.evaluationTitle}</h1></header>
      {view === "chat" ? <><div className="chat-content"><div className="chat-examples"><small>{T.quick}</small>{examples.map((item) => <button key={item} onClick={() => void submit(undefined, item)}>{item}</button>)}</div><div className="messages">{messages.map((message, index) => <article key={index} className={`message ${message.role}`}><ChatAvatar role={message.role}/><div className="bubble"><p>{message.content}</p></div></article>)}{loading && <article className="message assistant"><div className="bubble typing"><i/><i/><i/></div></article>}</div></div><form className="composer" onSubmit={submit}><div className="compose-row"><input value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入问题，例如：查询订单物流"/><button className={`voice-button ${voiceListening ? "listening" : ""}`} type="button" onClick={toggleVoiceInput} title={voiceListening ? "\u7ed3\u675f\u5f55\u97f3" : "\u8bed\u97f3\u8f93\u5165"} aria-label={voiceListening ? "\u7ed3\u675f\u5f55\u97f3" : "\u8bed\u97f3\u8f93\u5165"}><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="8" y="3" width="8" height="12" rx="4" stroke="currentColor" strokeWidth="1.8"/><path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3M8.5 21h7" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/></svg></button><button className="send" disabled={loading || !input.trim()} type="submit">发送</button></div>{voiceStatus && <small className="voice-status">{voiceStatus}</small>}</form></> : null}
      {view === "knowledge" ? <div className="knowledge-panel"><div className="knowledge-panel-head"><div><h2>{T.knowledgeTitle}</h2><p>支持一次选择多份 PDF，后台解析并保留型号、页码、章节和结构化字段。</p></div><label className="knowledge-upload">{T.uploadManual}<input type="file" multiple accept=".pdf" onChange={(event) => event.target.files && void uploadKnowledge(event.target.files)}/></label></div><p className="knowledge-status">{knowledgeStatus}</p><div className="knowledge-table"><div className="knowledge-row knowledge-header"><span>文件与型号</span><span>状态</span><span>解析结果</span><span>更新时间</span><span>操作</span></div>{visibleDocuments.map((doc) => { const job = jobs[doc.document_id]; const status = job?.status ?? doc.status; return <div className="knowledge-row" key={doc.document_id}><span><strong>{doc.filename}</strong><small>{doc.model ?? "未识别型号"} · v{doc.manual_version ?? "-"}</small></span><span className={`document-status ${status}`}>{status}{job && !["ready", "failed"].includes(status) ? ` ${job.progress}%` : ""}</span><span>{job?.error || doc.error || (job && !["ready", "failed"].includes(status) ? `正在${status}，已完成 ${job.progress}%` : `${doc.page_count ?? 0} 页 · ${doc.chapter_count ?? 0} 父块 · ${doc.child_chunk_count ?? doc.chunk_count ?? 0} 子块 · ${doc.parser_version ?? "待处理"}`)}</span><span>{doc.updated_at ? new Date(doc.updated_at).toLocaleString("zh-CN", { hour12: false }) : "-"}</span><span className="knowledge-actions"><button type="button" className="knowledge-reindex" onClick={() => void reindexDocument(doc.document_id, doc.filename)} disabled={status !== "ready"}>{T.reindex}</button><button type="button" className="knowledge-delete" onClick={() => void deleteDocument(doc.document_id, doc.filename)}>{T.delete}</button></span></div>; })}<div className="knowledge-pagination"><button disabled={knowledgePage === 1} onClick={() => setKnowledgePage((page) => Math.max(1, page - 1))}>上一页</button><span>第 {knowledgePage} / {pageCount} 页，共 {manuals.length} 份</span><button disabled={knowledgePage === pageCount} onClick={() => setKnowledgePage((page) => Math.min(pageCount, page + 1))}>下一页</button></div></div></div> : null}
      {view === "eval" ? <div className="evaluation"><div className="eval-actions"><button className="run-eval" onClick={() => void runEvaluation()} disabled={evaluationRunning}>{evaluationRunning ? "正在执行 100 条评测..." : "重新执行评测"}</button><span className="knowledge-status">评测不包含手册导入，请在手册入库页面操作</span></div><div className="eval-summary">{evaluation ? <><span className={`eval-status ${evaluation.passed ? "passed" : "failed"}`}>{evaluation.passed ? "质量门禁已通过" : "质量门禁未通过"}</span><span>样本 {evaluation.total_cases ?? 0} 条</span><span>失败 {evaluation.failures?.length ?? 0} 条</span><span>总耗时 {evaluation.duration_ms !== undefined ? `${(evaluation.duration_ms / 1000).toFixed(2)} 秒` : "-"}</span><span>{"\u5e73\u5747\u6bcf\u9898"} {evaluation.average_duration_ms !== undefined ? `${evaluation.average_duration_ms.toFixed(2)} ms` : "-"}</span><span>总成本 {evaluation.cost?.total ?? "未计费"}{evaluation.cost?.total !== null && evaluation.cost?.total !== undefined ? ` ${evaluation.cost.currency}` : ""}</span></> : <span>尚未运行评测</span>}</div>{evaluationError && <p className="evaluation-error">{evaluationError}</p>}<div className="metric-grid">{metrics.map(([label, key]) => <button className="metric" key={key} onClick={() => setSelectedMetric(key)}><small>{label}</small><strong>{evaluation?.metrics?.[key] ? `${Math.round(evaluation.metrics[key].score * 100)}%` : "-"}</strong><span>{evaluation?.metrics?.[key] ? `${evaluation.metrics[key].passed}/${evaluation.metrics[key].total} 通过 · 覆盖 ${evaluation.coverage?.[key] ?? 0}` : "等待报告"}</span></button>)}<article className="metric metric-static"><small>{"\u5e73\u5747\u6bcf\u9898\u8017\u65f6"}</small><strong>{evaluation?.average_duration_ms !== undefined ? `${evaluation.average_duration_ms.toFixed(2)} ms` : "-"}</strong><span>{"\u4ec5\u7edf\u8ba1\u5ba2\u670d\u751f\u6210\u56de\u7b54\u8017\u65f6"}</span></article></div>{selectedMetric && <section className="evaluation-records"><header><strong>{metrics.find((item) => item[1] === selectedMetric)?.[0]} 逐条记录</strong><button onClick={() => setSelectedMetric(null)}>关闭</button></header>{evaluation?.records?.filter((record) => selectedMetric === "intent_routing_accuracy" || Boolean(record.scores[applicableFields[selectedMetric]])).map((record) => <details key={record.case_id}><summary>{record.case_id} · {record.duration_ms} ms · {record.scores[scoreFields[selectedMetric]] === true ? "通过" : "失败"}</summary><p>问题：{record.message}</p><p>路由：{record.route.intent} · {record.route.action}</p><p>回答：{record.answer}</p><p>引用：{record.citations.map((item) => item.title).join("；") || "无"}</p><>{selectedMetric === "rag_answer_accuracy" && <p>{"\u6a21\u578b\u8bc4\u5206\uff1a"}{record.scores.rag_judge_score ?? "-"}{"\u5206"}{record.scores.rag_judge_reason ? `?${record.scores.rag_judge_reason}` : ""}</p>}</></details>)}</section>}</div> : null}
    </section>
  </main>;
}

createRoot(document.getElementById("root")!).render(<App/>);