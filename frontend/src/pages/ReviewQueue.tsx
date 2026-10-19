import { useId, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { useCandidateId } from "../candidates";
import { Pager } from "../components/Pager";
import { ReviewTable } from "../components/ReviewTable";
import { Empty, ErrorState, Loading } from "../components/States";
import { formatDateTime, jobLabel, STATUS_LABEL, TIER_LABEL } from "../labels";
import type { ReviewSort, ReviewStatus, Tier } from "../types";
import { useLoad } from "../useLoad";

const PAGE_SIZE = 20;
const QUEUE_STATUSES: ReviewStatus[] = ["pending_review", "shortlisted", "postponed"];

function SummaryCounts() {
  const candidateId = useCandidateId();
  const { data } = useLoad(() => api.summary(candidateId), `summary:${candidateId}`);
  if (!data) return <div className="counts counts--placeholder" aria-hidden="true" />;
  const items: { label: string; value: number; tone: string }[] = [
    { label: "to review", value: data.counts.pending_review, tone: "pending_review" },
    { label: "shortlisted", value: data.counts.shortlisted, tone: "shortlisted" },
    { label: "postponed", value: data.counts.postponed, tone: "postponed" },
    { label: "approved", value: data.counts.approved_for_drafting, tone: "approved_for_drafting" },
  ];
  return (
    <ul className="counts" aria-label="Review counts">
      {items.map((i) => (
        <li key={i.label} className={`count count--${i.tone}`}>
          <span className="count__value">{i.value}</span> {i.label}
        </li>
      ))}
      {data.follow_ups_due > 0 && (
        <li className="count count--due">
          <span className="count__value">{data.follow_ups_due}</span> follow-ups due
        </li>
      )}
    </ul>
  );
}

function PriorityCard() {
  const candidateId = useCandidateId();
  const { data } = useLoad(
    () =>
      api.reviews({
        candidate_id: candidateId,
        status: ["pending_review"],
        tier: ["strong"],
        sort: "score",
        limit: 3,
      }),
    `priority:${candidateId}`,
  );
  return (
    <section className="card mini" aria-labelledby="priority-title">
      <h2 id="priority-title" className="card__title">
        Priority reviews
      </h2>
      <p className="muted small">Strong matches still waiting for you</p>
      {data && data.items.length === 0 && <p className="muted">Nothing waiting.</p>}
      <ul className="mini__list">
        {data?.items.map((i) => (
          <li key={i.id}>
            <Link to={`/reviews/${i.id}`}>{jobLabel(i.job.title, i.job.company)}</Link>
            <span className="muted small">Score {i.assessment.score ?? "—"}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function FollowUpsCard() {
  const candidateId = useCandidateId();
  const { data } = useLoad(
    () => api.reviews({ candidate_id: candidateId, status: ["postponed"], sort: "updated", limit: 3 }),
    `postponed:${candidateId}`,
  );
  return (
    <section className="card mini" aria-labelledby="followups-title">
      <h2 id="followups-title" className="card__title">
        Follow-ups
      </h2>
      <p className="muted small">Postponed roles</p>
      {data && data.items.length === 0 && <p className="muted">No postponed roles.</p>}
      <ul className="mini__list">
        {data?.items.map((i) => (
          <li key={i.id}>
            <Link to={`/reviews/${i.id}`}>{jobLabel(i.job.title, i.job.company)}</Link>
            {i.postpone_until && (
              <span className={`small ${i.follow_up_due ? "due" : "muted"}`}>
                {i.follow_up_due ? "Due now" : formatDateTime(i.postpone_until)}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}

function RecentDecisionsCard() {
  const candidateId = useCandidateId();
  const { data } = useLoad(
    () => api.activity({ candidate_id: candidateId, actor: "operator", limit: 3 }),
    `recent:${candidateId}`,
  );
  return (
    <section className="card mini" aria-labelledby="recent-title">
      <h2 id="recent-title" className="card__title">
        Recent decisions
      </h2>
      <p className="muted small">From your activity history</p>
      {data && data.items.length === 0 && <p className="muted">No decisions yet.</p>}
      <ul className="mini__list">
        {data?.items.map((e) => (
          <li key={e.id}>
            <Link to={`/reviews/${e.review_id}`}>{jobLabel(e.job_title, e.company)}</Link>
            <span className="muted small">{STATUS_LABEL[e.new_status]}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ReviewQueue() {
  const searchId = useId();
  const statusId = useId();
  const tierId = useId();
  const sortId = useId();
  const [search, setSearch] = useState("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState<ReviewStatus | "">("");
  const [tier, setTier] = useState<Tier | "">("");
  const [sort, setSort] = useState<ReviewSort>("score");
  const [offset, setOffset] = useState(0);
  const candidateId = useCandidateId();
  const [scopedTo, setScopedTo] = useState(candidateId);
  if (scopedTo !== candidateId) {
    // A different candidate's queue starts from the first page.
    setScopedTo(candidateId);
    setOffset(0);
  }

  const params = {
    candidate_id: candidateId,
    status: status ? [status] : QUEUE_STATUSES,
    tier: tier ? [tier] : undefined,
    q: query || undefined,
    sort,
    limit: PAGE_SIZE,
    offset,
  };
  const list = useLoad(() => api.reviews(params), JSON.stringify(params));

  return (
    <>
      <div className="page-head">
        <h1>Role review</h1>
        <SummaryCounts />
      </div>
      <section className="card" aria-labelledby="queue-title">
        <h2 id="queue-title" className="sr-only">
          Review queue
        </h2>
        <form
          className="filters"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setOffset(0);
            setQuery(search.trim());
          }}
        >
          <div className="field field--inline field--grow">
            <label htmlFor={searchId}>Search</label>
            <input
              id={searchId}
              type="search"
              placeholder="Title or company"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <div className="field field--inline">
            <label htmlFor={statusId}>Status</label>
            <select
              id={statusId}
              value={status}
              onChange={(e) => {
                setOffset(0);
                setStatus(e.target.value as ReviewStatus | "");
              }}
            >
              <option value="">Open (not decided)</option>
              {QUEUE_STATUSES.map((s) => (
                <option key={s} value={s}>
                  {STATUS_LABEL[s]}
                </option>
              ))}
            </select>
          </div>
          <div className="field field--inline">
            <label htmlFor={tierId}>Suitability</label>
            <select
              id={tierId}
              value={tier}
              onChange={(e) => {
                setOffset(0);
                setTier(e.target.value as Tier | "");
              }}
            >
              <option value="">Any</option>
              {(Object.keys(TIER_LABEL) as Tier[]).map((t) => (
                <option key={t} value={t}>
                  {TIER_LABEL[t]}
                </option>
              ))}
            </select>
          </div>
          <div className="field field--inline">
            <label htmlFor={sortId}>Sort</label>
            <select id={sortId} value={sort} onChange={(e) => setSort(e.target.value as ReviewSort)}>
              <option value="score">Highest score</option>
              <option value="assessed">Recently assessed</option>
              <option value="updated">Recently changed</option>
              <option value="created">Newest</option>
            </select>
          </div>
          <button type="submit" className="btn btn--secondary">
            Apply
          </button>
        </form>

        {list.loading && !list.data && <Loading label="Loading reviews" />}
        {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
        {list.data && list.data.items.length === 0 && (
          <Empty title="No roles to review">
            Roles appear here after a job is assessed. Rejected and approved roles are under
            Decisions.
          </Empty>
        )}
        {list.data && list.data.items.length > 0 && (
          <>
            <ReviewTable items={list.data.items} caption="Roles awaiting a decision" />
            <Pager
              offset={offset}
              pageSize={PAGE_SIZE}
              shown={list.data.items.length}
              total={list.data.total}
              onChange={setOffset}
            />
          </>
        )}
      </section>
      <div className="mini-grid">
        <PriorityCard />
        <FollowUpsCard />
        <RecentDecisionsCard />
      </div>
      <p className="muted small disclaimer">
        Suitability labels and scores come from the stored assessment. They rank roles by stated
        requirements and supported evidence; they are not a probability of being hired.
      </p>
    </>
  );
}
