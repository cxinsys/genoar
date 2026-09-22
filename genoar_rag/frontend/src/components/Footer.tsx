import Link from "next/link";

/** The site's footer.
 *
 * Lifted out of the sample page, which was the only page that had one, so the
 * API page can sit in the same frame rather than in a near-copy of it.
 *
 * API Documentation is a link now that there is a page behind it. The other two
 * keep the tooltip: a footer link that goes nowhere is worse than one that says
 * why, and saying "coming soon" is at least true.
 */
const PENDING = ["Privacy Policy", "Terms of Service"];

export default function Footer() {
  return (
    <footer className="bg-surface border-t border-edge py-8 mt-auto">
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 flex flex-col md:flex-row justify-between items-center gap-4">
        <div className="flex items-center gap-2 text-ink-faint">
          <span className="material-symbols-outlined">genetics</span>
          <span className="text-sm font-medium">
            &copy; {new Date().getFullYear()} GENOAR. All rights reserved.
          </span>
        </div>
        <div className="flex gap-6 text-sm text-ink-faint">
          {PENDING.map((label) => (
            <span key={label} className="relative group/tooltip cursor-default">
              {label}
              <span className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-2.5 py-1 bg-inverse text-on-inverse text-xs rounded-md whitespace-nowrap opacity-0 group-hover/tooltip:opacity-100 transition-opacity pointer-events-none z-50">
                Coming soon
                <span className="absolute top-full left-1/2 -translate-x-1/2 border-4 border-transparent border-t-inverse" />
              </span>
            </span>
          ))}
          <Link href="/api-docs" className="hover:text-brand transition-colors">
            API Documentation
          </Link>
        </div>
      </div>
    </footer>
  );
}
