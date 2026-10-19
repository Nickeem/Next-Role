import { useState } from "react";

import { api } from "../api";
import { useCandidateId } from "../candidates";
import { Pager } from "../components/Pager";
import { ReviewTable } from "../components/ReviewTable";
import { Empty, ErrorState, Loading } from "../components/States";
import { STATUS_LABEL } from "../labels";
import type { ReviewStatus } from "../types";
import { useLoad } from "../useLoad";

const PAGE_SIZE = 25;
const TABS: ReviewStatus[] = ["approved_for_drafting", "shortlisted", "postponed", "rejected"];

export function Decisions() {
  const [status, setStatus] = useState<ReviewStatus>("approved_for_drafting");
  const [offset, setOffset] = useState(0);
  const candidateId = useCandidateId();
  const [scopedTo, setScopedTo] = useState(candidateId);
  if (scopedTo !== candidateId) {
    setScopedTo(candidateId);
    setOffset(0);
  }
  const params = {
    candidate_id: candidateId,
    status: [status],
    sort: "updated",
    limit: PAGE_SIZE,
    offset,
  };
  const list = useLoad(() => api.reviews(params), JSON.stringify(params));

  return (
    <>
      <div className="page-head">
        <h1>Decisions</h1>
      </div>
      <div className="segmented" role="tablist" aria-label="Decision status">
        {TABS.map((s) => (
          <button
            key={s}
            type="button"
            role="tab"
            aria-selected={status === s}
            className={`segmented__item ${status === s ? "segmented__item--active" : ""}`}
            onClick={() => {
              setOffset(0);
              setStatus(s);
            }}
          >
            {STATUS_LABEL[s]}
          </button>
        ))}
      </div>
      <section className="card" role="tabpanel" aria-label={STATUS_LABEL[status]}>
        {status === "approved_for_drafting" && (
          <p className="muted small">
            Approval records your authorization for a later phase to draft documents. Nothing has
            been generated or sent.
          </p>
        )}
        {list.loading && !list.data && <Loading />}
        {list.error && <ErrorState error={list.error} onRetry={list.reload} />}
        {list.data && list.data.items.length === 0 && (
          <Empty title={`No ${STATUS_LABEL[status].toLowerCase()} roles`} />
        )}
        {list.data && list.data.items.length > 0 && (
          <>
            <ReviewTable items={list.data.items} caption={`${STATUS_LABEL[status]} roles`} />
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
    </>
  );
}
