import { useEffect, useMemo, useRef, useState } from "react";
import {
  AppSettings,
  AssistantStreamEvent,
  ConversationMessage,
  MemoryRecord,
  MemorySnapshot,
  ResourceUsageSnapshot,
  SessionState,
  SessionStats,
  SystemStatus
} from "../types";

const HTTP_BASE = "http://127.0.0.1:8765";
const WS_BASE = "ws://127.0.0.1:8765/ws/events";

const defaultSettings: AppSettings = {
  audio_input_device: "default",
  audio_output_device: "default",
  push_to_talk_hotkey: "Mouse Hold",
  llm_provider: "local",
  llm_model_path: "E:\\program\\models\\llm\\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf",
  llm_api_base: "",
  llm_api_model: "",
  tts_voice_id: "default",
  memory_file_path: "",
  memory_root_path: "",
  backend_mode: "auto",
  whisper_model_name: "E:\\program\\models\\asr\\faster-whisper-medium.en"
};

const defaultStatus: SystemStatus = {
  backend_mode: "mock",
  llm_available: false,
  asr_available: false,
  tts_available: false,
  vector_index_available: false,
  vector_index_provider: "json",
  llm_provider: "local",
  llm_resolved_provider: "local",
  llm_error: null,
  asr_error: null,
  tts_error: null
};

const defaultStats: SessionStats = {
  latestFirstAudioMs: null,
  averageFirstAudioMs: null,
  completedTurns: 0,
  lastCompletedAt: ""
};

const defaultUsage: ResourceUsageSnapshot = {
  cpu_percent: null,
  memory_mb: null,
  gpu_util_percent: null,
  gpu_memory_mb: null,
  gpu_scope: "unavailable",
  sampled_at: ""
};

const isDesktopApp =
  typeof window !== "undefined" &&
  ("__TAURI_INTERNALS__" in window || "__TAURI__" in window);

export function useAssistantSession(options?: { enableUsagePolling?: boolean }) {
  const enableUsagePolling = options?.enableUsagePolling ?? true;
  const [sessionId] = useState(() => crypto.randomUUID());
  const [sessionState, setSessionState] = useState<SessionState>("idle");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [partialAsr, setPartialAsr] = useState("");
  const [memoryRecall, setMemoryRecall] = useState<MemoryRecord[]>([]);
  const [memoryWritten, setMemoryWritten] = useState<MemoryRecord[]>([]);
  const [memorySnapshot, setMemorySnapshot] = useState<MemorySnapshot | null>(null);
  const [status, setStatus] = useState<SystemStatus>(defaultStatus);
  const [settings, setSettings] = useState<AppSettings>(defaultSettings);
  const [usage, setUsage] = useState<ResourceUsageSnapshot>(defaultUsage);
  const [stats, setStats] = useState<SessionStats>(defaultStats);
  const [error, setError] = useState("");
  const [streamConnected, setStreamConnected] = useState(false);

  const activeAssistantMessageId = useRef<string | null>(null);
  const activeTurnId = useRef<string | null>(null);
  const turnStartedAt = useRef<number | null>(null);
  const firstAudioCapturedForTurn = useRef(false);
  const speechQueue = useRef<Promise<void>>(Promise.resolve());
  const latencySamples = useRef<number[]>([]);
  const speechGeneration = useRef(0);
  const activeVoices = useRef<SpeechSynthesisVoice[]>([]);
  const spokenSentenceCount = useRef(0);

  useEffect(() => {
    if (!("speechSynthesis" in window)) {
      return;
    }

    const loadVoices = () => {
      const voices = window.speechSynthesis.getVoices();
      if (voices.length) {
        activeVoices.current = voices;
      }
    };

    loadVoices();
    window.speechSynthesis.addEventListener("voiceschanged", loadVoices);

    return () => {
      window.speechSynthesis.removeEventListener("voiceschanged", loadVoices);
    };
  }, []);

  useEffect(() => {
    let socket: WebSocket | null = null;
    let canceled = false;
    let reconnectTimer: number | null = null;
    let usageTimer: number | null = null;
    let healthTimer: number | null = null;

    async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
      let lastError: unknown;
      for (let attempt = 0; attempt < 20; attempt += 1) {
        try {
          const response = await fetch(`${HTTP_BASE}${path}`, init);
          if (!response.ok) {
            throw new Error(`Request failed: ${response.status}`);
          }
          return (await response.json()) as T;
        } catch (fetchError) {
          lastError = fetchError;
          await new Promise((resolve) => setTimeout(resolve, 750));
        }
      }
      throw lastError instanceof Error ? lastError : new Error("Backend unavailable.");
    }

    function scheduleUsagePolling() {
      const poll = async () => {
        if (canceled) {
          return;
        }
        try {
          const payload = await fetchJson<{ usage: ResourceUsageSnapshot }>("/api/resource-usage");
          if (!canceled) {
            setUsage(payload.usage);
          }
        } catch {
          // Keep the previous snapshot if telemetry fails briefly.
        } finally {
          if (!canceled) {
            usageTimer = window.setTimeout(poll, 1000);
          }
        }
      };

      void poll();
    }

    function scheduleHealthPolling() {
      const poll = async () => {
        if (canceled) {
          return;
        }
        try {
          const payload = await fetchJson<{ status: SystemStatus }>("/health");
          if (!canceled) {
            setStatus(payload.status);
            if (payload.status.asr_available && payload.status.llm_available) {
              setError("");
            } else if (!payload.status.asr_available) {
              setError(payload.status.asr_error || "ASR model is unavailable.");
            } else if (!payload.status.llm_available) {
              setError(payload.status.llm_error || "LLM provider is unavailable.");
            }
          }
        } catch {
          // Keep the current UI state if health checking fails briefly.
        } finally {
          if (!canceled) {
            healthTimer = window.setTimeout(poll, 2000);
          }
        }
      };

      void poll();
    }

    function connectSocket() {
      if (canceled) {
        return;
      }

      socket = new WebSocket(WS_BASE);
      socket.onopen = () => {
        setStreamConnected(true);
        socket?.send("subscribe");
        void fetchJson<{ status: SystemStatus }>("/health")
          .then((payload) => {
            if (canceled) {
              return;
            }
            setStatus(payload.status);
            if (payload.status.asr_available && payload.status.llm_available) {
              setError("");
            }
          })
          .catch(() => {
            // The reconnect loop will keep trying until the backend is ready again.
          });
      };
      socket.onmessage = (message) => {
        let event: AssistantStreamEvent;
        try {
          event = JSON.parse(message.data) as AssistantStreamEvent;
        } catch {
          setError("Received an invalid backend stream event. Reconnecting usually fixes this.");
          return;
        }
        if (!event || typeof event !== "object" || typeof event.type !== "string") {
          setError("Received an unknown backend stream event.");
          return;
        }
        if (event.session_id !== sessionId) {
          return;
        }
        handleEvent(event);
      };
      socket.onclose = () => {
        setStreamConnected(false);
        if (canceled) {
          return;
        }
        reconnectTimer = window.setTimeout(() => {
          connectSocket();
        }, 1000);
      };
      socket.onerror = () => {
        setStreamConnected(false);
        socket?.close();
      };
    }

    async function bootstrap() {
      const health = await fetchJson<{ status: SystemStatus }>("/health");
      if (!canceled) {
        setStatus(health.status);
        if (!health.status.asr_available) {
          setError(health.status.asr_error || "ASR model is unavailable.");
        } else if (!health.status.llm_available) {
          setError(health.status.llm_error || "LLM provider is unavailable.");
        } else {
          setError("");
        }
      }

      const settingsJson = await fetchJson<AppSettings>("/api/settings");
      if (!canceled) {
        setSettings(settingsJson);
      }

      const memoryJson = await fetchJson<{ memory: MemorySnapshot }>("/api/memory");
      if (!canceled) {
        setMemorySnapshot(memoryJson.memory);
      }

      if (canceled) {
        return;
      }

      if (enableUsagePolling) {
        scheduleUsagePolling();
      } else {
        setUsage(defaultUsage);
      }
      scheduleHealthPolling();
      connectSocket();
    }

    bootstrap().catch((err) => {
      setError(
        err instanceof Error
          ? err.message
          : "Desktop backend is unavailable. Check the packaged backend runtime and model paths."
      );
    });

    return () => {
      canceled = true;
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
      }
      if (usageTimer !== null) {
        window.clearTimeout(usageTimer);
      }
      if (healthTimer !== null) {
        window.clearTimeout(healthTimer);
      }
      socket?.close();
    };
  }, [enableUsagePolling, sessionId]);

  async function refreshMemorySnapshot() {
    const payload = await fetch(`${HTTP_BASE}/api/memory`);
    if (!payload.ok) {
      throw new Error(`Failed to refresh memory: ${payload.status}`);
    }
    const decoded = (await payload.json()) as { memory: MemorySnapshot };
    setMemorySnapshot(decoded.memory);
    return decoded.memory;
  }

  async function rebuildMemoryIndex() {
    const payload = await fetch(`${HTTP_BASE}/api/memory/rebuild`, {
      method: "POST"
    });
    if (!payload.ok) {
      throw new Error(`Failed to rebuild memory index: ${payload.status}`);
    }
    const decoded = (await payload.json()) as { ok: boolean; memory: MemorySnapshot };
    setMemorySnapshot(decoded.memory);
    return decoded.memory;
  }

  function speakSentence(sentence: string) {
    if (isDesktopApp) {
      return;
    }
    const generation = speechGeneration.current;
    speechQueue.current = speechQueue.current.then(
      () =>
        new Promise<void>((resolve) => {
          if (!("speechSynthesis" in window) || !sentence.trim()) {
            resolve();
            return;
          }
          if (generation !== speechGeneration.current) {
            resolve();
            return;
          }
          const voices = window.speechSynthesis.getVoices();
          if (voices.length) {
            activeVoices.current = voices;
          }
          const utterance = new SpeechSynthesisUtterance(sentence);
          utterance.lang = "en-US";
          utterance.rate = 0.98;
          utterance.pitch = 1;
          utterance.volume = 1;
          const preferredVoice =
            activeVoices.current.find((voice) => /^en(-|_)/i.test(voice.lang) && /female|zira|aria|jenny/i.test(voice.name)) ||
            activeVoices.current.find((voice) => /^en(-|_)/i.test(voice.lang)) ||
            activeVoices.current[0];
          if (preferredVoice) {
            utterance.voice = preferredVoice;
          }
          utterance.onend = () => resolve();
          utterance.onerror = () => resolve();
          if (generation !== speechGeneration.current) {
            resolve();
            return;
          }
          window.speechSynthesis.speak(utterance);
        })
    );
  }

  function stopSpeechPlayback() {
    speechGeneration.current += 1;
    spokenSentenceCount.current = 0;
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }
    speechQueue.current = Promise.resolve();
  }

  function ensureAssistantMessage(thinking = false) {
    if (activeAssistantMessageId.current) {
      return activeAssistantMessageId.current;
    }

    const id = crypto.randomUUID();
    activeAssistantMessageId.current = id;
    setMessages((current) => [
      ...current,
      {
        id,
        role: "assistant",
        text: thinking ? "Thinking..." : "",
        timestamp: new Date().toISOString(),
        status: "streaming",
        isThinking: thinking,
        assistantTurn: {
          reply_text: "",
          native_rewrite: "",
          optional_tip: "",
          raw_response: ""
        }
      }
    ]);
    return id;
  }

  function finalizeLatencySample() {
    if (turnStartedAt.current === null || firstAudioCapturedForTurn.current) {
      return;
    }
    const elapsed = performance.now() - turnStartedAt.current;
    firstAudioCapturedForTurn.current = true;
    latencySamples.current.push(elapsed);
    const average =
      latencySamples.current.reduce((total, value) => total + value, 0) /
      latencySamples.current.length;
    setStats((current) => ({
      latestFirstAudioMs: elapsed,
      averageFirstAudioMs: average,
      completedTurns: current.completedTurns,
      lastCompletedAt: current.lastCompletedAt
    }));
  }

  function resetTurnTracking() {
    turnStartedAt.current = null;
    firstAudioCapturedForTurn.current = false;
    activeAssistantMessageId.current = null;
    activeTurnId.current = null;
    spokenSentenceCount.current = 0;
  }

  function pruneStaleThinkingBubble() {
    setMessages((current) =>
      current.filter(
        (message) => !(message.role === "assistant" && message.isThinking && message.status === "streaming")
      )
    );
  }

  function handleEvent(event: AssistantStreamEvent) {
    if (event.turn_id && activeTurnId.current && event.turn_id !== activeTurnId.current) {
      if (event.type === "memory_written") {
        return;
      }
      if (event.type === "turn_complete" || event.type === "session_state" || event.type === "error") {
        return;
      }
      if (
        event.type === "asr_partial" ||
        event.type === "asr_final" ||
        event.type === "memory_recall" ||
        event.type === "llm_token" ||
        event.type === "llm_sentence" ||
        event.type === "tts_started" ||
        event.type === "tts_finished" ||
        event.type === "assistant_turn"
      ) {
        return;
      }
    }

    switch (event.type) {
      case "session_state":
        setSessionState(event.state);
        if (event.state !== "error") {
          setError("");
        }
        if (event.state === "thinking") {
          ensureAssistantMessage(true);
        }
        break;
      case "asr_partial":
        setPartialAsr(event.text);
        break;
      case "asr_final":
        setPartialAsr(event.text);
        pruneStaleThinkingBubble();
        activeAssistantMessageId.current = null;
        setMessages((current) => [
          ...current,
          {
            id: crypto.randomUUID(),
            role: "user",
            text: event.text,
            timestamp: new Date().toISOString(),
            status: "final"
          }
        ]);
        break;
      case "memory_recall":
        setMemoryRecall(event.recall.records);
        ensureAssistantMessage(true);
        break;
      case "llm_token":
        ensureAssistantMessage(false);
        setMessages((current) =>
          current.map((message) =>
            message.id === activeAssistantMessageId.current
              ? {
                  ...message,
                  text: (message.assistantTurn?.reply_text ?? "") + event.token,
                  isThinking: false,
                  assistantTurn: {
                    reply_text: (message.assistantTurn?.reply_text ?? "") + event.token,
                    native_rewrite: message.assistantTurn?.native_rewrite ?? "",
                    optional_tip: message.assistantTurn?.optional_tip ?? "",
                    raw_response: message.assistantTurn?.raw_response ?? ""
                  }
                }
              : message
          )
        );
        break;
      case "llm_sentence":
        spokenSentenceCount.current += 1;
        speakSentence(event.sentence);
        break;
      case "tts_started":
        finalizeLatencySample();
        setSessionState("speaking");
        break;
      case "tts_finished":
        break;
      case "assistant_turn":
        ensureAssistantMessage(false);
        setMessages((current) =>
          current.map((message) =>
            message.id === activeAssistantMessageId.current
              ? {
                  ...message,
                  text: event.turn.reply_text,
                  status: "final",
                  isThinking: false,
                  assistantTurn: event.turn
                }
              : message
          )
        );
        if (spokenSentenceCount.current === 0 && event.turn.reply_text.trim()) {
          spokenSentenceCount.current += 1;
          speakSentence(event.turn.reply_text);
        }
        break;
      case "memory_written":
        if (!event.turn_id || event.turn_id === activeTurnId.current) {
          setMemoryWritten(event.records);
          void refreshMemorySnapshot().catch(() => {
            // The turn-specific memory event already succeeded; snapshot refresh is best-effort.
          });
        }
        break;
      case "turn_complete":
        setStats((current) => ({
          ...current,
          completedTurns: current.completedTurns + (firstAudioCapturedForTurn.current ? 1 : 0),
          lastCompletedAt: new Date().toISOString()
        }));
        pruneStaleThinkingBubble();
        setSessionState("idle");
        resetTurnTracking();
        break;
      case "error":
        setError(event.message);
        pruneStaleThinkingBubble();
        setSessionState("error");
        resetTurnTracking();
        break;
      default:
        break;
    }
  }

  async function submitAudio(audio: Blob) {
    setError("");
    stopSpeechPlayback();
    pruneStaleThinkingBubble();
    const turnId = crypto.randomUUID();
    activeTurnId.current = turnId;
    activeAssistantMessageId.current = null;
    turnStartedAt.current = performance.now();
    firstAudioCapturedForTurn.current = false;

    const form = new FormData();
    form.append("session_id", sessionId);
    form.append("turn_id", turnId);
    form.append("audio", audio, "utterance.wav");

    const response = await fetch(`${HTTP_BASE}/api/voice-turn`, {
      method: "POST",
      body: form
    });

    if (!response.ok) {
      turnStartedAt.current = null;
      const payload = await response.json();
      throw new Error(payload.detail ?? "Failed to submit voice turn.");
    }

    window.setTimeout(() => {
      if (activeTurnId.current === turnId && turnStartedAt.current !== null) {
        setError(
          streamConnected
            ? "The backend accepted the turn, but no stream events arrived yet."
            : "The backend accepted the turn, but the event stream is disconnected."
        );
      }
    }, 20_000);
  }

  const latestAssistantText = useMemo(() => {
    const assistantMessages = messages.filter((message) => message.role === "assistant");
    return assistantMessages[assistantMessages.length - 1]?.assistantTurn?.reply_text ?? "";
  }, [messages]);

  return {
    sessionId,
    sessionState,
    messages,
    partialAsr,
    memoryRecall,
    memoryWritten,
    memorySnapshot,
    settings,
    status,
    usage,
    stats,
    streamConnected,
    error,
    latestAssistantText,
    refreshMemorySnapshot,
    rebuildMemoryIndex,
    setError,
    setSessionState,
    stopSpeechPlayback,
    submitAudio
  };
}
