import type { Metadata } from "next";
import Header from "@/components/Header";
import LoadingIndicator from "@/components/LoadingIndicator";
import DownloadToast from "@/components/DownloadToast";
import "./globals.css";

export const metadata: Metadata = {
  title: "GENOAR - An AI Librarian for SRA Metadata",
  description: "Search biological samples using natural language",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        {/* Before anything is painted.
         *
         * The theme lives in localStorage, which the server cannot read, so the markup
         * it sends is always the light one. Left to React, the correction would arrive
         * after the first paint — which is a white page that turns dark, once per visit,
         * for exactly the readers who asked for it not to be white.
         *
         * So this runs synchronously in the head, before the body exists. It is the only
         * inline script on the site and it stays small enough to read in one go; what it
         * writes is the same two attributes lib/preferences.ts writes later, and the two
         * agree because both resolve the same way — a palette that names a brightness
         * wins, and otherwise "system" is asked of the OS.
         *
         * The one piece of knowledge duplicated out of the registry is `fixed` — which
         * palettes name their own brightness, and which one each names. That is
         * deliberate: this script cannot import, and the alternative is shipping the
         * palette table twice. It doubles as the check that the stored name is one we
         * know, so a palette dropped in a later version falls back to plain here the same
         * way it does in preferences.ts.
         *
         * Getting an entry wrong costs a flash and not a wrong page, because preferences.ts
         * resolves it properly a moment later. Leaving one out costs more than that, so
         * every palette has to appear here. The table cannot be shortened to "anything but
         * plain is light": a dark palette missing from it opens a night page on a white
         * flash.
         *
         * suppressHydrationWarning on <html> above is the price: the server sends an
         * element without the attributes and the client finds one with them, which is the
         * whole point and not a mistake to be told about. */}
        <script
          dangerouslySetInnerHTML={{
            __html: `try{var F={nautical:"light",tropical:"light",night:"dark"},p=JSON.parse(localStorage.getItem("genoar:preferences")||"{}"),t=p.theme||"system",l=F[p.palette]?p.palette:"plain",d=document.documentElement;d.dataset.palette=l;d.dataset.theme=F[l]||(t==="system"?(matchMedia("(prefers-color-scheme: dark)").matches?"dark":"light"):t)}catch(e){}`,
          }}
        />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link
          rel="preconnect"
          href="https://fonts.gstatic.com"
          crossOrigin=""
        />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700;800;900&family=Roboto+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
        {/* display=block, not swap: a Material Symbols name is rendered as its own
            text until the font loads, so swapping would flash the word "search"
            where the icon belongs. Blocking hides it instead, and the 1em box in
            globals.css keeps the swap from moving the layout. */}
        <link
          href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:wght,FILL@100..700,0..1&display=block"
          rel="stylesheet"
        />
      </head>
      {/* A fixed shell: the window never scrolls, each page scrolls inside its
          own container. That keeps the header at full width — a page-level
          scrollbar would take its 8px out of every element on the page — and
          gives every route the same scrollbar in the same place. */}
      <body className="antialiased h-screen overflow-hidden flex flex-col">
        {/* Here and not in the pages. A layout survives a route change and its
            children do not, so the header rendered from a page was being torn
            down and rebuilt on every navigation — and a rebuilt element starts
            its animations over, which is the sea jolting back to its starting
            position each time you moved between pages. */}
        <Header />
        {children}
        {/* Last, and fixed, so it sits over every page without any of them
            leaving room for it — and in the layout so that it survives a route
            change, which is one of the times something is loading. */}
        <LoadingIndicator />
        {/* A corner note while an export is being prepared, since a streaming
            download shows nothing until its first bytes arrive. Fixed and in the
            layout for the same reason as the indicator above. */}
        <DownloadToast />
      </body>
    </html>
  );
}
