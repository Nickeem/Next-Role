import { useEffect } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { BUNDLE_LABEL, RUN_LABEL, runError } from "../documents/labels";
import type { ReviewStatus } from "../types";
import { useLoad } from "../useLoad";
import { ErrorState } from "./States";

/** Documents drafted for this review, or the progress of drafting them. */
export function DocumentsCard({ reviewId, status, version }: { reviewId: string; status: ReviewStatus; version: number }) {
  const docs = useLoad(() => api.reviewDocuments(reviewId), `docs:${reviewId}:${version}`);
  const data = docs.data;
  const active = data?.runs.find((r) => r.status === "queued" || r.status === "running");
  const reload = docs.reload;
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(reload, 3000);
    return () => clearTimeout(timer);
  }, [active, data, reload]);

  if (!data && (!docs.error || status !== "approved_for_drafting")) return null;
  const bundles = data?.bundles ?? [];
  const failed = data?.runs.find((r) => r.status === "failed" && r.intent === "initial");
  if (bundles.length === 0 && !active && !failed && status !== "approved_for_drafting") return null;

  return (
    <section className="card" aria-labelledby="documents-title">
      <h2 id="documents-title" className="card__title">
        Documents
      </h2>
      {docs.error && !data && <ErrorState error={docs.error} onRetry={reload} />}
      {active && (
        <p className="small" role="status">
          <span className="spinner" aria-hidden="true" /> {RUN_LABEL[active.intent]}…
        </p>
      )}
      {!active && failed && bundles.length === 0 && <p className="notice notice--warn small">{runError(failed.last_error)}</p>}
      {bundles.length > 0 ? (
        <ul className="mini__list">
          {bundles.map((b, i) => (
            <li key={b.id}>
              <Link to={`/documents/${b.id}`}>{i === 0 ? "Open resume and cover letter" : `Earlier draft (${new Date(b.created_at).toLocaleDateString()})`}</Link>{" "}
              <span className={`pill pill--doc-${b.status}`}>
                <span className="pill__dot" aria-hidden="true" />
                {BUNDLE_LABEL[b.status]}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        !active && !failed && <p className="muted small">Drafting starts shortly.</p>
      )}
    </section>
  );
}
