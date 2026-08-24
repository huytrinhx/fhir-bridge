export interface AuthUser {
  id: string;
  username: string | null;
  email: string | null;
  display_name: string | null;
  is_admin: boolean;
}

export interface AdminSettings {
  intent_model: string;
  synth_model: string;
  models: string[];
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

export interface MappedFieldEntry {
  segment: string;
  field_position: number;
  field_name: string;
  raw_value: string;
  target_path: string;
  explanation: string | null;
  source_url: string;
  applied_to_skeleton: boolean;
}

export interface UnmappedFieldEntry {
  segment: string;
  field_position: number;
  raw_value: string;
}

export interface ResourceMappingResponse {
  resource_type: string;
  skeleton: Record<string, unknown>;
  mapped_fields: MappedFieldEntry[];
  unmapped_fields: UnmappedFieldEntry[];
}

export type MessageResponse =
  | { kind: "out_of_scope"; session_id: string; reason: string }
  | { kind: "clarifying_question"; session_id: string; question: string; options: string[] | null }
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
}

// Live per-node status, from GET /api/messages/stream (SSE) -- and, with the
// same shape, GET /api/messages/events (the polling fallback for a dropped
// stream), so one renderer (see describeEvent in api.ts) covers both.
export interface ProgressEvent {
  node_name: string;
  event_type: string;
  input_data: unknown;
  output_data: unknown;
}

export interface PolledProgressEvent extends ProgressEvent {
  id: number;
  ts: string;
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
