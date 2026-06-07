export type SessionState =
  | "idle"
  | "recording"
  | "transcribing"
  | "recalling"
  | "thinking"
  | "generating"
  | "speaking"
  | "error";

export interface AssistantTurnPayload {
  reply_text: string;
  native_rewrite: string;
  optional_tip: string;
  raw_response: string;
}

export interface ConversationMessage {
  id: string;
  role: "user" | "assistant" | "system";
  text: string;
  timestamp: string;
  status: "streaming" | "final";
  assistantTurn?: AssistantTurnPayload;
  isThinking?: boolean;
}

export interface MemoryRecord {
  memory_id: string;
  category: string;
  text: string;
  salience: number;
  created_at: string;
  updated_at: string;
  source_turn_id: string;
}

export interface MemoryRecallResult {
  records: MemoryRecord[];
  summary: string;
  source_ids: string[];
}

export interface MemorySnapshot {
  records: MemoryRecord[];
  grouped: Record<string, MemoryRecord[]>;
  summary: string;
  counts: Record<string, number>;
  memory_file_path: string;
  memory_root_path: string;
  vector_index_provider: string;
}

export interface AppSettings {
  audio_input_device: string;
  audio_output_device: string;
  push_to_talk_hotkey: string;
  llm_provider: "auto" | "local" | "api";
  llm_model_path: string;
  llm_api_base: string;
  llm_api_model: string;
  tts_voice_id: string;
  memory_file_path: string;
  memory_root_path: string;
  backend_mode: "auto" | "mock" | "real";
  whisper_model_name?: string;
}

export interface SystemStatus {
  backend_mode: string;
  llm_available: boolean;
  asr_available: boolean;
  tts_available: boolean;
  vector_index_available: boolean;
  vector_index_provider?: string;
  llm_provider?: string;
  llm_resolved_provider?: string;
  llm_error?: string | null;
  asr_error?: string | null;
  tts_error?: string | null;
}

export interface ResourceUsageSnapshot {
  cpu_percent: number | null;
  memory_mb: number | null;
  gpu_util_percent: number | null;
  gpu_memory_mb: number | null;
  gpu_scope: "process" | "device" | "unavailable";
  sampled_at: string;
}

export interface SessionStats {
  latestFirstAudioMs: number | null;
  averageFirstAudioMs: number | null;
  completedTurns: number;
  lastCompletedAt: string;
}

export type AssistantStreamEvent =
  | { type: "session_state"; session_id: string; turn_id?: string; state: SessionState }
  | { type: "asr_partial"; session_id: string; turn_id?: string; text: string }
  | { type: "asr_final"; session_id: string; turn_id?: string; text: string }
  | { type: "memory_recall"; session_id: string; turn_id?: string; recall: MemoryRecallResult }
  | { type: "llm_token"; session_id: string; turn_id?: string; token: string }
  | { type: "llm_sentence"; session_id: string; turn_id?: string; sentence: string }
  | { type: "tts_started"; session_id: string; turn_id?: string; sentence: string }
  | { type: "tts_finished"; session_id: string; turn_id?: string; sentence: string }
  | { type: "assistant_turn"; session_id: string; turn_id?: string; turn: AssistantTurnPayload }
  | { type: "memory_written"; session_id: string; turn_id?: string; records: MemoryRecord[] }
  | { type: "turn_complete"; session_id: string; turn_id?: string }
  | { type: "error"; session_id: string; turn_id?: string; message: string };
