import { useEffect, useState } from "react";
import { getFeedback, listFeedback } from "../api";
import { relativeTime } from "../format";
import type { FeedbackDetail, FeedbackSummary } from "../types";

interface Props {
  onBack: () => void;
}

export default function AdminFeedback({ onBack }: Props) {
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
    <div className="admin">
      <header className="admin__header">
        <h1>Quality Reports</h1>
        <button type="button" className="secondary-button" onClick={onBack}>
          ← Back
        </button>
      </header>

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
            </>
          )}
        </div>
      </div>

      {error && <p className="error">{error}</p>}
    </div>
  );
}
