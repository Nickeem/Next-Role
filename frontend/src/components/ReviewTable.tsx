import { Link } from "react-router-dom";

import { formatDate, formatDateTime } from "../labels";
import type { ReviewListItem } from "../types";
import { StatusPill, TierBadge } from "./Pills";

function Score({ item }: { item: ReviewListItem }) {
  const { score } = item.assessment;
  return (
    <span className="score">
      <span className="score__value">{score ?? "—"}</span>
      <span className="sr-only"> out of 100</span>
    </span>
  );
}

/** A table on wide screens; each row becomes a stacked card on narrow screens (CSS). */
export function ReviewTable({ items, caption }: { items: ReviewListItem[]; caption: string }) {
  return (
    <table className="reviews">
      <caption className="sr-only">{caption}</caption>
      <thead>
        <tr>
          <th scope="col">Role</th>
          <th scope="col">Suitability</th>
          <th scope="col">Score</th>
          <th scope="col">Your decision</th>
          <th scope="col">
            <span className="sr-only">Action</span>
          </th>
        </tr>
      </thead>
      <tbody>
        {items.map((item) => (
          <tr key={item.id}>
            <td data-label="Role">
              <span className="role">{item.job.title ?? "Untitled role"}</span>
              <span className="muted small">
                {[item.job.company, item.job.location].filter(Boolean).join(" · ") || "Details not stated"}
              </span>
            </td>
            <td data-label="Suitability">
              <TierBadge tier={item.assessment.tier} />
              {item.assessment.has_blocking_gaps && <span className="flag">Blocking gap</span>}
              <span className="muted small">Assessed {formatDate(item.assessment.as_of)}</span>
            </td>
            <td data-label="Score">
              <Score item={item} />
            </td>
            <td data-label="Your decision">
              <StatusPill status={item.status} />
              {item.status === "postponed" && item.postpone_until && (
                <span className={`small ${item.follow_up_due ? "due" : "muted"}`}>
                  {item.follow_up_due ? "Due since " : "Until "}
                  {formatDateTime(item.postpone_until)}
                </span>
              )}
            </td>
            <td className="reviews__action">
              <Link className="btn btn--secondary btn--small" to={`/reviews/${item.id}`}>
                Open<span className="sr-only"> {item.job.title ?? "role"}</span>
              </Link>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
