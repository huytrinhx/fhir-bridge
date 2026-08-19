import { useEffect, useState } from "react";
import { adminRerunConversation, getAdminSettings, getFeedback, listFeedback, updateAdminSettings } from "../api";
import { relativeTime } from "../format";
import type { FeedbackDetail, FeedbackSummary, MessageResponse } from "../types";

interface Props {
  onBack: () => void;
}

function ModelSettings() {
  const [models, setModels] = useState<string[]>([]);
  const [intentModel, setIntentModel] = useState("");
  const [synthModel, setSynthModel] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    getAdminSettings()
      .then((settings) => {
        setModels(settings.models);
        setIntentModel(settings.intent_model);
        setSynthModel(settings.synth_model);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "failed to load model settings"))
      .finally(() => setLoading(false));
  }, []);

  async function handleSave() {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      await updateAdminSettings(intentModel, synthModel);
      setSaved(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to save model settings");
    } finally {
      setSaving(false);
    }
  }

  if (loading) return <p className="sidebar__hint">Loading model settings…</p>;

  return (
    <div className="admin__settings">
      <h2>Default models</h2>
      <p className="panel__muted">
        Used for every new conversation unless a user picks a different synthesis model themselves.
      </p>
      <div className="field-row">
        <label className="field">
          <span className="field__label">Intent gate model</span>
          <select
            className="field__select"
            value={intentModel}
            onChange={(e) => {
              setIntentModel(e.target.value);
              setSaved(false);
            }}
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>

        <label className="field">
          <span className="field__label">Synthesis model</span>
          <select
            className="field__select"
            value={synthModel}
            onChange={(e) => {
              setSynthModel(e.target.value);
              setSaved(false);
            }}
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>

        <button type="button" className="secondary-button secondary-button--accent" onClick={handleSave} disabled={saving}>
          {saving ? "Saving…" : "Save"}
        </button>
      </div>
      {saved && <p className="admin__settings-saved">Saved.</p>}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function RerunWithModel({ conversationId }: { conversationId: string }) {
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState("");
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<MessageResponse | null>(null);

  useEffect(() => {
    getAdminSettings()
      .then((settings) => {
        setModels(settings.models);
        setModel(settings.synth_model);
      })
      .catch(() => {}); // the model select just stays empty; Rerun is disabled until a model loads
  }, []);

  async function handleRerun() {
    if (!model || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    try {
      setResult(await adminRerunConversation(conversationId, model));
    } catch (err) {
      setError(err instanceof Error ? err.message : "rerun failed");
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="admin__rerun">
      <h2>Test with a different model</h2>
      <p className="panel__muted">
        Reruns this report's original conversation against the model you pick, to check whether
        the issue is model-specific. Saved as a new conversation under your own account, not the
        original reporter's.
      </p>
      <div className="field-row">
        <label className="field">
          <span className="field__label">Model</span>
          <select className="field__select" value={model} onChange={(e) => setModel(e.target.value)}>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
        <button
          type="button"
          className="secondary-button secondary-button--accent"
          onClick={handleRerun}
          disabled={running || !model}
        >
          {running ? "Running…" : "Rerun"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {result && <pre className="admin__outcome">{JSON.stringify(result, null, 2)}</pre>}
    </div>
  );
}

function QualityReports() {
  const [reports, setReports] = useState<FeedbackSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<FeedbackDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);

  useEffect(() => {
    listFeedback()
      .then(setReports)
      .catch((err) => setError(err instanceof Error ? err.message : "failed to load reports"))
      .finally(() => setLoading(false));
  }, []);

  async function selectReport(id: string) {
    setSelectedId(id);
    setDetail(null);
    setDetailLoading(true);
    setError(null);
    try {
      setDetail(await getFeedback(id));
    } catch (err) {
      setError(err instanceof Error ? err.message : "failed to load report");
    } finally {
      setDetailLoading(false);
    }
  }

  return (
    <div className="admin__body">
      <div className="admin__list">
        {loading && <p className="sidebar__hint">Loading…</p>}
        {!loading && reports.length === 0 && (
          <p className="sidebar__hint">No quality reports yet.</p>
        )}
        <ul className="admin__report-list">
          {reports.map((r) => (
            <li key={r.id}>
              <button
                type="button"
                className={`admin__report-item${r.id === selectedId ? " admin__report-item--active" : ""}`}
                onClick={() => selectReport(r.id)}
              >
                <span className="admin__report-message">{r.initial_message}</span>
                <span className="admin__report-expectation">{r.user_expectation}</span>
                <span className="sidebar__item-meta">
                  <span className="sidebar__item-model">{r.model}</span>
                  <span className="sidebar__item-time">{relativeTime(r.created_at)}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <div className="admin__detail">
        {!selectedId && (
          <p className="panel__muted">Select a report to view its full conversation log.</p>
        )}
        {detailLoading && <p className="panel__muted">Loading…</p>}
        {detail && (
          <>
            <h2>What they expected</h2>
            <p className="admin__expectation-text">{detail.user_expectation}</p>

            <h2>Conversation log</h2>
            <div className="admin__transcript">
              {detail.transcript_snapshot.map((entry, i) => (
                <div key={i} className={`bubble bubble--${entry.role}`}>
                  <span className="bubble__text">{entry.text}</span>
                </div>
              ))}
            </div>

            {detail.outcome_snapshot && (
              <>
                <h2>Final outcome</h2>
                <pre className="admin__outcome">{JSON.stringify(detail.outcome_snapshot, null, 2)}</pre>
              </>
            )}

            <RerunWithModel key={detail.conversation_id} conversationId={detail.conversation_id} />
          </>
        )}
      </div>

      {error && <p className="error">{error}</p>}
    </div>
  );
}

export default function AdminFeedback({ onBack }: Props) {
  const [tab, setTab] = useState<"models" | "quality">("models");

  return (
    <div className="admin">
      <header className="admin__header">
        <h1>Admin</h1>
        <button type="button" className="secondary-button" onClick={onBack}>
          ← Back
        </button>
      </header>

      <div className="admin__tabs">
        <button
          type="button"
          className={`admin__tab${tab === "models" ? " admin__tab--active" : ""}`}
          onClick={() => setTab("models")}
        >
          Models
        </button>
        <button
          type="button"
          className={`admin__tab${tab === "quality" ? " admin__tab--active" : ""}`}
          onClick={() => setTab("quality")}
        >
          Quality
        </button>
      </div>

      {tab === "models" ? <ModelSettings /> : <QualityReports />}
    </div>
  );
}
