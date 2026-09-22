import Link from "next/link";

interface ErrorStateProps {
  type: "network" | "not-found" | "empty" | "generic";
  message?: string;
  onRetry?: () => void;
}

const CONFIG = {
  network: {
    icon: "cloud_off",
    title: "Can't connect to server",
    description: "Check your network connection and try again.",
  },
  "not-found": {
    icon: "search_off",
    title: "Sample not found",
    description: "The requested resource doesn't exist.",
  },
  empty: {
    icon: "filter_list_off",
    title: "No matching samples",
    description: "Try adjusting or resetting your filters.",
  },
  generic: {
    icon: "error_outline",
    title: "Something went wrong",
    description: "Please try again shortly.",
  },
};

export default function ErrorState({
  type,
  message,
  onRetry,
}: ErrorStateProps) {
  const config = CONFIG[type];

  return (
    <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
      <span
        className="material-symbols-outlined text-ink-faint mb-4"
        style={{ fontSize: "64px" }}
      >
        {config.icon}
      </span>
      <h3 className="text-lg font-bold text-ink-body mb-2">{config.title}</h3>
      <p className="text-sm text-ink-soft max-w-md mb-6">
        {message || config.description}
      </p>

      <div className="flex gap-3">
        {type === "network" && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="px-4 py-2 bg-brand text-on-brand rounded-lg text-sm font-medium hover:bg-brand-soft cursor-pointer transition-colors flex items-center gap-2"
          >
            <span className="material-symbols-outlined text-[18px]">
              refresh
            </span>
            Try again
          </button>
        )}

        {type === "not-found" && (
          <Link
            href="/"
            className="px-4 py-2 bg-brand text-on-brand rounded-lg text-sm font-medium hover:bg-brand-soft transition-colors flex items-center gap-2"
          >
            <span className="material-symbols-outlined text-[18px]">
              dataset
            </span>
            Go to Dashboard
          </Link>
        )}

        {type === "empty" && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="px-4 py-2 border border-edge-firm text-ink-body rounded-lg text-sm font-medium hover:bg-raised cursor-pointer transition-colors flex items-center gap-2"
          >
            <span className="material-symbols-outlined text-[18px]">
              filter_list_off
            </span>
            Reset filters
          </button>
        )}

        {type === "generic" && onRetry && (
          <button
            type="button"
            onClick={onRetry}
            className="px-4 py-2 bg-brand text-on-brand rounded-lg text-sm font-medium hover:bg-brand-soft cursor-pointer transition-colors flex items-center gap-2"
          >
            <span className="material-symbols-outlined text-[18px]">
              refresh
            </span>
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
