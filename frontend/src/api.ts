import { getToken } from "./auth";
import type {
  AdminSettings,
  AuthUser,
  ConversationDetail,
  ConversationSummary,
  FeedbackDetail,
  FeedbackReceipt,
  FeedbackSummary,
  MessageResponse,
  PolledProgressEvent,
  ProgressEvent,
  ResourceMappingResponse,
  StartFields,
} from "./types";

// Empty string = same-origin, which is correct in production (backend
// serves the built frontend, see root Dockerfile). Local dev runs the
// frontend on Vite's dev server (5173) separate from the backend (8000), so
// frontend/.env sets VITE_API_BASE=http://localhost:8000 there.
export const API_BASE = import.meta.env.VITE_API_BASE ?? "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    ...init,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `request failed: ${res.status}`);
  }

  return res.json();
}

export async function signup(username: string, password: string, email?: string): Promise<{ token: string; user: AuthUser }> {
  return request("/api/auth/signup", {
    method: "POST",
    body: JSON.stringify({ username, password, email: email || null }),
  });
}

export async function login(username: string, password: string): Promise<{ token: string; user: AuthUser }> {
  return request("/api/auth/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

export async function getCurrentUser(): Promise<AuthUser> {
  return request("/api/auth/me");
}

export async function startConversation(fields: StartFields, clientSessionId: string): Promise<MessageResponse> {
  return request("/api/messages", {
    method: "POST",
    body: JSON.stringify({ session_id: null, client_session_id: clientSessionId, ...fields }),
  });
}

export async function postMessage(sessionId: string, message: string): Promise<MessageResponse> {
  return request("/api/messages", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message }),
  });
}

// Live per-node status while a conversation turn is in flight (issue #4).
// Plain EventSource, not the request() wrapper -- EventSource can't send an
// Authorization header at all, and the backend endpoint is deliberately
// unauthenticated for exactly that reason (session_id is the capability
// token, see backend/api.py::stream_messages).
export function openProgressStream(sessionId: string): EventSource {
  return new EventSource(`${API_BASE}/api/messages/stream?session_id=${encodeURIComponent(sessionId)}`);
}

// Best-effort: resolves once the stream is confirmed open, or after
// timeoutMs, whichever comes first -- callers proceed either way so a slow
// or failed stream connection never blocks starting the actual conversation.
export function waitForOpen(es: EventSource, timeoutMs = 1500): Promise<void> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(resolve, timeoutMs);
    es.addEventListener(
      "open",
      () => {
        window.clearTimeout(timer);
        resolve();
      },
      { once: true },
    );
  });
}

// Polling fallback once a progress stream drops (issue #4) -- reads the same
// decision_events rows the stream is sourced from, scoped to the requesting
// user (unlike the stream, this is a durable-data read, so it goes through
// the authenticated request() wrapper and naturally 401s/throws for guests,
// who have nothing persisted to recover anyway).
export async function fetchEvents(sessionId: string, afterId: number): Promise<PolledProgressEvent[]> {
  const data = await request<{ events: PolledProgressEvent[] }>(
    `/api/messages/events?session_id=${encodeURIComponent(sessionId)}&after_id=${afterId}`,
  );
  return data.events;
}

const EVENT_LABELS: Record<string, string> = {
  "intent/start": "Detecting intent…",
  "intent/finish": "Intent confirmed…",
  "reasoning/start": "Deciding what to do next…",
  "reasoning/finish": "Reviewing the next step…",
  "retrieval/start": "Searching the FHIR knowledge base…",
  "retrieval/finish": "Search results ready…",
  // clarification/finish deliberately doesn't echo the answer back -- the
  // user just typed it (see backend/graph.py's clarification_node, which
  // only ever logs "finish", never "start", since interrupt() replays the
  // node from the top on every resume).
  "clarification/finish": "Incorporating your answer…",
};

export function describeEvent(evt: ProgressEvent): string {
  if (evt.node_name === "retrieval" && evt.event_type === "search_fhir_kb") {
    const query = (evt.input_data as { query?: string } | undefined)?.query;
    return query ? `Searching: "${query}"` : "Searching…";
  }
  // Forward-compatible default: an event type this mapping doesn't know
  // about yet (a future node, say) still shows *something* live rather than
  // nothing.
  return EVENT_LABELS[`${evt.node_name}/${evt.event_type}`] ?? "Working…";
}

export async function listConversations(searchRegex?: string): Promise<ConversationSummary[]> {
  const query = searchRegex ? `?search=${encodeURIComponent(searchRegex)}` : "";
  const data = await request<{ conversations: ConversationSummary[] }>(`/api/conversations${query}`);
  return data.conversations;
}

export async function getConversation(id: string): Promise<ConversationDetail> {
  return request(`/api/conversations/${id}`);
}

export async function rerunConversation(id: string): Promise<MessageResponse> {
  return request(`/api/conversations/${id}/rerun`, { method: "POST" });
}

export async function getResourceMapping(
  sessionId: string,
  resourceType: string,
): Promise<ResourceMappingResponse> {
  return request(`/api/conversations/${sessionId}/mapping`, {
    method: "POST",
    body: JSON.stringify({ resource_type: resourceType }),
  });
}

export async function submitFeedback(
  conversationId: string,
  userExpectation: string,
): Promise<FeedbackReceipt> {
  return request("/api/feedback", {
    method: "POST",
    body: JSON.stringify({ conversation_id: conversationId, user_expectation: userExpectation }),
  });
}

export async function listFeedback(): Promise<FeedbackSummary[]> {
  const data = await request<{ reports: FeedbackSummary[] }>("/api/feedback");
  return data.reports;
}

export async function getFeedback(id: string): Promise<FeedbackDetail> {
  return request(`/api/feedback/${id}`);
}

export async function getAdminSettings(): Promise<AdminSettings> {
  return request("/api/admin/settings");
}

export async function updateAdminSettings(intentModel: string, synthModel: string): Promise<AdminSettings> {
  return request("/api/admin/settings", {
    method: "POST",
    body: JSON.stringify({ intent_model: intentModel, synth_model: synthModel }),
  });
}

export async function adminRerunConversation(conversationId: string, model: string): Promise<MessageResponse> {
  return request(`/api/admin/conversations/${conversationId}/rerun`, {
    method: "POST",
    body: JSON.stringify({ model }),
  });
}
