import { useState } from "react";

import { api } from "../api";
import { ErrorState, Loading } from "../components/States";
import { formatDateTime } from "../labels";
import type { LLMStatus, LLMTask, LLMTaskStatus, NotificationPreferences } from "../types";
import { useLoad } from "../useLoad";

function NtfyCard() {
  const status = useLoad(() => api.ntfyStatus(), "ntfy");
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function sendTest() {
    setBusy(true);
    setMessage(null);
    try {
      await api.ntfyTest();
      setMessage("Test queued. The worker sends it within a few seconds.");
      status.reload();
    } catch (err) {
      setMessage(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby="ntfy-title">
      <h2 id="ntfy-title" className="card__title">
        ntfy notifications
      </h2>
      {status.loading && !status.data && <Loading />}
      {status.error && <ErrorState error={status.error} onRetry={status.reload} />}
      {status.data && (
        <>
          <dl className="meta">
            <div>
              <dt>Status</dt>
              <dd>{status.data.enabled ? `Connected to ${status.data.server}` : "Disabled"}</dd>
            </div>
            <div>
              <dt>Last accepted by ntfy</dt>
              <dd>
                {status.data.last_accepted_at ? formatDateTime(status.data.last_accepted_at) : "Never"}
              </dd>
            </div>
            <div>
              <dt>Queued / sent / failed / cancelled</dt>
              <dd>
                {status.data.counts.pending} / {status.data.counts.sent} / {status.data.counts.failed}{" "}
                / {status.data.counts.cancelled}
              </dd>
            </div>
            {status.data.last_error && (
              <div>
                <dt>Last problem</dt>
                <dd>
                  {status.data.last_error}
                  {status.data.last_error_at && (
                    <span className="muted small"> · {formatDateTime(status.data.last_error_at)}</span>
                  )}
                </dd>
              </div>
            )}
            {!status.data.click_links && status.data.enabled && (
              <div>
                <dt>Links</dt>
                <dd className="muted">Set NEXT_ROLE_PUBLIC_BASE_URL to link messages to the app</dd>
              </div>
            )}
          </dl>
          <p className="muted small">
            "Accepted" means the ntfy server took the message. It does not confirm that a device
            showed it.
          </p>
          <button
            type="button"
            className="btn btn--secondary"
            onClick={sendTest}
            disabled={busy || !status.data.enabled}
          >
            Send test notification
          </button>
          <p className="small" aria-live="polite">
            {message}
          </p>
        </>
      )}
    </section>
  );
}

const TASK_LABELS: Record<LLMTask, string> = {
  extraction: "Requirement extraction",
  resume_drafting: "Resume drafting",
  letter_drafting: "Cover letter drafting",
  semantic_check: "Claim check",
};

const PROVIDER_LABELS: Record<LLMStatus["provider"], string> = {
  none: "None",
  ollama: "Ollama",
  openai_compatible: "OpenAI-compatible",
};

function LLMTaskRow({ row, candidateData }: { row: LLMTaskStatus; candidateData: boolean }) {
  const c = row.counts_24h;
  const errors = c.provider_error + c.schema_invalid;
  return (
    <div>
      <dt>{TASK_LABELS[row.task]}</dt>
      <dd>
        {!row.available ? (
          <span className="muted">
            {candidateData
              ? "Not used"
              : "Not used: set NEXT_ROLE_LLM_ALLOW_CANDIDATE_DATA to send candidate facts"}
          </span>
        ) : (
          <>
            <span>{row.model}</span>
            <span className="muted small">
              {" "}
              · 24 h: {c.ok} ok, {errors} failed, {c.deterministic_fallback} fell back
              {c.temperature_dropped > 0 && `, ${c.temperature_dropped} temperature dropped`}
            </span>
          </>
        )}
        {row.last_error && (
          <div className="small">
            Last problem: {row.last_error.message ?? "Unknown error"}
            <span className="muted"> · {formatDateTime(row.last_error.at)}</span>
          </div>
        )}
        {row.last_success && (
          <div className="muted small">
            Last success {formatDateTime(row.last_success.at)}
            {row.last_success.latency_ms !== null && ` in ${row.last_success.latency_ms} ms`}
          </div>
        )}
      </dd>
    </div>
  );
}

function LLMCard() {
  const status = useLoad(() => api.llmStatus(), "llm");
  const data = status.data;
  return (
    <section className="card" aria-labelledby="llm-title">
      <h2 id="llm-title" className="card__title">
        Language model
      </h2>
      {status.loading && !data && <Loading />}
      {status.error && <ErrorState error={status.error} onRetry={status.reload} />}
      {data && !data.enabled && (
        <p className="muted">
          No language model is configured (NEXT_ROLE_LLM_PROVIDER=none). Requirements are extracted
          by rules and documents are assembled from your facts.
        </p>
      )}
      {data && data.enabled && (
        <>
          <dl className="meta">
            <div>
              <dt>Provider</dt>
              <dd>
                {PROVIDER_LABELS[data.provider]}
                {data.host && <span className="muted small"> · {data.host}</span>}
              </dd>
            </div>
            <div>
              <dt>Data sent</dt>
              <dd>
                {data.allow_candidate_data ? "Job text and candidate facts" : "Job text only"}
                {data.allow_external && <span className="muted small"> · external allowed</span>}
              </dd>
            </div>
            {data.tasks.map((row) => (
              <LLMTaskRow key={row.task} row={row} candidateData={data.allow_candidate_data} />
            ))}
          </dl>
          <p className="muted small">
            Counts cover the last 24 hours; events are kept for {data.retention_days} days. Nothing
            sent to or returned by the model is stored here.
          </p>
        </>
      )}
    </section>
  );
}

function PreferencesCard() {
  const prefs = useLoad(() => api.preferences(), "prefs");
  const [error, setError] = useState<string | null>(null);

  async function toggle(field: keyof Omit<NotificationPreferences, "updated_at">, value: boolean) {
    setError(null);
    try {
      await api.updatePreferences({ [field]: value });
      prefs.reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <section className="card" aria-labelledby="prefs-title">
      <h2 id="prefs-title" className="card__title">
        What to notify
      </h2>
      <p className="muted small">
        Messages never include the employer, job title, score, or your details.
      </p>
      {prefs.loading && !prefs.data && <Loading />}
      {prefs.error && <ErrorState error={prefs.error} onRetry={prefs.reload} />}
      {prefs.data && (
        <fieldset className="toggles">
          <legend className="sr-only">Notification types</legend>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={prefs.data.review_ready}
              onChange={(e) => void toggle("review_ready", e.target.checked)}
            />
            A newly assessed role is ready for review
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={prefs.data.review_postponed_due}
              onChange={(e) => void toggle("review_postponed_due", e.target.checked)}
            />
            A postponed role is due
          </label>
          <label className="checkbox">
            <input
              type="checkbox"
              checked={prefs.data.documents}
              onChange={(e) => void toggle("documents", e.target.checked)}
            />
            Documents are ready, need input, or failed
          </label>
        </fieldset>
      )}
      {error && (
        <p className="notice notice--error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

export function Settings({ onLogout }: { onLogout: () => Promise<void> }) {
  const [signingOut, setSigningOut] = useState(false);
  const [signOutError, setSignOutError] = useState<string | null>(null);

  const signOut = () => {
    setSigningOut(true);
    setSignOutError(null);
    onLogout().catch((err: unknown) => {
      setSigningOut(false);
      setSignOutError(err instanceof Error ? err.message : String(err));
    });
  };

  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
        <button type="button" className="btn btn--secondary" onClick={signOut} disabled={signingOut}>
          {signingOut ? "Signing out…" : "Sign out"}
        </button>
      </div>
      {signOutError && (
        <p className="notice notice--error" role="alert">
          Sign-out failed, so you are still signed in: {signOutError}
        </p>
      )}
      <div className="settings-grid">
        <NtfyCard />
        <LLMCard />
        <PreferencesCard />
      </div>
    </>
  );
}
