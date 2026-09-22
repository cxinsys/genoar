export function StatCardSkeleton() {
  return (
    <div className="bg-surface rounded-card p-6 border border-edge shadow-card animate-pulse">
      <div className="h-3 w-24 bg-placeholder rounded mb-3" />
      <div className="h-7 w-32 bg-placeholder rounded mb-3" />
      <div className="h-2 w-full bg-sunken rounded" />
    </div>
  );
}

export function SampleCardSkeleton() {
  return (
    <div className="bg-surface border border-edge rounded-card shadow-card p-5 animate-pulse">
      <div className="flex items-center gap-2 mb-3">
        <div className="h-4 w-24 bg-placeholder rounded" />
        <div className="h-4 w-16 bg-sunken rounded" />
      </div>
      <div className="h-5 w-3/4 bg-placeholder rounded mb-3" />
      <div className="flex gap-2 mb-3">
        <div className="h-5 w-14 bg-sunken rounded" />
        <div className="h-5 w-14 bg-sunken rounded" />
      </div>
      <div className="flex gap-4">
        <div className="h-3 w-20 bg-sunken rounded" />
        <div className="h-3 w-20 bg-sunken rounded" />
      </div>
    </div>
  );
}

export function TableRowSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <tr key={i} className="animate-pulse">
          <td className="px-6 py-3">
            <div className="h-4 w-28 bg-placeholder rounded" />
          </td>
          <td className="px-6 py-3">
            <div className="h-4 w-20 bg-placeholder rounded" />
          </td>
          <td className="px-6 py-3">
            <div className="h-4 w-16 bg-placeholder rounded" />
          </td>
          <td className="px-6 py-3">
            <div className="h-4 w-24 bg-placeholder rounded" />
          </td>
        </tr>
      ))}
    </>
  );
}

export function DetailSkeleton() {
  return (
    <div className="animate-pulse space-y-8">
      {/* Hero */}
      <div className="flex flex-col gap-3 pb-8 border-b border-edge">
        <div className="h-8 w-48 bg-placeholder rounded" />
        <div className="h-4 w-32 bg-placeholder rounded" />
        <div className="h-4 w-96 bg-sunken rounded" />
      </div>
      {/* Info cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {[1, 2, 3].map((i) => (
          <div
            key={i}
            className="bg-surface rounded-card border border-edge shadow-card p-5 space-y-4"
          >
            <div className="h-5 w-32 bg-placeholder rounded" />
            {[1, 2, 3, 4].map((j) => (
              <div key={j} className="flex gap-4">
                <div className="h-3 w-20 bg-sunken rounded" />
                <div className="h-3 w-28 bg-placeholder rounded" />
              </div>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}
