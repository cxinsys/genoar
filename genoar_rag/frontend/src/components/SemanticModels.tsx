"use client";

import { useEffect, useState } from "react";
import { checkHealth } from "@/lib/api-client";

/** The exact encoder behind each species' AI Librarian search.
 *
 * Read from the running service rather than written into the page, so the
 * page cannot say one model while the index was built with another — the
 * backend refuses to load an index whose sidecar names a different model, so
 * what /health reports is what ranks the results.
 *
 * Named by identifier, not described: a reader checking a result against the
 * paper needs "sentence-transformers/all-MiniLM-L6-v2", not "a sentence
 * encoder". The paper's text had the name two different ways; this is the
 * one place it is spelled by the thing itself. */
export default function SemanticModels() {
  const [models, setModels] = useState<Record<string, string> | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const ctrl = new AbortController();
    checkHealth(ctrl.signal)
      .then((h) => setModels(h.embedding_models ?? {}))
      // An abort is this effect being cleaned up, not the service failing;
      // in development React mounts twice and the first request is always
      // abandoned. Counting it as a failure left the fallback on screen over
      // the answer the second request brought.
      .catch(() => {
        if (!ctrl.signal.aborted) setFailed(true);
      });
    return () => ctrl.abort();
  }, []);

  if (failed) {
    return (
      <p className="text-sm text-ink-soft">
        The service did not answer; see <code className="font-mono text-xs bg-sunken px-1 py-0.5 rounded">/health</code> for the models in use.
      </p>
    );
  }
  if (models === null) {
    return <div className="h-12 bg-sunken rounded-lg animate-pulse" />;
  }
  const entries = Object.entries(models);
  if (entries.length === 0) {
    return (
      <p className="text-sm text-ink-soft">
        Semantic search is not enabled on this deployment.
      </p>
    );
  }
  return (
    <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-2 text-sm">
      {entries.map(([species, model]) => (
        <div key={species} className="contents">
          <dt className="text-ink-soft capitalize">{species}</dt>
          <dd className="font-mono text-xs text-ink break-all">{model}</dd>
        </div>
      ))}
    </dl>
  );
}
