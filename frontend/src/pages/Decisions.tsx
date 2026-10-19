import { useState } from "react";

import { api } from "../api";
import { ReviewTable } from "../components/ReviewTable";
import { Empty, ErrorState, Loading } from "../components/States";
import { STATUS_LABEL } from "../labels";
import type { ReviewStatus } from "../types";
import { useLoad } from "../useLoad";

const TABS: ReviewStatus[] = ["approved_for_drafting", "shortlisted", "postponed", "rejected"];

export function Decisions() {
  const [status, setStatus] = useState<ReviewStatus>("approved_for_drafting");
  const list = useLoad(() => api.reviews({ status: [status], sort: "updated", limit: 100 }), status);

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
            onClick={() => setStatus(s)}
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
          <ReviewTable items={list.data.items} caption={`${STATUS_LABEL[status]} roles`} />
        )}
      </section>
    </>
  );
}
