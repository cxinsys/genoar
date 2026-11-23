import Link from "next/link";

export type GuideSection = "guide" | "data" | "api";

/** The two pages under the guide, in the order they sit across the header.
 *  Each is its own page at its own address — /api-docs is linked from outside
 *  and keeps working — and the header is how they read as one place. */
const SECTIONS: {
  key: GuideSection;
  href: string;
  icon: string;
  title: string;
  badge?: string;
  blurb: string;
}[] = [
  {
    key: "guide",
    href: "/guide",
    icon: "menu_book",
    title: "Analysis Code Support",
    blurb: "Notes on using the data GENOAR provides.",
  },
  {
    key: "data",
    href: "/guide/scope",
    icon: "dataset",
    title: "Data Scope",
    blurb: "What the corpus holds, how it was selected, and where its boundaries are.",
  },
  {
    key: "api",
    href: "/api-docs",
    icon: "code",
    title: "API",
    badge: "No key required",
    blurb:
      "The website uses these endpoints. They are open to anyone and need no account or key. All paths are relative to this host.",
  },
];

function Badge({ text }: { text: string }) {
  return (
    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-bold bg-accent/12 text-accent border border-accent/25">
      {text}
    </span>
  );
}

/** The title of the guide's pages, with the way to the other one.
 *
 * From md up the row is split in half, one page to each side, set alike. The
 * page you are on is drawn as an index tab — the cards' colour at its tip,
 * fading down into the page, with nothing along its foot, so it reads as the
 * top of what is below it. The other half is the same text with no tab and its
 * title underlined, which is what says it can be pressed. A line runs under
 * that half alone, so the tab is the one gap in it.
 *
 * Both halves are always there, whichever page this is, so the row is the same
 * height on both and nothing below it moves when you switch.
 *
 * Below md the halves would each be half a phone, too narrow for either title,
 * so it is the page's title with a row of tabs under it instead — the dashboard
 * explorer's tabs, sat the same way on the border.
 *
 * The page says which half it is rather than this reading the path: both pages
 * are rendered on the server, and each already knows. */
export default function GuideHeader({ active }: { active: GuideSection }) {
  const current = SECTIONS.find((s) => s.key === active)!;

  return (
    <>
      {/* Phone: title, then tabs on the border. */}
      <div className="md:hidden flex flex-col gap-4 mb-8 border-b border-edge">
        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-3 flex-wrap">
            <h1 className="type-figure text-3xl">{current.title}</h1>
            {current.badge && <Badge text={current.badge} />}
          </div>
          <p className="text-sm text-ink-soft">{current.blurb}</p>
        </div>
        {/* Three tabs want more than a phone is wide, so the row scrolls
            sideways rather than clipping the last one — the explorer's tabs
            on the dashboard do the same. */}
        <nav
          aria-label="Guide sections"
          className="flex gap-6 -mb-px overflow-x-auto hide-scrollbar"
        >
          {SECTIONS.map(({ key, href, icon, title }) => {
            const isActive = key === active;
            return (
              <Link
                key={key}
                href={href}
                aria-current={isActive ? "page" : undefined}
                className="pb-3 border-b-2 font-medium text-sm flex items-center gap-2 transition-colors whitespace-nowrap"
                style={
                  isActive
                    ? {
                        borderColor: "var(--color-brand)",
                        color: "var(--color-brand)",
                      }
                    : {
                        borderColor: "transparent",
                        color: "var(--color-ink-soft)",
                      }
                }
              >
                <span className="material-symbols-outlined text-[18px]">
                  {icon}
                </span>
                {title}
              </Link>
            );
          })}
        </nav>
      </div>

      {/* md and up: half each, the current page as a tab. Both halves carry
          the same border and padding, so they are the same box whatever is
          drawn in it; only which edges are coloured differs. */}
      <div className="hidden md:grid grid-cols-3 mb-8">
        {SECTIONS.map((s) =>
          s.key === active ? (
            <div
              key={s.key}
              className="guide-bookmark rounded-t-2xl border border-edge border-b-transparent px-6 pt-6 pb-8"
            >
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="type-figure text-3xl">{s.title}</h1>
                {s.badge && <Badge text={s.badge} />}
              </div>
              <p className="text-sm text-ink-soft mt-2 max-w-xl">{s.blurb}</p>
            </div>
          ) : (
            <Link
              key={s.key}
              href={s.href}
              className="group border border-transparent border-b-edge px-6 pt-6 pb-8"
            >
              <div className="flex items-center gap-3 flex-wrap">
                <p className="type-figure text-3xl underline decoration-2 underline-offset-[6px] decoration-ink-faint group-hover:text-brand group-hover:decoration-brand transition-colors">
                  {s.title}
                </p>
                {s.badge && <Badge text={s.badge} />}
              </div>
              <p className="text-sm text-ink-soft mt-2 max-w-xl">{s.blurb}</p>
            </Link>
          ),
        )}
      </div>
    </>
  );
}
