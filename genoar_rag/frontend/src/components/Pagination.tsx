interface PaginationProps {
  total: number;
  offset: number;
  limit: number;
  onPageChange: (newOffset: number) => void;
}

export default function Pagination({
  total,
  offset,
  limit,
  onPageChange,
}: PaginationProps) {
  const totalPages = Math.ceil(total / limit);
  const currentPage = Math.floor(offset / limit) + 1;

  if (totalPages <= 1) return null;

  function getPageNumbers(): (number | "...")[] {
    const pages: (number | "...")[] = [];

    // Always show page 1
    pages.push(1);

    const start = Math.max(2, currentPage - 2);
    const end = Math.min(totalPages - 1, currentPage + 2);

    if (start > 2) pages.push("...");

    for (let i = start; i <= end; i++) {
      pages.push(i);
    }

    if (end < totalPages - 1) pages.push("...");

    // Always show last page
    if (totalPages > 1) pages.push(totalPages);

    return pages;
  }

  function goToPage(page: number) {
    onPageChange((page - 1) * limit);
  }

  return (
    <nav
      aria-label="Pagination"
      className="flex items-center gap-1 bg-surface rounded-lg p-1 border border-edge shadow-control"
    >
      <button
        type="button"
        disabled={currentPage === 1}
        onClick={() => goToPage(currentPage - 1)}
        className="p-2 text-ink-soft hover:text-brand rounded-md hover:bg-raised disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-colors"
        aria-label="Previous page"
      >
        <span className="material-symbols-outlined text-[20px]">
          chevron_left
        </span>
      </button>

      {getPageNumbers().map((page, idx) =>
        page === "..." ? (
          <span
            key={`ellipsis-${idx}`}
            className="px-2 text-ink-faint text-sm select-none"
          >
            ...
          </span>
        ) : (
          <button
            key={page}
            type="button"
            onClick={() => goToPage(page)}
            aria-current={currentPage === page ? "page" : undefined}
            className={`w-9 h-9 flex items-center justify-center text-sm font-medium rounded-md cursor-pointer transition-colors ${
              currentPage === page
                ? "bg-brand text-on-brand font-semibold"
                : "text-ink-body hover:bg-raised hover:text-brand"
            }`}
          >
            {page.toLocaleString()}
          </button>
        ),
      )}

      <button
        type="button"
        disabled={currentPage === totalPages}
        onClick={() => goToPage(currentPage + 1)}
        className="p-2 text-ink-soft hover:text-brand rounded-md hover:bg-raised disabled:opacity-40 disabled:cursor-not-allowed cursor-pointer transition-colors"
        aria-label="Next page"
      >
        <span className="material-symbols-outlined text-[20px]">
          chevron_right
        </span>
      </button>
    </nav>
  );
}
