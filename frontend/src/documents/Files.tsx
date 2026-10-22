import { useEffect, useId, useRef, useState } from "react";

import { downloadUrl } from "../api";
import { ARTIFACT_LABEL, KIND_LABEL, approvalBlockers } from "./labels";
import type { Artifact, Bundle, DocumentKind } from "./types";

function kindOf(bundle: Bundle, artifact: Artifact): DocumentKind {
  return bundle.resume?.id === artifact.revision_id ? "resume" : "cover_letter";
}

function size(bytes: number | null): string {
  if (!bytes) return "";
  return bytes > 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}

function ApproveDialog({ onConfirm, onClose }: { onConfirm: () => Promise<boolean>; onClose: () => void }) {
  const titleId = useId();
  const confirmRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const [submitting, setSubmitting] = useState(false);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    confirmRef.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      if (e.key !== "Tab" || !dialogRef.current) return;
      // Keep keyboard focus inside the modal.
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>("button:not([disabled])");
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) return;
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={dialogRef} className="dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <h2 id={titleId} className="dialog__title">
          Approve these documents?
        </h2>
        <p className="muted">
          You approve exactly these versions and files. Any later edit or design change needs a
          new approval. Approving does not apply to the job or send anything to the employer.
        </p>
        <div className="dialog__actions">
          <button type="button" className="btn btn--secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            ref={confirmRef}
            type="button"
            className="btn btn--primary"
            disabled={submitting}
            onClick={async () => {
              setSubmitting(true);
              if (!(await onConfirm())) setSubmitting(false);
            }}
          >
            {submitting ? "Approving…" : "Approve documents"}
          </button>
        </div>
      </div>
    </div>
  );
}

/** The four files, their state, downloads, and approval of the exact set. */
export function Files({
  bundle,
  busy,
  onRetry,
  onApprove,
}: {
  bundle: Bundle;
  busy: boolean;
  onRetry: (artifact: Artifact) => void;
  onApprove: () => Promise<boolean>;
}) {
  const [confirming, setConfirming] = useState(false);
  const blockers = approvalBlockers(bundle);
  const approved = bundle.status === "approved";
  const rows = [...bundle.artifacts].sort((a, b) =>
    `${kindOf(bundle, a)}${a.format}`.localeCompare(`${kindOf(bundle, b)}${b.format}`),
  );
  return (
    <section className="card" aria-labelledby="files-title" id="approve">
      <h2 id="files-title" className="card__title">
        Files and approval
      </h2>
      {rows.length === 0 ? (
        <p className="muted">No files yet.</p>
      ) : (
        <ul className="files">
          {rows.map((a) => {
            const kind = kindOf(bundle, a);
            return (
              <li key={a.id} className="file">
                <span>
                  <strong>
                    {KIND_LABEL[kind]} · {a.format.toUpperCase()}
                  </strong>
                  <span className={`file__status file__status--${a.status}`}> {ARTIFACT_LABEL[a.status]}</span>
                  {a.page_count ? <span className="muted small"> · {a.page_count} page{a.page_count === 1 ? "" : "s"}</span> : null}
                  {a.bytes ? <span className="muted small"> · {size(a.bytes)}</span> : null}
                  {a.qa?.warnings.map((w) => (
                    <span key={w} className="small file__warning">
                      {" "}
                      · {w}
                    </span>
                  ))}
                  {a.status === "failed" && a.last_error && (
                    <span className="small file__warning"> · {a.last_error === "qa_failed" ? "the file failed its checks" : a.last_error}</span>
                  )}
                </span>
                {a.status === "ready" && (
                  <a className="btn btn--secondary btn--small" href={downloadUrl(a.id)} download>
                    Download<span className="sr-only"> {KIND_LABEL[kind]} {a.format.toUpperCase()}</span>
                    {!approved && " draft"}
                  </a>
                )}
                {a.status === "failed" && (
                  <button type="button" className="btn btn--secondary btn--small" disabled={busy} onClick={() => onRetry(a)}>
                    Try again
                  </button>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {approved ? (
        <p className="notice">
          Approved. These exact files are the approved versions. Approval does not apply to the job.
        </p>
      ) : (
        <>
          {bundle.approval && !bundle.approval_current && (
            <p className="notice notice--warn">
              An earlier version was approved. The documents changed since, so they need a new approval.
            </p>
          )}
          {blockers.length > 0 && (
            <ul className="blockers" aria-label="Before you can approve">
              {blockers.map((b) => (
                <li key={b.text} className="small">
                  {b.text}
                </li>
              ))}
            </ul>
          )}
          <button
            type="button"
            className="btn btn--primary btn--block"
            disabled={blockers.length > 0 || busy}
            onClick={() => setConfirming(true)}
          >
            Approve documents
          </button>
        </>
      )}
      {confirming && (
        <ApproveDialog
          onClose={() => setConfirming(false)}
          onConfirm={async () => {
            const ok = await onApprove();
            if (ok) setConfirming(false);
            return ok;
          }}
        />
      )}
    </section>
  );
}
