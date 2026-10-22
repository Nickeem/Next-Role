import { formatDateTime } from "../labels";
import { KIND_LABEL } from "./labels";
import type { RevisionSummary } from "./types";

const AUTHOR: Record<RevisionSummary["created_by"], string> = {
  system: "assembled from your facts",
  llm: "drafted with the language model",
  operator: "edited by you",
};

export function History({
  revisions,
  currentIds,
  viewing,
  onView,
}: {
  revisions: RevisionSummary[];
  currentIds: Set<string>;
  viewing: string | null;
  onView: (revision: RevisionSummary | null) => void;
}) {
  return (
    <section className="card" aria-labelledby="doc-history-title">
      <h2 id="doc-history-title" className="card__title">
        Versions
      </h2>
      <ol className="timeline">
        {revisions.map((r) => (
          <li key={r.id}>
            <span className="timeline__what">
              {KIND_LABEL[r.kind]} v{r.seq}
              {currentIds.has(r.id) ? " (current)" : ""}
            </span>
            <span className="muted small">
              {formatDateTime(r.created_at)} · {AUTHOR[r.created_by]}
              {r.note ? ` · ${r.note}` : ""}
              {r.verification_status === "passed" ? " · all supported" : ""}
            </span>
            {!currentIds.has(r.id) && (
              <button
                type="button"
                className="btn btn--secondary btn--small"
                aria-pressed={viewing === r.id}
                onClick={() => onView(viewing === r.id ? null : r)}
              >
                {viewing === r.id ? "Back to current" : "View"}
              </button>
            )}
          </li>
        ))}
      </ol>
    </section>
  );
}
