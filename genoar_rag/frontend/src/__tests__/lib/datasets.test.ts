/** What naming a dataset actually puts on the wire.
 *
 * Two claims are checked here. That a deployment serving one dataset produces
 * the addresses it always produced — the same URLs, the same requests, the
 * same bookmarks — because the dataset is left out when it is the default. And
 * that naming one carries it through every address a page builds.
 *
 * Reading the built bundle cannot show either: the code that adds a dataset is
 * compiled in whether it fires or not, so only a call tells you which happened.
 */

import fs from "fs";
import path from "path";

import { buildSearchParams, buildExportUrl, buildSampleExportUrl } from "@/lib/api-client";
import { isDefaultDataset, parseDataset, sampleHref } from "@/lib/datasets";

describe("a deployment that serves one dataset", () => {
  it("says nothing about it in a search", () => {
    expect(buildSearchParams({ tissue: ["Blood"] })).toBe("tissue=Blood");
  });

  it("nor in an export URL", () => {
    expect(buildExportUrl({ tissue: ["Blood"] })).toBe(
      "/api/v1/export?tissue=Blood&format=csv",
    );
  });

  it("nor in a single sample's export URL", () => {
    expect(buildSampleExportUrl("SRR1", "csv", undefined)).toBe(
      "/api/v1/export?accession=SRR1&format=csv",
    );
  });

  it("nor in the way into a sample page", () => {
    expect(sampleHref("SRR1", undefined)).toBe("/sample/SRR1");
  });
});

describe("naming a dataset", () => {
  it("carries it on a search", () => {
    expect(buildSearchParams({ tissue: ["Blood"], dataset: "atlas" })).toBe(
      "tissue=Blood&dataset=atlas",
    );
  });

  it("carries it on an export, which is the same set as the page", () => {
    expect(buildExportUrl({ tissue: ["Blood"], dataset: "atlas" })).toBe(
      "/api/v1/export?tissue=Blood&dataset=atlas&format=csv",
    );
  });

  it("carries it on one sample's export", () => {
    expect(buildSampleExportUrl("SRR1", "csv", "atlas")).toBe(
      "/api/v1/export?accession=SRR1&format=csv&dataset=atlas",
    );
  });

  it("carries it into the sample page", () => {
    expect(sampleHref("SRR1", "atlas")).toBe("/sample/SRR1?dataset=atlas");
  });

  it("survives an export dropping the paging it does not use", () => {
    const url = buildExportUrl({
      tissue: ["Blood"],
      offset: 40,
      limit: 20,
      dataset: "atlas",
    });
    expect(url).toContain("dataset=atlas");
    expect(url).not.toContain("offset");
    expect(url).not.toContain("limit");
  });

  it("escapes a name that would otherwise change the query string", () => {
    expect(sampleHref("SRR1", "a b&c")).toBe("/sample/SRR1?dataset=a%20b%26c");
  });
});

describe("reading a dataset back off an address", () => {
  it("takes whatever is there", () => {
    // Which names exist is the server's to know — it answers 404 for one it
    // does not serve. Checking here would mean holding a second copy of the
    // list and being able to disagree with the server about it.
    expect(parseDataset("atlas")).toBe("atlas");
    expect(parseDataset("anything")).toBe("anything");
  });

  it("treats an absent or empty one as the default", () => {
    expect(parseDataset(null)).toBeUndefined();
    expect(parseDataset("")).toBeUndefined();
    expect(parseDataset("   ")).toBeUndefined();
  });

  it("and so does everything that decides whether to say it", () => {
    expect(isDefaultDataset(undefined)).toBe(true);
    expect(isDefaultDataset("")).toBe(true);
    expect(isDefaultDataset("atlas")).toBe(false);
  });
});

describe("the way into a sample page", () => {
  it("is the only place a sample address is built", () => {
    // Not a behavioural check — a structural one, because the fault was
    // structural. There were two builders: cards on the dashboard carried the
    // corpus and the links inside a sample page (the series table, the
    // similar-samples cards) did not, so following either out of a paper page
    // landed in another dataset, where that sample may not exist at all
    // and a shared one carries the other table's annotations.
    //
    // A third builder would pass every test above while reintroducing exactly
    // that, so what is asserted is that there is no third builder. The search
    // page is exempt: it always serves the default dataset, and there the two
    // forms are the same string.
    const root = path.join(__dirname, "..", "..");
    const offenders: string[] = [];

    const walk = (dir: string) => {
      for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__") walk(full);
          continue;
        }
        if (!/\.tsx?$/.test(entry.name)) continue;
        const rel = path.relative(root, full);
        if (rel === path.join("lib", "datasets.ts")) continue;
        if (rel.startsWith(path.join("app", "search"))) continue;
        if (rel === path.join("components", "SimilarityMap.tsx")) continue;
        if (/`\/sample\/\$\{/.test(fs.readFileSync(full, "utf8"))) {
          offenders.push(rel);
        }
      }
    };
    walk(root);

    expect(offenders).toEqual([]);
  });
});
