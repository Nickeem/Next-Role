export function Pager({
  offset,
  pageSize,
  shown,
  total,
  onChange,
}: {
  offset: number;
  pageSize: number;
  shown: number;
  total: number;
  onChange: (offset: number) => void;
}) {
  return (
    <nav className="pager" aria-label="Pagination">
      <button
        type="button"
        className="btn btn--secondary btn--small"
        disabled={offset === 0}
        onClick={() => onChange(Math.max(0, offset - pageSize))}
      >
        Previous
      </button>
      <span className="muted small">
        {offset + 1}–{offset + shown} of {total}
      </span>
      <button
        type="button"
        className="btn btn--secondary btn--small"
        disabled={offset + pageSize >= total}
        onClick={() => onChange(offset + pageSize)}
      >
        Next
      </button>
    </nav>
  );
}
