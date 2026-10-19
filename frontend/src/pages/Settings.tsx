import { useState } from "react";

import { api } from "../api";
import { ErrorState, Loading } from "../components/States";
import { formatDateTime } from "../labels";
import type { NotificationPreferences } from "../types";
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

export function Settings({ onLogout }: { onLogout: () => void }) {
  return (
    <>
      <div className="page-head">
        <h1>Settings</h1>
        <button type="button" className="btn btn--secondary" onClick={onLogout}>
          Sign out
        </button>
      </div>
      <div className="settings-grid">
        <NtfyCard />
        <PreferencesCard />
      </div>
    </>
  );
}
