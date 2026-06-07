import { PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { AudioRecorder } from "./audio";
import { useAssistantSession } from "./hooks/useAssistantSession";
import { AssistantTurnPayload, ConversationMessage, MemoryRecord, MemorySnapshot, ResourceUsageSnapshot } from "./types";

type SidebarTab = "coach" | "telemetry" | "memory";

const CANCEL_SWIPE_PX = 88;

function formatLatency(ms: number | null) {
  if (ms === null) {
    return "Unavailable";
  }
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatUsage(value: number | null, suffix: string) {
  if (value === null) {
    return "Unavailable";
  }
  return `${value.toFixed(1)}${suffix}`;
}

function formatTimestamp(value: string) {
  if (!value) {
    return "Not yet";
  }
  return new Date(value).toLocaleTimeString();
}

function formatPathTail(value: string) {
  if (!value) {
    return "Unavailable";
  }
  const parts = value.split(/[\\/]/).filter(Boolean);
  return parts.slice(-2).join("\\") || value;
}

function tokenize(value: string) {
  return value.match(/\S+|\s+/g) ?? [];
}

function renderRewriteDiff(source: string, rewrite: string) {
  const sourceTokens = tokenize(source);
  const rewriteTokens = tokenize(rewrite);

  return rewriteTokens.map((token, index) => {
    const sourceToken = sourceTokens[index] ?? "";
    const changed = token.trim() !== "" && token !== sourceToken;
    return (
      <span key={`${index}-${token}`} className={changed ? "rewrite-token changed" : "rewrite-token"}>
        {token}
      </span>
    );
  });
}

function StatusPill({ label, tone = "neutral" }: { label: string; tone?: "neutral" | "good" | "warn" | "bad" }) {
  return <span className={`status-pill ${tone}`}>{label}</span>;
}

function SidebarSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="sidebar-section">
      <div className="section-title">{title}</div>
      {children}
    </section>
  );
}

function MemoryList({ records, emptyText }: { records: MemoryRecord[]; emptyText: string }) {
  if (!records.length) {
    return <p className="muted-text">{emptyText}</p>;
  }

  return (
    <ul className="memory-list">
      {records.map((record) => (
        <li key={record.memory_id}>
          <span className="memory-tag">{record.category}</span>
          <span>{record.text}</span>
        </li>
      ))}
    </ul>
  );
}

function ResourceUsageCard({ usage }: { usage: ResourceUsageSnapshot }) {
  return (
    <SidebarSection title="Resource Usage">
      <dl className="metric-grid">
        <div>
          <dt>CPU</dt>
          <dd>{formatUsage(usage.cpu_percent, "%")}</dd>
        </div>
        <div>
          <dt>Memory</dt>
          <dd>{formatUsage(usage.memory_mb, " MB")}</dd>
        </div>
        <div>
          <dt>GPU Util</dt>
          <dd>
            {formatUsage(usage.gpu_util_percent, "%")}
            {usage.gpu_scope === "device" ? " (device)" : ""}
          </dd>
        </div>
        <div>
          <dt>GPU Memory</dt>
          <dd>{formatUsage(usage.gpu_memory_mb, " MB")}</dd>
        </div>
      </dl>
    </SidebarSection>
  );
}

function SessionStatsCard({
  latestFirstAudioMs,
  averageFirstAudioMs,
  completedTurns,
  lastCompletedAt
}: {
  latestFirstAudioMs: number | null;
  averageFirstAudioMs: number | null;
  completedTurns: number;
  lastCompletedAt: string;
}) {
  return (
    <SidebarSection title="Session Stats">
      <dl className="metric-grid">
        <div>
          <dt>Latest First Audio</dt>
          <dd>{formatLatency(latestFirstAudioMs)}</dd>
        </div>
        <div>
          <dt>Average First Audio</dt>
          <dd>{formatLatency(averageFirstAudioMs)}</dd>
        </div>
        <div>
          <dt>Completed Turns</dt>
          <dd>{completedTurns}</dd>
        </div>
        <div>
          <dt>Last Completed</dt>
          <dd>{formatTimestamp(lastCompletedAt)}</dd>
        </div>
      </dl>
    </SidebarSection>
  );
}

function SettingsPreview({
  whisperModelName,
  llmProvider,
  llmResolvedProvider,
  llmTarget,
  vectorProvider,
  streamConnected
}: {
  whisperModelName?: string;
  llmProvider: string;
  llmResolvedProvider?: string;
  llmTarget: string;
  vectorProvider?: string;
  streamConnected: boolean;
}) {
  return (
    <SidebarSection title="Runtime">
      <dl className="preview-grid">
        <div>
          <dt>LLM Provider</dt>
          <dd>{llmProvider} → {llmResolvedProvider || "unknown"}</dd>
        </div>
        <div>
          <dt>LLM Target</dt>
          <dd className="path-preview">{formatPathTail(llmTarget)}</dd>
        </div>
        <div>
          <dt>ASR Model</dt>
          <dd className="path-preview">{formatPathTail(whisperModelName || "")}</dd>
        </div>
        <div>
          <dt>Vector Index</dt>
          <dd>{vectorProvider || "json"}</dd>
        </div>
        <div>
          <dt>Event Stream</dt>
          <dd>{streamConnected ? "connected" : "reconnecting"}</dd>
        </div>
      </dl>
    </SidebarSection>
  );
}

function MemorySnapshotPanel({
  snapshot,
  onRefresh,
  onRebuild,
  busy
}: {
  snapshot: MemorySnapshot | null;
  onRefresh: () => void;
  onRebuild: () => void;
  busy: boolean;
}) {
  const categories = [
    ["constraints", "Coach Rules"],
    ["profile", "Learner Profile"],
    ["mistakes", "Frequent Mistakes"],
    ["topics", "Practice Topics"],
    ["notes", "Notes"]
  ] as const;

  return (
    <SidebarSection title="Long-Term Learning Memory">
      <div className="memory-actions">
        <button type="button" className="ghost-button" onClick={onRefresh} disabled={busy}>
          Refresh
        </button>
        <button type="button" className="ghost-button" onClick={onRebuild} disabled={busy}>
          Rebuild Index
        </button>
      </div>
      {snapshot?.summary ? <p className="memory-summary">{snapshot.summary}</p> : <p className="muted-text">No durable memory yet.</p>}
      <div className="memory-count-grid">
        {categories.map(([category, label]) => (
          <div key={category}>
            <span>{label}</span>
            <strong>{snapshot?.counts?.[category] ?? 0}</strong>
          </div>
        ))}
      </div>
      <div className="memory-category-stack">
        {categories.map(([category, label]) => {
          const records = snapshot?.grouped?.[category] ?? [];
          return (
            <section key={category} className="memory-category">
              <div className="memory-category-title">{label}</div>
              <MemoryList records={records.slice(0, 4)} emptyText="Nothing saved here yet." />
            </section>
          );
        })}
      </div>
      {snapshot?.memory_root_path ? <p className="path-note">{snapshot.memory_root_path}</p> : null}
    </SidebarSection>
  );
}

function AssistantBubble({
  message,
  previousUserText
}: {
  message: ConversationMessage;
  previousUserText: string;
}) {
  const assistantTurn: AssistantTurnPayload | undefined = message.assistantTurn;
  const replyText = assistantTurn?.reply_text || message.text;
  const nativeRewrite = assistantTurn?.native_rewrite?.trim() || "";
  const optionalTip = assistantTurn?.optional_tip?.trim() || "";

  return (
    <article className={`message-card assistant ${message.isThinking ? "thinking" : ""}`}>
      <div className="message-label">Coach</div>
      <p className="message-text">{replyText || "Thinking..."}</p>
      {nativeRewrite ? (
        <section className="rewrite-panel">
          <div className="panel-label">A more natural way to say it</div>
          <p className="rewrite-text">{renderRewriteDiff(previousUserText, nativeRewrite)}</p>
        </section>
      ) : null}
      {optionalTip ? (
        <section className="tip-panel">
          <div className="panel-label">Optional Tip</div>
          <p>{optionalTip}</p>
        </section>
      ) : null}
    </article>
  );
}

export default function App() {
  const [sidebarTab, setSidebarTab] = useState<SidebarTab>("coach");
  const [cancelIntent, setCancelIntent] = useState(false);
  const {
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
    setError,
    setSessionState,
    stopSpeechPlayback,
    submitAudio,
    refreshMemorySnapshot,
    rebuildMemoryIndex
  } = useAssistantSession();
  const recorderRef = useRef<AudioRecorder | null>(null);
  const messageStreamRef = useRef<HTMLDivElement | null>(null);
  const recorderStartPromiseRef = useRef<Promise<void> | null>(null);
  const recordingRef = useRef(false);
  const finishAfterStartRef = useRef(false);
  const cancelIntentRef = useRef(false);
  const pointerIdRef = useRef<number | null>(null);
  const pointerStartYRef = useRef<number | null>(null);
  const stopSpeechPlaybackRef = useRef(stopSpeechPlayback);
  const [memoryActionBusy, setMemoryActionBusy] = useState(false);

  const canStartRecording = sessionState === "idle" || sessionState === "speaking" || sessionState === "error";
  const recordDisabled = !canStartRecording && sessionState !== "recording";
  const llmTarget = settings.llm_provider === "api" ? settings.llm_api_model : settings.llm_model_path;
  const latestTranscript = partialAsr || "Speak in English. I will answer first, then correct lightly.";
  const transcriptHint = cancelIntent
    ? "Release now to cancel this take."
    : sessionState === "recording"
      ? `Slide up ${CANCEL_SWIPE_PX}px to cancel.`
      : "Hold to speak. Slide up before release to cancel.";

  useEffect(() => {
    stopSpeechPlaybackRef.current = stopSpeechPlayback;
  }, [stopSpeechPlayback]);

  useEffect(() => {
    return () => {
      stopSpeechPlaybackRef.current();
    };
  }, []);

  useEffect(() => {
    const target = messageStreamRef.current;
    if (!target) {
      return;
    }
    target.scrollTo({ top: target.scrollHeight, behavior: "smooth" });
  }, [messages, sessionState, partialAsr]);

  async function runMemoryAction(action: "refresh" | "rebuild") {
    setMemoryActionBusy(true);
    try {
      if (action === "refresh") {
        await refreshMemorySnapshot();
      } else {
        await rebuildMemoryIndex();
      }
      setError("");
    } catch (memoryError) {
      setError(memoryError instanceof Error ? memoryError.message : String(memoryError));
    } finally {
      setMemoryActionBusy(false);
    }
  }

  async function startRecording() {
    if (!canStartRecording || recordingRef.current) {
      return;
    }

    recordingRef.current = true;
    finishAfterStartRef.current = false;
    cancelIntentRef.current = false;
    setCancelIntent(false);
    setError("");
    stopSpeechPlayback();
    setSessionState("recording");

    const recorder = new AudioRecorder();
    recorderRef.current = recorder;
    const startPromise = recorder.start();
    recorderStartPromiseRef.current = startPromise;
    try {
      await startPromise;
      if (recorderStartPromiseRef.current === startPromise) {
        recorderStartPromiseRef.current = null;
      }
      if (finishAfterStartRef.current) {
        const shouldCancel = cancelIntentRef.current;
        finishAfterStartRef.current = false;
        await finishRecording(shouldCancel);
      }
    } catch (recordError) {
      recordingRef.current = false;
      finishAfterStartRef.current = false;
      recorderStartPromiseRef.current = null;
      recorderRef.current = null;
      setSessionState("error");
      setError(recordError instanceof Error ? recordError.message : String(recordError));
    }
  }

  async function finishRecording(shouldCancel: boolean) {
    if (!recordingRef.current) {
      return;
    }

    if (recorderStartPromiseRef.current) {
      finishAfterStartRef.current = true;
      cancelIntentRef.current = shouldCancel;
      setCancelIntent(shouldCancel);
      return;
    }

    recordingRef.current = false;
    finishAfterStartRef.current = false;
    cancelIntentRef.current = false;
    setCancelIntent(false);
    const recorder = recorderRef.current;
    recorderRef.current = null;
    pointerIdRef.current = null;
    pointerStartYRef.current = null;

    if (!recorder) {
      setSessionState("idle");
      return;
    }

    try {
      const audio = await recorder.stop();
      if (shouldCancel) {
        setSessionState("idle");
        return;
      }
      setSessionState("transcribing");
      await submitAudio(audio);
    } catch (recordError) {
      setSessionState("error");
      setError(recordError instanceof Error ? recordError.message : String(recordError));
    }
  }

  function handlePointerDown(event: PointerEvent<HTMLButtonElement>) {
    if (!canStartRecording || recordingRef.current) {
      return;
    }
    pointerIdRef.current = event.pointerId;
    pointerStartYRef.current = event.clientY;
    event.currentTarget.setPointerCapture(event.pointerId);
    void startRecording();
  }

  function handlePointerMove(event: PointerEvent<HTMLButtonElement>) {
    if (!recordingRef.current || pointerIdRef.current !== event.pointerId || pointerStartYRef.current === null) {
      return;
    }
    const delta = pointerStartYRef.current - event.clientY;
    const nextCancelIntent = delta >= CANCEL_SWIPE_PX;
    cancelIntentRef.current = nextCancelIntent;
    setCancelIntent(nextCancelIntent);
  }

  function shouldCancelFromPointer(event: PointerEvent<HTMLButtonElement>) {
    if (pointerStartYRef.current === null) {
      return cancelIntentRef.current;
    }
    return cancelIntentRef.current || pointerStartYRef.current - event.clientY >= CANCEL_SWIPE_PX;
  }

  function handlePointerUp(event: PointerEvent<HTMLButtonElement>) {
    if (pointerIdRef.current !== event.pointerId) {
      return;
    }
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // Ignore if capture was already released.
    }
    void finishRecording(shouldCancelFromPointer(event));
  }

  function handlePointerCancel(event: PointerEvent<HTMLButtonElement>) {
    if (pointerIdRef.current !== event.pointerId) {
      return;
    }
    try {
      event.currentTarget.releasePointerCapture(event.pointerId);
    } catch {
      // Ignore if capture was already released.
    }
    void finishRecording(true);
  }

  const renderedMessages = useMemo(
    () =>
      messages.map((message, index) => {
        const previousUserText =
          [...messages.slice(0, index)].reverse().find((candidate) => candidate.role === "user")?.text ?? "";

        if (message.role === "assistant") {
          return <AssistantBubble key={message.id} message={message} previousUserText={previousUserText} />;
        }

        return (
          <article key={message.id} className="message-card user">
            <div className="message-label">You</div>
            <p className="message-text">{message.text}</p>
          </article>
        );
      }),
    [messages]
  );

  return (
    <main className="app-shell">
      <section className="workspace-panel">
        <header className="hero-panel">
          <div className="hero-copy">
            <div className="eyebrow">Local voice coach</div>
            <h1>Practice in a chat that feels immediate.</h1>
            <p>Speak naturally, get a direct reply first, then a native rewrite only when it helps.</p>
          </div>
          <div className="status-stack">
            <StatusPill label={sessionState} tone={sessionState === "error" ? "bad" : sessionState === "speaking" ? "good" : "neutral"} />
            <StatusPill label={status.backend_mode} tone={status.backend_mode === "real" ? "good" : "warn"} />
            <StatusPill label={status.llm_available ? "LLM online" : "LLM issue"} tone={status.llm_available ? "good" : "bad"} />
            <StatusPill label={status.asr_available ? "ASR online" : "ASR issue"} tone={status.asr_available ? "good" : "bad"} />
            <StatusPill label={streamConnected ? "stream live" : "stream retry"} tone={streamConnected ? "good" : "warn"} />
          </div>
        </header>

        <section className="conversation-panel">
          <div className="message-stream" ref={messageStreamRef}>
            {messages.length ? (
              renderedMessages
            ) : (
              <section className="empty-state-card">
                <div className="empty-orbit" />
                <div>
                  <div className="eyebrow">Ready when you are</div>
                  <h2>Hold the button and start with one real sentence.</h2>
                  <p>Try: “I want to practice job interview small talk.” The coach will remember durable preferences and recurring mistakes.</p>
                </div>
              </section>
            )}
          </div>

          <div className="composer-panel">
            <div className="composer-status-row">
              <span>{formatPathTail(settings.whisper_model_name || "")}</span>
              <span>{status.llm_resolved_provider || status.llm_provider || "local"}</span>
              <span>{status.vector_index_provider || "json"} memory</span>
            </div>
            <button
              type="button"
              className={`record-button ${sessionState === "recording" ? "recording" : ""} ${cancelIntent ? "cancel-intent" : ""}`}
              disabled={recordDisabled}
              onPointerDown={handlePointerDown}
              onPointerMove={handlePointerMove}
              onPointerUp={handlePointerUp}
              onPointerCancel={handlePointerCancel}
            >
              <span className="record-button-title">
                {sessionState === "recording" ? (cancelIntent ? "Release to Cancel" : "Release to Send") : "Hold to Speak"}
              </span>
              <span className="record-button-hint">{transcriptHint}</span>
            </button>
            <div className="transcript-panel">
              <div className="panel-label">Live Transcript</div>
              <p>{latestTranscript}</p>
            </div>
            {error ? <div className="error-banner">{error}</div> : null}
          </div>
        </section>
      </section>

      <aside className="inspector-panel">
        <div className="tab-header">
          <button type="button" className={`tab-chip ${sidebarTab === "coach" ? "active" : ""}`} onClick={() => setSidebarTab("coach")}>
            Coach
          </button>
          <button
            type="button"
            className={`tab-chip ${sidebarTab === "telemetry" ? "active" : ""}`}
            onClick={() => setSidebarTab("telemetry")}
          >
            Metrics
          </button>
          <button type="button" className={`tab-chip ${sidebarTab === "memory" ? "active" : ""}`} onClick={() => setSidebarTab("memory")}>
            Memory
          </button>
        </div>

        {sidebarTab === "coach" ? (
          <div className="tab-stack">
            <SidebarSection title="Session Summary">
              <p className="muted-text">
                Voice output reads the direct coach reply. Rewrites and tips stay visual, so practice feels like a conversation instead of a grammar exam.
              </p>
            </SidebarSection>
            <SettingsPreview
              whisperModelName={settings.whisper_model_name}
              llmProvider={settings.llm_provider}
              llmResolvedProvider={status.llm_resolved_provider}
              llmTarget={llmTarget}
              vectorProvider={status.vector_index_provider}
              streamConnected={streamConnected}
            />
          </div>
        ) : null}

        {sidebarTab === "telemetry" ? (
          <div className="tab-stack">
            <SessionStatsCard
              latestFirstAudioMs={stats.latestFirstAudioMs}
              averageFirstAudioMs={stats.averageFirstAudioMs}
              completedTurns={stats.completedTurns}
              lastCompletedAt={stats.lastCompletedAt}
            />
            <ResourceUsageCard usage={usage} />
          </div>
        ) : null}

        {sidebarTab === "memory" ? (
          <div className="tab-stack">
            <MemorySnapshotPanel
              snapshot={memorySnapshot}
              onRefresh={() => void runMemoryAction("refresh")}
              onRebuild={() => void runMemoryAction("rebuild")}
              busy={memoryActionBusy}
            />
            <SidebarSection title="Recalled Learning Memory">
              <MemoryList records={memoryRecall} emptyText="No recalled memory yet." />
            </SidebarSection>
            <SidebarSection title="Updated Learning Memory">
              <MemoryList records={memoryWritten} emptyText="No new learning memory yet." />
            </SidebarSection>
          </div>
        ) : null}
      </aside>
    </main>
  );
}
