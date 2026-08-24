import { useState, useRef, useEffect, type FormEvent } from "react";
import {
  describeEvent,
  fetchEvents,
  getConversation,
  getCurrentUser,
  getResourceMapping,
  listConversations,
  openProgressStream,
  postMessage,
  rerunConversation,
  startConversation,
  submitFeedback,
  waitForOpen,
} from "./api";
import { clearToken, getToken, setToken } from "./auth";
import type {
  AuthUser,
  ClarifyingQuestionItem,
  ConversationSummary,
  DisplayTranscriptEntry,
  MessageResponse,
  ProgressEvent,
  RecommendationItem,
  ResourceMappingResponse,
  StartFields,
} from "./types";
import InputPanel from "./components/InputPanel";
import HistorySidebar from "./components/HistorySidebar";
import ClarifyingQuestionDeck from "./components/ClarifyingQuestionDeck";
import FeedbackModal from "./components/FeedbackModal";
import AdminFeedback from "./components/AdminFeedback";
import LandingPage from "./components/LandingPage";
import PayloadMappingPanel from "./components/PayloadMappingPanel";
import "./App.css";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
}

interface RecommendationState {
  mustHave: RecommendationItem[];
  potentiallyNeeded: RecommendationItem[];
  dropped: string[];
  redacted: string[];
}

function nextId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function citationDomain(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

const SIDEBAR_MIN_WIDTH = 180;
const SIDEBAR_MAX_WIDTH = 440;
const CHAT_MIN_WIDTH = 320;
const CHAT_MAX_WIDTH = 900;

function transcriptToMessages(entries: DisplayTranscriptEntry[]): ChatMessage[] {
  return entries.map((e) => ({
    id: nextId(),
    role: e.role,
    text: e.kind === "search" ? `🔍 ${e.text}` : e.kind === "question" ? `❓ ${e.text}` : e.text,
  }));
}

function ResourceCard({
  item,
  onViewPayload,
}: {
  item: RecommendationItem;
  onViewPayload: ((resourceType: string) => void) | null;
}) {
  return (
    <li className="resource-card">
      <div className="resource-card__header">
        <span className="resource-card__type">{item.resource_type}</span>
        {onViewPayload && (
          <button
            type="button"
            className="resource-card__view-payload"
            onClick={() => onViewPayload(item.resource_type)}
          >
            View suggested payload
          </button>
        )}
      </div>
      <p className="resource-card__rationale">
        {item.rationale}
        <sup className="resource-card__marker">†</sup>
      </p>
      <a className="resource-card__footnote" href={item.citation.url} target="_blank" rel="noreferrer">
        <span className="resource-card__footnote-marker">†</span>
        <span className="resource-card__footnote-domain">{citationDomain(item.citation.url)}</span>
        <span>↗</span>
      </a>
    </li>
  );
}

function RecommendationPanel({
  state,
  onReportIssue,
  onViewPayload,
}: {
  state: RecommendationState | null;
  onReportIssue: (() => void) | null;
  onViewPayload: ((resourceType: string) => void) | null;
}) {
  if (!state) {
    return (
      <div className="panel panel--empty">
        <p>Describe a data source to see recommended FHIR R4 resources here.</p>
      </div>
    );
  }

  return (
    <div className="panel">
      <section>
        <h2>
          <span className="panel__clause">§1</span> Must-have
        </h2>
        {state.mustHave.length === 0 ? (
          <p className="panel__muted">None</p>
        ) : (
          <ul className="resource-list">
            {state.mustHave.map((item) => (
              <ResourceCard key={item.resource_type} item={item} onViewPayload={onViewPayload} />
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>
          <span className="panel__clause">§2</span> Potentially needed
        </h2>
        {state.potentiallyNeeded.length === 0 ? (
          <p className="panel__muted">None</p>
        ) : (
          <ul className="resource-list">
            {state.potentiallyNeeded.map((item) => (
              <ResourceCard key={item.resource_type} item={item} onViewPayload={onViewPayload} />
            ))}
          </ul>
        )}
      </section>

      {state.dropped.length > 0 && (
        <section className="panel__dropped">
          <h2>
            <span className="panel__clause">§3</span> Not included
          </h2>
          <p className="panel__muted">
            These were considered but couldn't be verified against the FHIR R4 spec this turn,
            so they were left out rather than guessed:
          </p>
          <ul className="dropped-list">
            {state.dropped.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </section>
      )}

      {state.redacted.length > 0 && (
        <section className="panel__dropped">
          <h2>Code references removed</h2>
          <p className="panel__muted">
            A rationale mentioned what looked like a specific terminology code. Codes aren't
            verified against a terminology corpus yet, so they're stripped rather than shown:
          </p>
          <ul className="dropped-list">
            {state.redacted.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        </section>
      )}

      {onReportIssue && (
        <button type="button" className="panel__report-issue" onClick={onReportIssue}>
          Report quality issue
        </button>
      )}
    </div>
  );
}

export default function App() {
  const [stage, setStage] = useState<"loading" | "landing" | "app">("loading");
  const [authUser, setAuthUser] = useState<AuthUser | null>(null);
  const isGuest = stage === "app" && authUser === null;
  const [page, setPage] = useState<"main" | "admin">("main");
  const [mode, setMode] = useState<"input" | "chat">("input");
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  // Live per-node status text for the loading bubble (issue #4) -- null
  // falls back to the static "Thinking…" text, e.g. before the first event
  // arrives or once a turn finishes.
  const [liveStatus, setLiveStatus] = useState<string | null>(null);
  const pollIntervalRef = useRef<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [finished, setFinished] = useState(false);
  const [readOnly, setReadOnly] = useState(false);
  const [recommendation, setRecommendation] = useState<RecommendationState | null>(null);
  // The current batch of clarifying questions (null when there's none
  // pending) -- rendered as a card deck (ClarifyingQuestionDeck) the user
  // pages through and answers before a single combined resume. batchId is
  // bumped on every new batch so the deck component remounts with fresh
  // internal state (card index, per-card answers) instead of carrying over
  // the previous batch's.
  const [pendingQuestions, setPendingQuestions] = useState<ClarifyingQuestionItem[] | null>(null);
  const [questionBatchId, setQuestionBatchId] = useState(0);
  const [dataFormat, setDataFormat] = useState<string | null>(null);
  const [mappingPanelResourceType, setMappingPanelResourceType] = useState<string | null>(null);
  const [mappingCache, setMappingCache] = useState<Record<string, ResourceMappingResponse>>({});
  const [mappingLoading, setMappingLoading] = useState(false);
  const [mappingError, setMappingError] = useState<string | null>(null);
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [showFeedback, setShowFeedback] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [historySearch, setHistorySearch] = useState("");
  const [historySearchError, setHistorySearchError] = useState<string | null>(null);
  const [sidebarWidth, setSidebarWidth] = useState(260);
  const [chatWidth, setChatWidth] = useState(560);
  const [isResizing, setIsResizing] = useState(false);
  const threadEndRef = useRef<HTMLDivElement>(null);
  const isFirstSearchRender = useRef(true);
  // Bumped by any action that supersedes an in-flight request (New
  // conversation, selecting a different history item, a second Send before
  // the first resolves). Async handlers check it after every await so a
  // stale response can't land on top of state that's moved on -- e.g.
  // clicking "New conversation" mid-request used to let the old response
  // repopulate sessionId/messages/recommendation on the fresh conversation.
  const generationRef = useRef(0);

  // Resolve which stage to land on: a Google OAuth redirect carries a fresh
  // token in the URL; otherwise fall back to whatever's in localStorage from
  // a previous session; otherwise show the landing page.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const tokenFromUrl = params.get("auth_token");
    if (tokenFromUrl) {
      setToken(tokenFromUrl);
      window.history.replaceState({}, "", window.location.pathname);
    }

    const token = getToken();
    if (!token) {
      setStage("landing");
      return;
    }

    getCurrentUser()
      .then((user) => {
        setAuthUser(user);
        setStage("app");
      })
      .catch(() => {
        clearToken();
        setStage("landing");
      });
  }, []);

  // History only exists for authenticated users -- guests have nothing
  // persisted to fetch, so this deliberately doesn't run for them.
  useEffect(() => {
    if (stage === "app" && authUser) {
      refreshHistory();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stage, authUser]);

  useEffect(() => {
    threadEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  // Debounced re-fetch when the search box changes; skips the mount-time
  // render since the initial load above already fetches the unfiltered list.
  useEffect(() => {
    if (isFirstSearchRender.current) {
      isFirstSearchRender.current = false;
      return;
    }
    const handle = setTimeout(() => refreshHistory(), 300);
    return () => clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [historySearch]);

  async function refreshHistory() {
    setHistoryLoading(true);
    setHistorySearchError(null);
    try {
      setConversations(await listConversations(historySearch || undefined));
    } catch (err) {
      // A plain refresh failing stays silent (sidebar just stays stale, not
      // fatal to the main flow) -- but a bad regex from the search box is
      // worth surfacing since the user typed it deliberately.
      if (historySearch) {
        setHistorySearchError(err instanceof Error ? err.message : "search failed");
      }
    } finally {
      setHistoryLoading(false);
    }
  }

  function appendMessage(role: ChatMessage["role"], text: string) {
    setMessages((prev) => [...prev, { id: nextId(), role, text }]);
  }

  function applyOutcome(res: MessageResponse) {
    setSessionId(res.session_id);
    if (res.kind === "out_of_scope") {
      appendMessage("assistant", `Out of scope: ${res.reason}`);
      setPendingQuestions(null);
      setFinished(true);
    } else if (res.kind === "clarifying_question") {
      appendMessage(
        "assistant",
        res.questions.length === 1
          ? res.questions[0].question
          : `${res.questions.length} follow-up questions before I can recommend resources:`,
      );
      setPendingQuestions(res.questions);
      setQuestionBatchId((n) => n + 1);
    } else {
      appendMessage("assistant", "Here's what I found, categorized and cited in the panel →");
      setPendingQuestions(null);
      setRecommendation({
        mustHave: res.must_have,
        potentiallyNeeded: res.potentially_needed,
        dropped: res.dropped,
        redacted: res.redacted,
      });
      setFinished(true);
    }
  }

  // Opens the live-progress SSE stream for one in-flight turn (issue #4) and
  // returns a stop() to close it -- called from handleStart/handleContinue,
  // torn down in their existing finally blocks alongside setLoading(false).
  // Guarded by generationRef the same way every other async update in this
  // file is: a stale event/poll from a superseded request must not touch
  // liveStatus.
  function beginLiveProgress(progressSessionId: string, myGeneration: number) {
    const es = openProgressStream(progressSessionId);

    es.onmessage = (e) => {
      if (generationRef.current !== myGeneration) return;
      try {
        setLiveStatus(describeEvent(JSON.parse(e.data) as ProgressEvent));
      } catch {
        // Malformed frame -- ignore, the static "Thinking…" text stays put.
      }
    };

    es.onerror = () => {
      // EventSource auto-reconnects by default -- close it so we own the
      // fallback from here instead of racing our own polling against the
      // browser's silent retries.
      es.close();
      if (generationRef.current !== myGeneration) return;
      if (!authUser) return; // guests: nothing persisted to poll, degrade to the static bubble
      if (pollIntervalRef.current != null) return; // already polling

      let afterId = 0;
      pollIntervalRef.current = window.setInterval(async () => {
        try {
          const events = await fetchEvents(progressSessionId, afterId);
          if (generationRef.current !== myGeneration || events.length === 0) return;
          afterId = events[events.length - 1].id;
          setLiveStatus(describeEvent(events[events.length - 1]));
        } catch {
          // Transient poll failure -- retry next tick.
        }
      }, 2000);
    };

    return {
      es,
      stop: () => {
        es.close();
        if (pollIntervalRef.current != null) {
          window.clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
      },
    };
  }

  function startNewConversation() {
    generationRef.current += 1; // invalidate any in-flight request from the conversation being left
    setMode("input");
    setSessionId(null);
    setMessages([]);
    setInput("");
    setLoading(false);
    setLiveStatus(null);
    setError(null);
    setFinished(false);
    setReadOnly(false);
    setRecommendation(null);
    setPendingQuestions(null);
    setDataFormat(null);
    setMappingPanelResourceType(null);
    setMappingCache({});
    setMappingError(null);
    if (authUser) {
      // The conversation being left is already persisted turn-by-turn (every
      // start/respond call saves it server-side) -- this just makes sure the
      // sidebar reflects that immediately rather than waiting for the next
      // natural refresh. Guests have nothing persisted, so skip the call.
      refreshHistory();
    }
  }

  function handleGuest() {
    setAuthUser(null);
    setStage("app");
  }

  function handleAuthenticated(_token: string, user: AuthUser) {
    setAuthUser(user);
    setStage("app");
  }

  function handleLogout() {
    clearToken();
    setAuthUser(null);
    setStage("landing");
    setPage("main");
    generationRef.current += 1;
    setMode("input");
    setSessionId(null);
    setMessages([]);
    setInput("");
    setLoading(false);
    setLiveStatus(null);
    setError(null);
    setFinished(false);
    setReadOnly(false);
    setRecommendation(null);
    setPendingQuestions(null);
    setDataFormat(null);
    setMappingPanelResourceType(null);
    setMappingCache({});
    setMappingError(null);
    setConversations([]);
  }

  async function handleStart(fields: StartFields) {
    const myGeneration = ++generationRef.current;
    setMode("chat");
    setReadOnly(false);
    setMessages([{ id: nextId(), role: "user", text: fields.message }]);
    setDataFormat(fields.data_format ?? null);
    setMappingPanelResourceType(null);
    setMappingCache({});
    setMappingError(null);
    setLoading(true);
    setLiveStatus(null);
    setError(null);
    // Client-generated, not server-generated: the SSE stream needs a
    // session_id to subscribe with *before* startConversation's (blocking)
    // POST resolves -- the server can't hand one back any earlier than that
    // otherwise. See MessageRequest.client_session_id in backend/api.py.
    const clientSessionId = crypto.randomUUID();
    const { es, stop } = beginLiveProgress(clientSessionId, myGeneration);
    try {
      await waitForOpen(es);
      const outcome = await startConversation(fields, clientSessionId);
      if (generationRef.current !== myGeneration) return; // superseded (e.g. New conversation clicked meanwhile)
      applyOutcome(outcome);
    } catch (err) {
      if (generationRef.current !== myGeneration) return;
      // Terminal, not just an inline message: sessionId never got set here,
      // so leaving the composer enabled would let the user type and hit
      // Send only to have it silently no-op (handleContinue requires a
      // sessionId) -- looks like the app hung rather than failed.
      setError(err instanceof Error ? err.message : "something went wrong");
      setFinished(true);
      setSessionId(null);
    } finally {
      stop();
      if (generationRef.current === myGeneration) {
        setLoading(false);
        setLiveStatus(null);
      }
      if (authUser) refreshHistory();
    }
  }

  // Shared by the free-text composer submit and submitting a clarifying-
  // question card deck -- both resume the graph immediately with no
  // separate submit step. displayText (defaults to text) is what's shown in
  // the chat thread's "user" bubble -- for a card deck this is a friendlier
  // per-question summary, while `text` is the fuller Q/A-labeled version
  // that actually resumes the graph.
  async function submitAnswer(text: string, displayText?: string) {
    if (!text || loading || finished || readOnly || !sessionId) return;

    const myGeneration = ++generationRef.current;
    appendMessage("user", displayText ?? text);
    setPendingQuestions(null);
    setInput("");
    setLoading(true);
    setLiveStatus(null);
    setError(null);
    // sessionId is already known here (unlike handleStart) -- no client-
    // generated id needed, just open the stream before firing the POST.
    const { es, stop } = beginLiveProgress(sessionId, myGeneration);
    try {
      await waitForOpen(es);
      const outcome = await postMessage(sessionId, text);
      if (generationRef.current !== myGeneration) return;
      applyOutcome(outcome);
    } catch (err) {
      if (generationRef.current !== myGeneration) return;
      setError(err instanceof Error ? err.message : "something went wrong");
      setFinished(true);
    } finally {
      stop();
      if (generationRef.current === myGeneration) {
        setLoading(false);
        setLiveStatus(null);
      }
      if (authUser) refreshHistory();
    }
  }

  function handleContinue(e: FormEvent) {
    e.preventDefault();
    void submitAnswer(input.trim());
  }

  async function handleSelectHistory(id: string) {
    const myGeneration = ++generationRef.current;
    setError(null);
    try {
      const detail = await getConversation(id);
      if (generationRef.current !== myGeneration) return;
      setMode("chat");
      setReadOnly(true);
      setFinished(true);
      setPendingQuestions(null);
      setSessionId(detail.id);
      setMessages(transcriptToMessages(detail.display_transcript));
      setDataFormat(detail.data_format);
      setMappingPanelResourceType(null);
      setMappingCache({});
      setMappingError(null);
      if (detail.last_outcome?.kind === "final_recommendation") {
        setRecommendation({
          mustHave: detail.last_outcome.must_have,
          potentiallyNeeded: detail.last_outcome.potentially_needed,
          dropped: detail.last_outcome.dropped,
          redacted: detail.last_outcome.redacted,
        });
      } else {
        setRecommendation(null);
      }
    } catch (err) {
      if (generationRef.current !== myGeneration) return;
      setError(err instanceof Error ? err.message : "something went wrong");
    }
  }

  async function handleRerun(id: string) {
    const myGeneration = ++generationRef.current;
    setError(null);
    setLoading(true);
    try {
      const detail = await getConversation(id);
      if (generationRef.current !== myGeneration) return;
      setMode("chat");
      setReadOnly(false);
      setFinished(false);
      setRecommendation(null);
      setPendingQuestions(null);
      setDataFormat(detail.data_format);
      setMappingPanelResourceType(null);
      setMappingCache({});
      setMappingError(null);
      setMessages([{ id: nextId(), role: "user", text: detail.initial_message }]);
      const outcome = await rerunConversation(id);
      if (generationRef.current !== myGeneration) return;
      applyOutcome(outcome);
    } catch (err) {
      if (generationRef.current !== myGeneration) return;
      // sessionId is cleared, not left pointing at the conversation being
      // rerun -- continuing into it here would silently post a message onto
      // the wrong (old) conversation instead of the failed new attempt.
      setError(err instanceof Error ? err.message : "something went wrong");
      setFinished(true);
      setSessionId(null);
    } finally {
      if (generationRef.current === myGeneration) setLoading(false);
      if (authUser) refreshHistory();
    }
  }

  async function handleFeedbackSubmit(expectation: string) {
    if (!sessionId) return;
    await submitFeedback(sessionId, expectation);
  }

  const canReportIssue = !isGuest && finished && sessionId ? () => setShowFeedback(true) : null;

  async function toggleMapping(resourceType: string) {
    if (mappingPanelResourceType === resourceType) {
      setMappingPanelResourceType(null);
      return;
    }
    setMappingPanelResourceType(resourceType);
    setMappingError(null);
    if (!sessionId || mappingCache[resourceType]) return;

    setMappingLoading(true);
    try {
      const result = await getResourceMapping(sessionId, resourceType);
      setMappingCache((prev) => ({ ...prev, [resourceType]: result }));
    } catch (err) {
      setMappingError(err instanceof Error ? err.message : "couldn't build a suggested payload");
    } finally {
      setMappingLoading(false);
    }
  }

  const canViewPayload = sessionId ? toggleMapping : null;

  function beginResize(which: "sidebar" | "chat") {
    return (e: React.MouseEvent) => {
      e.preventDefault();
      const startX = e.clientX;
      const startSidebar = sidebarWidth;
      const startChat = chatWidth;
      setIsResizing(true);
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";

      function onMove(ev: MouseEvent) {
        const dx = ev.clientX - startX;
        if (which === "sidebar") {
          setSidebarWidth(clamp(startSidebar + dx, SIDEBAR_MIN_WIDTH, SIDEBAR_MAX_WIDTH));
        } else {
          setChatWidth(clamp(startChat + dx, CHAT_MIN_WIDTH, CHAT_MAX_WIDTH));
        }
      }
      function onUp() {
        setIsResizing(false);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", onUp);
      }
      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", onUp);
    };
  }

  const gridTemplateColumns = sidebarCollapsed
    ? `44px ${chatWidth}px 6px 1fr`
    : `${sidebarWidth}px 6px ${chatWidth}px 6px 1fr`;

  const accountName = isGuest ? "Guest" : authUser?.username ?? authUser?.display_name ?? "Signed in";
  const accountInitial = accountName.charAt(0).toUpperCase();

  if (stage === "loading") {
    return (
      <div className="landing">
        <p className="panel__muted">Loading…</p>
      </div>
    );
  }

  if (stage === "landing") {
    return <LandingPage onGuest={handleGuest} onAuthenticated={handleAuthenticated} />;
  }

  if (page === "admin") {
    return <AdminFeedback onBack={() => setPage("main")} />;
  }

  return (
    <div className="layout" style={{ gridTemplateColumns, transition: isResizing ? "none" : undefined }}>
      <HistorySidebar
        conversations={conversations}
        loading={historyLoading}
        activeId={sessionId}
        collapsed={sidebarCollapsed}
        guestMode={isGuest}
        search={historySearch}
        searchError={historySearchError}
        accountName={accountName}
        accountInitial={accountInitial}
        onSelect={handleSelectHistory}
        onRerun={handleRerun}
        onNew={startNewConversation}
        onToggleCollapsed={() => setSidebarCollapsed((c) => !c)}
        onSearchChange={setHistorySearch}
        onLogout={handleLogout}
      />

      {!sidebarCollapsed && (
        <div className="resize-handle" onMouseDown={beginResize("sidebar")} />
      )}

      <div className="chat-column">
        <header className="chat-header">
          <h1>
            <span className="wordmark-mark" aria-hidden="true" />
            FHIR Bud
          </h1>
          <div className="chat-header__actions">
            {!isGuest && finished && sessionId && !readOnly && (
              <button type="button" className="secondary-button" onClick={() => handleRerun(sessionId)}>
                ↻ Rerun
              </button>
            )}
            <button type="button" className="secondary-button" onClick={startNewConversation}>
              New conversation
            </button>
            {!isGuest && authUser?.is_admin && (
              <button type="button" className="secondary-button" onClick={() => setPage("admin")}>
                Admin
              </button>
            )}
          </div>
        </header>

        {readOnly && (
          <div className="readonly-banner">
            Viewing a past conversation (read-only) — use Rerun to test it again.
          </div>
        )}

        {mode === "input" ? (
          <InputPanel submitting={loading} onStart={handleStart} />
        ) : (
          <>
            <div className="thread">
              {messages.map((m) => (
                <div key={m.id} className={`bubble bubble--${m.role}`}>
                  <span className="bubble__text">{m.text}</span>
                </div>
              ))}
              {loading && (
                <div className="bubble bubble--assistant bubble--loading">
                  <span className="bubble__text">{liveStatus ?? "Thinking…"}</span>
                </div>
              )}
              {pendingQuestions && !loading && !finished && !readOnly && (
                <ClarifyingQuestionDeck
                  key={questionBatchId}
                  questions={pendingQuestions}
                  onSubmit={(combined, summary) => void submitAnswer(combined, summary)}
                />
              )}
              <div ref={threadEndRef} />
            </div>

            {error && <p className="error">{error}</p>}

            <form className="composer" onSubmit={handleContinue}>
              <input
                type="text"
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder={
                  readOnly
                    ? "Read-only — start a new conversation to continue"
                    : finished
                      ? "Start a new conversation to continue"
                      : pendingQuestions
                        ? "Answer the questions above to continue"
                        : "Type your message…"
                }
                disabled={loading || finished || readOnly || pendingQuestions !== null}
              />
              <button
                type="submit"
                disabled={loading || finished || readOnly || pendingQuestions !== null || !input.trim()}
              >
                Send
              </button>
            </form>
          </>
        )}
      </div>

      <div className="resize-handle" onMouseDown={beginResize("chat")} />

      <div className="panel-column">
        <RecommendationPanel state={recommendation} onReportIssue={canReportIssue} onViewPayload={canViewPayload} />
      </div>

      {mappingPanelResourceType && (
        <PayloadMappingPanel
          resourceType={mappingPanelResourceType}
          dataFormat={dataFormat}
          data={mappingCache[mappingPanelResourceType] ?? null}
          loading={mappingLoading && !mappingCache[mappingPanelResourceType]}
          error={mappingError}
          onClose={() => setMappingPanelResourceType(null)}
        />
      )}

      {showFeedback && (
        <FeedbackModal onSubmit={handleFeedbackSubmit} onClose={() => setShowFeedback(false)} />
      )}
    </div>
  );
}
