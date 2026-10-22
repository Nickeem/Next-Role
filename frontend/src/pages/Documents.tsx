import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, api } from "../api";
import { Icon } from "../components/Icons";
import { ErrorState, Loading } from "../components/States";
import { Checks } from "../documents/Checks";
import { Editor, cleaned } from "../documents/Editor";
import { Files } from "../documents/Files";
import { History } from "../documents/History";
import { BUNDLE_LABEL, KIND_LABEL, RUN_LABEL, claimTexts, isBusy, newKey, runError } from "../documents/labels";
import { Questions } from "../documents/Questions";
import { StylePanel } from "../documents/StylePanel";
import type { Content, DocumentKind, FactRef, Preview, RevisionSummary, StyleSettings } from "../documents/types";
import { DocumentView } from "../templates/DocumentView";
import { useLoad } from "../useLoad";

const POLL_MS = 3000;

export function Documents() {
  const { id = "" } = useParams();
  const bundle = useLoad(() => api.bundle(id), `bundle:${id}`);
  const templates = useLoad(() => api.templates(), "templates");
  const candidateId = bundle.data?.candidate_id ?? "";
  const facts = useLoad(
    () => (candidateId ? api.candidateFacts(candidateId) : Promise.resolve({ facts: [] as FactRef[] })),
    `facts:${candidateId}:${bundle.data?.version ?? 0}`,
  );
  const revisions = useLoad(() => api.revisions(id), `revisions:${id}:${bundle.data?.version ?? 0}`);

  const [kind, setKind] = useState<DocumentKind>("resume");
  const [draft, setDraft] = useState<Content | null>(null);
  const [style, setStyle] = useState<StyleSettings | null>(null);
  const [viewing, setViewing] = useState<RevisionSummary | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [working, setWorking] = useState(false);
  const [message, setMessage] = useState<{ tone: "error" | "info"; text: string } | null>(null);

  const data = bundle.data;
  const reload = bundle.reload;

  // Keep polling while drafting, checks, or rendering are in progress.
  const busy = data ? isBusy(data) : false;
  useEffect(() => {
    if (!busy) return;
    const timer = setTimeout(reload, POLL_MS);
    return () => clearTimeout(timer);
  }, [busy, data, reload]);

  // The design controls follow the saved style until the operator changes them.
  const savedStyle = data?.style?.settings ?? null;
  const savedStyleKey = JSON.stringify(savedStyle);
  useEffect(() => {
    setStyle(savedStyle);
    // savedStyleKey captures the value; the object identity changes on every load.
  }, [savedStyleKey]);

  const revision = data ? data[kind] : null;
  const styleChanged = style !== null && JSON.stringify(style) !== savedStyleKey;

  // Lay out unsaved edits, an unapplied design, or an older version through the server, so the
  // preview matches the files exactly.
  const previewKey = useRef(0);
  useEffect(() => {
    if (!data || (!draft && !styleChanged && !viewing)) {
      setPreview(null);
      return;
    }
    const key = ++previewKey.current;
    const timer = setTimeout(async () => {
      try {
        const content = draft ?? (viewing ? (await api.revision(viewing.id)).content : undefined);
        const result = await api.preview(id, {
          kind: viewing?.kind ?? kind,
          content: content ? cleaned(content) : undefined,
          settings: style ?? undefined,
        });
        if (key === previewKey.current) setPreview(result);
      } catch {
        // An invalid draft simply keeps the last good preview; saving reports the problem.
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [data, draft, style, styleChanged, viewing, kind, id]);

  const act = useCallback(
    async (run: () => Promise<unknown>, done?: string): Promise<boolean> => {
      setWorking(true);
      setMessage(null);
      try {
        await run();
        if (done) setMessage({ tone: "info", text: done });
        reload();
        return true;
      } catch (err) {
        const text = err instanceof Error ? err.message : String(err);
        if (err instanceof ApiError && err.status === 409) {
          setMessage({ tone: "error", text: `${text} The page has been refreshed.` });
          reload();
        } else {
          setMessage({ tone: "error", text });
        }
        return false;
      } finally {
        setWorking(false);
      }
    },
    [reload],
  );

  const factMap = useMemo(
    () => new Map((facts.data?.facts ?? []).map((f) => [f.id, f])),
    [facts.data],
  );

  if (bundle.loading && !data) return <Loading label="Loading documents" />;
  if (bundle.error && !data) return <ErrorState error={bundle.error} onRetry={reload} />;
  if (!data) return null;

  const withdrawn = data.status === "withdrawn";
  const active = data.runs.filter((r) => r.status === "queued" || r.status === "running");
  const failedRun = data.runs.find((r) => r.status === "failed");
  const tokens = preview?.tokens ?? data.style?.tokens;
  const shown = preview?.display ?? data.preview[viewing?.kind ?? kind];
  const disabled = working || withdrawn;

  const switchKind = (next: DocumentKind) => {
    if (draft && !window.confirm("Discard your unsaved edits?")) return;
    setDraft(null);
    setViewing(null);
    setKind(next);
  };

  return (
    <>
      <Link to={`/reviews/${data.review_id}`} className="back">
        <Icon name="back" size={16} /> Review
      </Link>
      <div className="page-head">
        <div>
          <h1>Documents: {data.job.title ?? "Untitled role"}</h1>
          <p className="muted">{data.job.company ?? "Company not stated"}</p>
        </div>
        <span className={`pill pill--doc-${data.status}`}>
          <span className="pill__dot" aria-hidden="true" />
          {BUNDLE_LABEL[data.status]}
        </span>
      </div>

      {withdrawn && (
        <p className="notice notice--warn">
          The approval to draft for this role was revoked. These documents are kept for your
          records and can no longer be changed or approved.
        </p>
      )}
      {data.drafting_notes.length > 0 && (
        <p className="notice">
          {data.drafting_notes.join(" · ")}
        </p>
      )}
      {active.length > 0 && (
        <p className="notice" role="status" aria-live="polite">
          <span className="spinner" aria-hidden="true" /> {RUN_LABEL[active[0]?.intent ?? "verify"]}…
        </p>
      )}
      {failedRun && active.length === 0 && failedRun.last_error && failedRun.last_error !== "approval_revoked" && (
        <p className="notice notice--warn">{runError(failedRun.last_error)}</p>
      )}
      {message && (
        <p className={`notice ${message.tone === "error" ? "notice--error" : ""}`} role={message.tone === "error" ? "alert" : "status"}>
          {message.text}
        </p>
      )}

      <div className="segmented doc-tabs" role="tablist" aria-label="Document">
        {(["resume", "cover_letter"] as const).map((k) => (
          <button
            key={k}
            type="button"
            role="tab"
            aria-selected={kind === k}
            className={`segmented__item${kind === k ? " segmented__item--active" : ""}`}
            onClick={() => switchKind(k)}
          >
            {KIND_LABEL[k]}
          </button>
        ))}
      </div>

      <div className="detail-grid doc-grid">
        <div className="detail-grid__main">
          <section className="card" aria-labelledby="preview-title">
            <div className="card__head">
              <h2 id="preview-title" className="card__title">
                {draft ? "Editing" : viewing ? `Version ${viewing.seq} (not current)` : "Preview"}
              </h2>
              {revision && !viewing && (
                <div className="question__actions">
                  {draft ? (
                    <>
                      <button
                        type="button"
                        className="btn btn--primary btn--small"
                        disabled={disabled}
                        onClick={async () => {
                          const ok = await act(
                            () => api.saveEdit(id, { kind, content: cleaned(draft), expected_version: data.version }),
                            "Saved. Your edits are being checked.",
                          );
                          if (ok) setDraft(null);
                        }}
                      >
                        Save edits
                      </button>
                      <button type="button" className="btn btn--secondary btn--small" onClick={() => setDraft(null)}>
                        Cancel
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn btn--secondary btn--small"
                      disabled={disabled}
                      onClick={() => setDraft(structuredClone(revision.content))}
                    >
                      Edit {KIND_LABEL[kind].toLowerCase()}
                    </button>
                  )}
                </div>
              )}
            </div>
            {draft && revision && (
              <Editor
                content={draft}
                checks={revision.verification?.claims ?? []}
                saved={claimTexts(revision.content)}
                onChange={setDraft}
                busy={disabled}
                onRedraft={(section) => {
                  if (!window.confirm("Redraft this section? Unsaved edits are discarded.")) return;
                  setDraft(null);
                  void act(
                    () =>
                      api.regenerate(id, {
                        kind,
                        section,
                        expected_version: data.version,
                        idempotency_key: newKey(),
                      }),
                    "Redrafting. The new version will appear when it is ready.",
                  );
                }}
              />
            )}
            {shown && tokens ? (
              <div className="doc-preview" aria-label={`${KIND_LABEL[viewing?.kind ?? kind]} preview`}>
                <DocumentView document={shown} tokens={tokens} />
              </div>
            ) : (
              <p className="muted">Not drafted yet.</p>
            )}
          </section>
        </div>
        <div className="detail-grid__side">
          <Files
            bundle={data}
            busy={disabled}
            onRetry={(a) => void act(() => api.retryArtifact(a.id), "The file will be rendered again.")}
            onApprove={() =>
              act(() => {
                if (!data.resume || !data.cover_letter || !data.style) {
                  return Promise.reject(new Error("Nothing to approve yet."));
                }
                return api.approveDocuments(id, {
                  expected_version: data.version,
                  resume_revision_id: data.resume.id,
                  cover_letter_revision_id: data.cover_letter.id,
                  style_id: data.style.id,
                  artifacts: Object.fromEntries(data.artifacts.map((a) => [a.id, a.sha256 ?? ""])),
                });
              }, "Approved.")
            }
          />
          {revision && (
            <Checks
              kind={kind}
              revision={revision}
              facts={factMap}
              busy={disabled}
              onConfirmClaim={(claimId) =>
                void act(
                  () => api.confirmClaim(id, claimId, { kind, expected_version: data.version }),
                  "Added to your profile as a confirmed fact. Checking again…",
                )
              }
              onConfirmFact={(factId) =>
                void act(async () => {
                  await api.confirmFact(data.candidate_id, factId);
                  await api.recheck(id);
                }, "Fact confirmed. Checking again…")
              }
            />
          )}
          <Questions
            questions={data.clarifications}
            busy={disabled}
            onAnswer={(q, answer) =>
              act(() => api.answer(q.id, { answer, expected_version: data.version }), "Answer saved.")
            }
            onDismiss={(q, reason) =>
              act(
                () => api.dismiss(q.id, { reason: reason || undefined, expected_version: data.version }),
                "Question dismissed.",
              )
            }
          />
          {templates.data && style && savedStyle && (
            <StylePanel
              templates={templates.data}
              value={style}
              saved={savedStyle}
              busy={disabled}
              onChange={setStyle}
              onApply={() =>
                void act(
                  () => api.setStyle(id, { settings: style, expected_version: data.version }),
                  "Design applied. New files are being rendered.",
                )
              }
            />
          )}
          {revisions.data && (
            <History
              revisions={revisions.data}
              currentIds={new Set([data.resume?.id, data.cover_letter?.id].filter((x): x is string => !!x))}
              viewing={viewing?.id ?? null}
              onView={(r) => {
                setDraft(null);
                setViewing(r);
                if (r) setKind(r.kind);
              }}
            />
          )}
        </div>
      </div>
    </>
  );
}
