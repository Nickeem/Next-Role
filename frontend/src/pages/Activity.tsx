import { useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { Empty, ErrorState, Loading } from "../components/States";
import { EVENT_LABEL, formatDateTime, jobLabel, STATUS_LABEL } from "../labels";
import { useLoad } from "../useLoad";

const PAGE_SIZE = 30;

export function Activity() {
  const [offset, setOffset] = useState(0);
  const [onlyMine, setOnlyMine] = useState(false);
  const key = `${offset}:${onlyMine}`;
  const page = useLoad(
    () => api.activity({ limit: PAGE_SIZE, offset, actor: onlyMine ? "operator" : undefined }),
    key,
  );

  return (
    <>
      <div className="page-head">
        <h1>Activity</h1>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={onlyMine}
            onChange={(e) => {
              setOffset(0);
              setOnlyMine(e.target.checked);
            }}
          />
          Only my decisions
        </label>
      </div>
      <section className="card" aria-label="Review history">
        <p className="muted small">
          Every review event, newest first. History is append-only. Notification delivery is shown
          under Settings.
        </p>
        {page.loading && !page.data && <Loading />}
        {page.error && <ErrorState error={page.error} onRetry={page.reload} />}
        {page.data && page.data.items.length === 0 && <Empty title="No activity yet" />}
        {page.data && page.data.items.length > 0 && (
          <>
            <ol className="timeline timeline--wide">
              {page.data.items.map((e) => (
                <li key={e.id}>
                  <span className="timeline__what">
                    {EVENT_LABEL[e.event_type]}
                    {e.previous_status !== e.new_status && ` → ${STATUS_LABEL[e.new_status]}`}
                  </span>
                  <Link to={`/reviews/${e.review_id}`}>{jobLabel(e.job_title, e.company)}</Link>
                  <span className="muted small">
                    {formatDateTime(e.created_at)} · {e.actor === "operator" ? "you" : "system"}
                  </span>
                  {e.reason && <q className="small">{e.reason}</q>}
                </li>
              ))}
            </ol>
            <nav className="pager" aria-label="Pagination">
              <button
                type="button"
                className="btn btn--secondary btn--small"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Newer
              </button>
              <span className="muted small">
                {offset + 1}–{offset + page.data.items.length} of {page.data.total}
              </span>
              <button
                type="button"
                className="btn btn--secondary btn--small"
                disabled={offset + PAGE_SIZE >= page.data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Older
              </button>
            </nav>
          </>
        )}
      </section>
    </>
  );
}
