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

export async function startConversation(fields: StartFields): Promise<MessageResponse> {
  return request("/api/messages", {
    method: "POST",
    body: JSON.stringify({ session_id: null, ...fields }),
  });
}

export async function postMessage(sessionId: string, message: string): Promise<MessageResponse> {
  return request("/api/messages", {
    method: "POST",
    body: JSON.stringify({ session_id: sessionId, message }),
  });
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
