import { relativeTime } from "../format";
import type { ConversationSummary } from "../types";

interface Props {
  conversations: ConversationSummary[];
  loading: boolean;
  activeId: string | null;
  collapsed: boolean;
  guestMode: boolean;
  search: string;
  searchError: string | null;
  accountName: string;
  accountInitial: string;
  onSelect: (id: string) => void;
  onRerun: (id: string) => void;
  onNew: () => void;
  onToggleCollapsed: () => void;
  onSearchChange: (value: string) => void;
  onLogout: () => void;
}

const OUTCOME_LABEL: Record<string, string> = {
  final_recommendation: "Answered",
  out_of_scope: "Out of scope",
  clarifying_question: "In progress",
};


export default function HistorySidebar({
  conversations,
  loading,
  activeId,
  collapsed,
  guestMode,
  search,
  searchError,
  accountName,
  accountInitial,
  onSelect,
  onRerun,
  onNew,
  onToggleCollapsed,
  onSearchChange,
  onLogout,
}: Props) {
  if (collapsed) {
    return (
      <div className="sidebar sidebar--collapsed">
        <button
          type="button"
          className="sidebar__toggle"
          onClick={onToggleCollapsed}
          title="Show history"
        >
          ›
        </button>
        <button
          type="button"
          className="sidebar__account-collapsed"
          onClick={onLogout}
          title={guestMode ? "Sign in" : `${accountName} — Log out`}
        >
          <span className={`account-badge__avatar${guestMode ? " account-badge__avatar--guest" : ""}`}>
            {accountInitial}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="sidebar">
      <div className="sidebar__header">
        <h2>History</h2>
        <div className="sidebar__header-actions">
          <button type="button" className="sidebar__new" onClick={onNew}>
            + New
          </button>
          <button
            type="button"
            className="sidebar__toggle"
            onClick={onToggleCollapsed}
            title="Hide history"
          >
            ‹
          </button>
        </div>
      </div>

      {guestMode ? (
        <p className="sidebar__hint">
          Guest mode — this session isn't saved anywhere. If something breaks mid-run there's no
          crash recovery, nothing is kept in your history, and it's not included in the
          production-study dataset. Sign in to change that.
        </p>
      ) : (
        <>
          <div className="sidebar__search">
            <input
              type="text"
              className="sidebar__search-input"
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
              placeholder="Search all (regex)…"
              title="Only the 10 most recent conversations load by default -- search matches against all of them by regex"
            />
          </div>
          {searchError && <p className="sidebar__search-error">{searchError}</p>}

          {loading && conversations.length === 0 && <p className="sidebar__hint">Loading…</p>}
          {!loading && conversations.length === 0 && (
            <p className="sidebar__hint">
              {search ? "No conversations match that pattern." : "Past conversations will show up here."}
            </p>
          )}
        </>
      )}

      <ul className="sidebar__list">
        {conversations.map((c) => (
          <li
            key={c.id}
            className={`sidebar__item${c.id === activeId ? " sidebar__item--active" : ""}`}
          >
            <button type="button" className="sidebar__item-main" onClick={() => onSelect(c.id)}>
              <span className="sidebar__item-message">{c.initial_message || "(untitled)"}</span>
              <span className="sidebar__item-meta">
                <span className={`badge badge--${c.outcome_kind}`}>
                  {OUTCOME_LABEL[c.outcome_kind] ?? c.outcome_kind}
                </span>
                <span className="sidebar__item-model">{c.model}</span>
                <span className="sidebar__item-time">{relativeTime(c.created_at)}</span>
              </span>
            </button>
            <button
              type="button"
              className="sidebar__item-rerun"
              title="Rerun this question"
              onClick={() => onRerun(c.id)}
            >
              ↻
            </button>
          </li>
        ))}
      </ul>

      <div className="sidebar__account">
        <span className="account-badge">
          <span className={`account-badge__avatar${guestMode ? " account-badge__avatar--guest" : ""}`}>
            {accountInitial}
          </span>
          <span className="account-badge__name">{accountName}</span>
        </span>
        <button
          type="button"
          className={guestMode ? "secondary-button secondary-button--accent" : "secondary-button"}
          onClick={onLogout}
        >
          {guestMode ? "Sign in" : "Log out"}
        </button>
      </div>
    </div>
  );
}
