import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { useCandidate } from "../candidates";
import { DecisionPanel } from "../components/DecisionPanel";
import { DocumentsCard } from "../components/DocumentsCard";
import { Icon } from "../components/Icons";
import { TierBadge } from "../components/Pills";
import { ErrorState, Loading } from "../components/States";
import { EVENT_LABEL, formatDate, formatDateTime, MATCH_LABEL, STATUS_LABEL } from "../labels";
import type { Assessment, Job, MatchStatus, RequirementResult, ReviewEvent } from "../types";
import { useLoad } from "../useLoad";

const GROUPS: { title: string; statuses: MatchStatus[]; hint: string }[] = [
  { title: "Matched", statuses: ["met", "partially_met"], hint: "Backed by supported evidence" },
  { title: "Gaps", statuses: ["not_met"], hint: "Contradicted by supported information" },
  { title: "Unverified", statuses: ["unverified"], hint: "Only self-reported claims address these" },
  { title: "Unknown", statuses: ["no_evidence", "unknown"], hint: "No relevant evidence, or not interpretable" },
];

function safeHttpUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : null;
  } catch {
    return null;
  }
}

function Requirement({ r }: { r: RequirementResult }) {
  const showEvidence = r.status !== "no_evidence" && r.status !== "unknown";
  return (
    <li className={`req req--${r.status}`}>
      <div className="req__head">
        <span className="req__text">{r.text}</span>
        <span className={`match match--${r.status}`}>{MATCH_LABEL[r.status]}</span>
      </div>
      <p className="muted small">
        {r.importance} · {r.category.replaceAll("_", " ")} · {r.explanation}
      </p>
      {showEvidence &&
        (r.evidence.length > 0 ? (
          <ul className="evidence" aria-label={`Evidence for: ${r.text}`}>
            {r.evidence.map((e, i) => (
              <li key={`${e.fact_id ?? "none"}-${i}`}>
                <span className="evidence__title">{e.title}</span>{" "}
                <span className={`support support--${e.support}`}>{e.support}</span>
                <span className="muted small"> · {e.fact_type} · {e.reason}</span>
                {/* Preference-based evidence (e.g. declared work authorization) has no fact. */}
                {!e.fact_id && e.fact_type !== "preferences" && (
                  <span className="muted small"> · source fact unavailable</span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="muted small evidence--none">Evidence unavailable</p>
        ))}
    </li>
  );
}

function AssessmentCard({ assessment }: { assessment: Assessment }) {
  return (
    <section className="card" aria-labelledby="assessment-title">
      <div className="card__head">
        <h2 id="assessment-title" className="card__title">
          Assessment
        </h2>
        <span className="muted small">
          {assessment.method_version} · as of {formatDate(assessment.as_of)} · stored{" "}
          {formatDateTime(assessment.created_at)}
        </span>
      </div>
      <div className="scoreline">
        <div className="scoreline__score">
          <span className="score__value score__value--large">{assessment.score ?? "—"}</span>
          <span className="muted small">score / 100</span>
        </div>
        <dl className="scoreline__facts">
          <div>
            <dt>Suitability</dt>
            <dd>
              <TierBadge tier={assessment.tier} />
            </dd>
          </div>
          <div>
            <dt>Coverage</dt>
            <dd>{Math.round(assessment.coverage * 100)}% of weighted requirements assessable</dd>
          </div>
          <div>
            <dt>Excluding unknowns</dt>
            <dd>{assessment.score_excluding_unknowns ?? "—"}</dd>
          </div>
          {assessment.has_blocking_gaps && (
            <div>
              <dt>Blocking gaps</dt>
              <dd className="flag">Yes: a required item is not met</dd>
            </div>
          )}
        </dl>
      </div>
      {GROUPS.map((g) => {
        const items = assessment.requirements.filter((r) => g.statuses.includes(r.status));
        if (items.length === 0) return null;
        return (
          <div key={g.title} className="req-group">
            <h3>
              {g.title} <span className="muted small">({items.length}) · {g.hint}</span>
            </h3>
            <ul className="reqs">
              {items.map((r) => (
                <Requirement key={r.requirement_id} r={r} />
              ))}
            </ul>
          </div>
        );
      })}
      {assessment.requirements.length === 0 && (
        <p className="muted">No requirements were extracted from this posting.</p>
      )}
      {assessment.preference_checks.length > 0 && (
        <div className="req-group">
          <h3>Your preferences</h3>
          <ul className="prefs">
            {assessment.preference_checks.map((c) => (
              <li key={c.name}>
                <span className={`match match--pref-${c.status}`}>{c.status.replaceAll("_", " ")}</span>{" "}
                <span className="small">{c.explanation}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      <p className="muted small disclaimer">{assessment.disclaimer}</p>
    </section>
  );
}

function JobCard({ job }: { job: Job }) {
  const source = safeHttpUrl(job.source_url);
  const salary =
    job.salary_min || job.salary_max
      ? `${[job.salary_min, job.salary_max].filter(Boolean).join("–")} ${job.salary_currency ?? ""}`
      : "Not stated";
  return (
    <section className="card" aria-labelledby="job-title">
      <h2 id="job-title" className="card__title">
        Job posting
      </h2>
      <dl className="meta">
        <div>
          <dt>Location</dt>
          <dd>{job.location ?? "Not stated"}</dd>
        </div>
        <div>
          <dt>Arrangement</dt>
          <dd>{job.work_arrangement}</dd>
        </div>
        <div>
          <dt>Employment</dt>
          <dd>{job.employment_type.replaceAll("_", " ")}</dd>
        </div>
        <div>
          <dt>Salary</dt>
          <dd>{salary}</dd>
        </div>
        <div>
          <dt>Source</dt>
          <dd>
            {source ? (
              <a href={source} target="_blank" rel="noopener noreferrer nofollow">
                {job.source} <Icon name="external" size={14} />
                <span className="sr-only"> (opens in a new tab)</span>
              </a>
            ) : (
              job.source
            )}
          </dd>
        </div>
      </dl>
      <details className="description">
        <summary>Original description</summary>
        {/* Rendered as text, never as HTML: postings are untrusted. */}
        <pre>{job.description}</pre>
      </details>
    </section>
  );
}

function History({ events }: { events: ReviewEvent[] }) {
  return (
    <section className="card" aria-labelledby="history-title">
      <h2 id="history-title" className="card__title">
        History
      </h2>
      <ol className="timeline">
        {events.map((e) => (
          <li key={e.id}>
            <span className="timeline__what">
              {EVENT_LABEL[e.event_type]}
              {e.previous_status !== e.new_status && ` → ${STATUS_LABEL[e.new_status]}`}
            </span>
            <span className="muted small">
              {formatDateTime(e.created_at)} · {e.actor === "operator" ? "you" : "system"}
            </span>
            {e.postpone_until && (
              <span className="small">Until {formatDateTime(e.postpone_until)}</span>
            )}
            {e.reason && <q className="small">{e.reason}</q>}
          </li>
        ))}
      </ol>
    </section>
  );
}

export function ReviewDetail() {
  const { id = "" } = useParams();
  const detail = useLoad(() => api.review(id), `detail:${id}`);
  const events = useLoad(() => api.events(id), `events:${id}:${detail.data?.review.version ?? 0}`);
  const { candidates } = useCandidate();

  if (detail.loading && !detail.data) return <Loading label="Loading review" />;
  if (detail.error && !detail.data) {
    return (
      <>
        <Link to="/" className="back">
          <Icon name="back" size={16} /> Review queue
        </Link>
        <ErrorState error={detail.error} onRetry={detail.reload} />
      </>
    );
  }
  if (!detail.data) return null;
  const { review, job, assessment, newer_assessment_available } = detail.data;
  const candidate = candidates.find((c) => c.id === review.candidate_id);

  return (
    <>
      <Link to="/" className="back">
        <Icon name="back" size={16} /> Review queue
      </Link>
      <div className="page-head">
        <div>
          <h1>{job.title ?? "Untitled role"}</h1>
          <p className="muted">{job.company ?? "Company not stated"}</p>
          {candidates.length > 1 && (
            <p className="candidate-label">
              Candidate: <strong>{candidate?.full_name ?? "Unknown candidate"}</strong>
            </p>
          )}
        </div>
      </div>
      {newer_assessment_available && (
        <p className="notice notice--warn">
          This role was re-assessed after your last decision. The assessment below is the newest.
        </p>
      )}
      <div className="detail-grid">
        <div className="detail-grid__main">
          <AssessmentCard assessment={assessment} />
          <JobCard job={job} />
        </div>
        <div className="detail-grid__side">
          <DecisionPanel
            review={review}
            tier={assessment.tier}
            hasBlockingGaps={assessment.has_blocking_gaps}
            onChanged={detail.reload}
          />
          <DocumentsCard reviewId={review.id} status={review.status} version={review.version} />
          {events.data && <History events={events.data} />}
          {events.error && !events.data && (
            <section className="card" aria-labelledby="history-title">
              <h2 id="history-title" className="card__title">
                History
              </h2>
              <ErrorState error={events.error} onRetry={events.reload} />
            </section>
          )}
        </div>
      </div>
      <a className="btn btn--primary decision-jump" href="#decision">
        Your decision: {STATUS_LABEL[review.status]}
      </a>
    </>
  );
}
