import { useState } from "react";
import AuthPanel from "./AuthPanel";
import type { AuthUser } from "../types";

interface Props {
  onGuest: () => void;
  onAuthenticated: (token: string, user: AuthUser) => void;
}

export default function LandingPage({ onGuest, onAuthenticated }: Props) {
  const [showAuth, setShowAuth] = useState(false);

  return (
    <div className="landing">
      <div className="landing__intro">
        <p className="landing__eyebrow">
          <span className="wordmark-mark" aria-hidden="true" />
          FHIR R4 · grounded resource recommendations
        </p>
        <h1>Describe the data source. Get the resources it maps to.</h1>
        <p className="landing__lede">
          Every FHIR R4 resource FHIR Bud recommends is checked against the real spec before it
          reaches you — nothing is guessed, and nothing is shown without a citation back to the
          source text.
        </p>
        <ul className="landing__clauses">
          <li>
            <span className="panel__clause">§1</span> No invented resource types
          </li>
          <li>
            <span className="panel__clause">§2</span> No invented terminology codes
          </li>
          <li>
            <span className="panel__clause">§3</span> Every recommendation cited
          </li>
        </ul>
      </div>

      {!showAuth ? (
        <div className="landing__options">
          <div className="landing-card">
            <h2>Continue as guest</h2>
            <p className="landing-card__body">
              No account, nothing saved. Your conversation lives only in memory for this
              session — it's cleared the moment you close the tab, or after 20 minutes idle. If
              something breaks mid-run, there's no crash recovery, no conversation history to come
              back to, and your session isn't included in the production-study dataset.
            </p>
            <button type="button" className="landing-card__button" onClick={onGuest}>
              Continue as guest
            </button>
          </div>

          <div className="landing-card landing-card--accent">
            <h2>Sign in</h2>
            <p className="landing-card__body">
              Your conversations and quality reports are saved to your account, so you can come
              back to them later, recover from a crash mid-run, and contribute to the
              production-study dataset. Real patient identifiers in your own text are
              automatically redacted before anything is stored or sent to the model.
            </p>
            <button
              type="button"
              className="landing-card__button landing-card__button--accent"
              onClick={() => setShowAuth(true)}
            >
              Sign in or create account
            </button>
          </div>
        </div>
      ) : (
        <AuthPanel onAuthenticated={onAuthenticated} onBack={() => setShowAuth(false)} />
      )}
    </div>
  );
}
