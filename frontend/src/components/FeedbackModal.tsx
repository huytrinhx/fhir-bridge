import { useState, type FormEvent } from "react";

interface Props {
  onSubmit: (expectation: string) => Promise<void>;
  onClose: () => void;
}

export default function FeedbackModal({ onSubmit, onClose }: Props) {
  const [expectation, setExpectation] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    if (!expectation.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await onSubmit(expectation.trim());
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "something went wrong");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        {done ? (
          <>
            <h2>Thanks — logged</h2>
            <p className="modal__muted">
              The full conversation log and your expectation were saved for review.
            </p>
            <button type="button" className="modal__close" onClick={onClose}>
              Close
            </button>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <h2>Report a quality issue</h2>
            <p className="modal__muted">
              The complete conversation log (searches, questions, answers, and the final answer)
              is attached automatically. What did you expect instead?
            </p>
            <textarea
              className="field__textarea"
              rows={4}
              autoFocus
              value={expectation}
              onChange={(e) => setExpectation(e.target.value)}
              placeholder="e.g. I expected ServiceRequest to be included as potentially-needed…"
            />
            {error && <p className="error">{error}</p>}
            <div className="modal__actions">
              <button type="button" className="modal__cancel" onClick={onClose}>
                Cancel
              </button>
              <button type="submit" className="modal__submit" disabled={submitting || !expectation.trim()}>
                {submitting ? "Submitting…" : "Submit"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
