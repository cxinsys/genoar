"use client";

import { useEffect, useState } from "react";

/** A small corner note that follows a download from start to finish.
 *
 * The exports stream, and a large one takes a few seconds to begin: a plain
 * anchor download shows nothing until the browser's own shelf appears, so a
 * click looked like nothing happened. This fetches the file itself instead, so
 * it knows the three things the anchor could not tell it — that it has started,
 * that it finished, and if it failed — and holds the note until the file is
 * actually saved.
 *
 * Mounted once by the layout, so the fetch and the note both outlive a move to
 * another page: navigating inside the app does not interrupt a download. Only a
 * full reload would, which is the browser throwing the page away, not us.
 *
 * The buffering an anchor avoids is fine here: these are curated-metadata CSVs,
 * tens of megabytes at most. The large binary sample files are not routed
 * through this — they stay plain streaming anchors. */

const EVENT = "genoar:download";
const SAVED_MS = 2200;
const ERROR_MS = 5000;

type Phase = "loading" | "done" | "error";

interface Detail {
  url: string;
  label: string;
}

interface Toast {
  id: number;
  label: string;
  phase: Phase;
}

/** Fetch `url` and save it, with a corner note for the wait. `label` names the
 *  file plainly, e.g. "The full catalogue". */
export function startDownload(url: string, label: string): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent<Detail>(EVENT, { detail: { url, label } }));
}

function filenameFrom(res: Response, fallback: string): string {
  const cd = res.headers.get("content-disposition") ?? "";
  const match = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
  return match ? decodeURIComponent(match[1]) : fallback;
}

function Spinner() {
  // An SVG rather than a rotated icon glyph: a glyph sits off the centre of its
  // own box, so spinning one wobbles. This turns about its own middle.
  return (
    <svg
      className="animate-spin text-brand"
      width="20"
      height="20"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden
    >
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" opacity="0.2" />
      <path
        d="M21 12a9 9 0 0 0-9-9"
        stroke="currentColor"
        strokeWidth="3"
        strokeLinecap="round"
      />
    </svg>
  );
}

export default function DownloadToast() {
  const [toast, setToast] = useState<Toast | null>(null);
  // Drives the slide/fade: false on the first paint after a toast arrives.
  const [shown, setShown] = useState(false);

  useEffect(() => {
    async function run(url: string, label: string) {
      const id = Date.now();
      setToast({ id, label, phase: "loading" });
      setShown(false);
      requestAnimationFrame(() => requestAnimationFrame(() => setShown(true)));

      try {
        const res = await fetch(url);
        if (!res.ok) throw new Error(String(res.status));
        const blob = await res.blob();
        const name = filenameFrom(res, "genoar_export.csv");
        const href = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = href;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(href), 10000);

        setToast((t) => (t && t.id === id ? { ...t, phase: "done" } : t));
        setTimeout(() => setShown(false), SAVED_MS);
      } catch {
        setToast((t) => (t && t.id === id ? { ...t, phase: "error" } : t));
        setTimeout(() => setShown(false), ERROR_MS);
      }
    }

    function onEvent(e: Event) {
      const detail = (e as CustomEvent<Detail>).detail;
      if (detail?.url) run(detail.url, detail.label);
    }

    window.addEventListener(EVENT, onEvent as EventListener);
    return () => window.removeEventListener(EVENT, onEvent as EventListener);
  }, []);

  if (!toast) return null;

  const heading =
    toast.phase === "done"
      ? "Download ready"
      : toast.phase === "error"
        ? "Download failed"
        : "Preparing your download";
  const sub =
    toast.phase === "done"
      ? `${toast.label} has been saved.`
      : toast.phase === "error"
        ? `${toast.label} could not be fetched. Please try again.`
        : `${toast.label} is being prepared.`;

  return (
    <div
      role="status"
      aria-live="polite"
      onTransitionEnd={() => {
        // Leave the DOM once faded out, so it is not a hidden click target.
        if (!shown) setToast(null);
      }}
      style={{
        opacity: shown ? 1 : 0,
        transform: shown ? "translateY(0)" : "translateY(0.5rem)",
      }}
      className="fixed bottom-4 right-4 z-60 flex items-start gap-3 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-edge bg-surface text-ink-body px-4 py-3 shadow-2xl transition-all duration-200"
    >
      <span className="shrink-0 mt-0.5 inline-flex">
        {toast.phase === "loading" ? (
          <Spinner />
        ) : (
          <span
            className={`material-symbols-outlined text-[20px] leading-none ${
              toast.phase === "done" ? "text-brand" : "text-amber-600"
            }`}
          >
            {toast.phase === "done" ? "check_circle" : "error"}
          </span>
        )}
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium text-ink">{heading}</p>
        <p className="text-xs text-ink-soft mt-0.5">{sub}</p>
      </div>
      <button
        type="button"
        onClick={() => setShown(false)}
        aria-label="Dismiss"
        className="shrink-0 -mr-1 -mt-0.5 text-ink-faint hover:text-ink-soft transition-colors cursor-pointer"
      >
        <span className="material-symbols-outlined text-[18px] leading-none">
          close
        </span>
      </button>
    </div>
  );
}
