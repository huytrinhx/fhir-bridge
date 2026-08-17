export interface AuthUser {
  id: string;
  username: string | null;
  email: string | null;
  display_name: string | null;
}

export interface Citation {
  title: string;
  url: string;
  spec_version: string;
}

export interface RecommendationItem {
  resource_type: string;
  category: "must_have" | "potentially_needed";
  rationale: string;
  citation: Citation;
}

export type MessageResponse =
  | { kind: "out_of_scope"; session_id: string; reason: string }
  | { kind: "clarifying_question"; session_id: string; questions: string[] }
  | {
      kind: "final_recommendation";
      session_id: string;
      must_have: RecommendationItem[];
      potentially_needed: RecommendationItem[];
      dropped: string[];
      redacted: string[];
    };

export interface StartFields {
  message: string;
  data_sample?: string | null;
  data_format?: string | null;
  terminology_system?: string | null;
  model?: string | null;
}

export interface ConversationSummary {
  id: string;
  created_at: string;
  initial_message: string;
  model: string;
  outcome_kind: string;
}

export interface DisplayTranscriptEntry {
  role: "user" | "assistant";
  kind: "message" | "search" | "question" | "answer";
  text: string;
}

export interface ConversationDetail {
  id: string;
  created_at: string;
  initial_message: string;
  data_format: string | null;
  terminology_system: string | null;
  data_sample: string | null;
  model: string;
  outcome_kind: string;
  display_transcript: DisplayTranscriptEntry[];
  last_outcome: MessageResponse | null;
}

export interface FeedbackReceipt {
  id: string;
  created_at: string;
}

export interface FeedbackSummary {
  id: string;
  created_at: string;
  conversation_id: string;
  user_expectation: string;
  initial_message: string;
  model: string;
}

export interface FeedbackDetail extends FeedbackSummary {
  transcript_snapshot: DisplayTranscriptEntry[];
  outcome_snapshot: MessageResponse | null;
}
