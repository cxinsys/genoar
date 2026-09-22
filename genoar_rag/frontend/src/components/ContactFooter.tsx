/** Who to reach about GENOAR, at the bottom of the dashboard.
 *
 * The corresponding investigators' addresses and lab pages, and the project
 * repository — put on the first page so a reader who wants to follow up never
 * has to leave it to find out how. Lab links rather than only addresses
 * because related publications land on the lab pages, not in this app.
 *
 * Institutional addresses only. This page is public and its text is scraped;
 * an address here belongs to the role and can be handed on, where a personal
 * one follows the person and cannot be withdrawn once it has been collected.
 */

const CONTACTS = [
    {
        name: "Hyojung Paik, Ph.D.",
        role: "Associate Professor of UST, KISTI",
        emails: ["hyojungpaik@kisti.re.kr"],
        lab: {
            label: "Lab home (related publications)",
            url: "https://sites.google.com/view/hplabbioinfo",
        },
    },
    {
        name: "Daewon Lee, Ph.D.",
        role: "Associate Professor, Chung-Ang University",
        emails: ["dwlee@cau.ac.kr"],
        lab: {
            label: "Lab home",
            url: "https://cislab.cau.ac.kr/",
        },
    },
];

const REPO_URL = "https://github.com/cxinsys/genoar";

// Where the data on the page comes from, and the notice the UMLS licence asks
// an application to show before a reader reaches UMLS-derived content. The
// concept annotations (CUIs and matched terms) are that content.
const UMLS_NOTICE =
    "Some material in the UMLS Metathesaurus is from copyrighted sources of " +
    "the respective copyright holders. Users of the UMLS Metathesaurus are " +
    "solely responsible for compliance with any copyright, patent or " +
    "trademark restrictions and are referred to the copyright, patent or " +
    "trademark notices appearing in the original sources, all of which are " +
    "hereby incorporated by reference.";

function ExternalLink({
    url,
    children,
}: {
    url: string;
    children: React.ReactNode;
}) {
    return (
        <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-0.5 text-accent hover:underline break-all"
        >
            {children}
            <span
                className="material-symbols-outlined leading-none"
                style={{ fontSize: "0.875rem" }}
                aria-hidden
            >
                open_in_new
            </span>
        </a>
    );
}

export default function ContactFooter() {
    return (
        // The same horizontal padding as the sections above, so the text lines up
        // with the column it closes. No card: this is the page's quiet last word,
        // not another panel competing with the charts — a hairline sets it apart.
        <footer className="px-6 pb-6">
            <div>
                <h4 className="type-eyebrow">Contact</h4>
                <div className="mt-3 grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-4">
                    {CONTACTS.map((c) => (
                        <div key={c.name} className="text-sm leading-relaxed">
                            <p className="font-semibold text-ink">{c.name}</p>
                            <p className="text-ink-faint">{c.role}</p>
                            <p className="text-ink-body">
                                {c.emails.map((email, i) => (
                                    <span key={email}>
                                        {i > 0 && (
                                            <span className="text-ink-faint">
                                                {" "}
                                                ·{" "}
                                            </span>
                                        )}
                                        <a
                                            href={`mailto:${email}`}
                                            className="text-accent hover:underline"
                                        >
                                            {email}
                                        </a>
                                    </span>
                                ))}
                            </p>
                            <p className="text-ink-body">
                                <span className="text-ink-faint">
                                    {c.lab.label}:{" "}
                                </span>
                                <ExternalLink url={c.lab.url}>
                                    {c.lab.url}
                                </ExternalLink>
                            </p>
                        </div>
                    ))}
                </div>
                <p className="mt-4 pt-4 border-t border-edge text-sm text-ink-body">
                    <span className="text-ink-faint">Related repository: </span>
                    <ExternalLink url={REPO_URL}>{REPO_URL}</ExternalLink>
                </p>
                <div className="mt-4 pt-4 border-t border-edge text-xs leading-relaxed text-ink-faint">
                    <p>
                        <span className="font-semibold">Data sources: </span>
                        sample metadata from{" "}
                        <ExternalLink url="https://www.ncbi.nlm.nih.gov/geo/">
                            NCBI GEO
                        </ExternalLink>{" "}
                        and{" "}
                        <ExternalLink url="https://www.ncbi.nlm.nih.gov/sra">
                            SRA
                        </ExternalLink>
                        ; concept annotations made against the{" "}
                        <ExternalLink url="https://www.nlm.nih.gov/research/umls/">
                            UMLS Metathesaurus
                        </ExternalLink>{" "}
                        (release 2024AB) of the U.S. National Library of
                        Medicine.
                    </p>
                    <p className="mt-2">{UMLS_NOTICE}</p>
                </div>
            </div>
        </footer>
    );
}
