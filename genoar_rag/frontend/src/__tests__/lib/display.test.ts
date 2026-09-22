import { displayLabel, displayCellType } from "@/lib/display";

describe("displayLabel", () => {
  it("writes an ordinary value in sentence case", () => {
    expect(displayLabel("Bone Marrow")).toBe("Bone marrow");
    expect(displayLabel("bone marrow")).toBe("Bone marrow");
    expect(displayLabel("Spinal Cord")).toBe("Spinal cord");
    expect(displayLabel("Control Groups")).toBe("Control groups");
  });

  it("folds the spellings the curated tables hold for one thing", () => {
    // All three are in `sra_core.tissue`.
    const spellings = ["Bone Marrow", "Bone marrow", "bone marrow"];
    expect(new Set(spellings.map(displayLabel)).size).toBe(1);
  });

  it("leaves a word that carries its own capitals alone", () => {
    // Lowercasing these loses what they are: a marker, an acronym, a strategy
    // name. "Cd45+ cells" and "Rna-seq" are what a blind rule produces.
    expect(displayLabel("CD45+ cells")).toBe("CD45+ cells");
    expect(displayLabel("PBMC")).toBe("PBMC");
    expect(displayLabel("HIV Infections")).toBe("HIV infections");
    expect(displayLabel("RNA-Seq")).toBe("RNA-Seq");
    expect(displayLabel("COVID-19")).toBe("COVID-19");
    expect(displayLabel("mononuclear cells (MNCs)")).toBe(
      "Mononuclear cells (MNCs)",
    );
    expect(
      displayLabel("sorted primary pancreatic epithelial cells (mKate2+)"),
    ).toBe("Sorted primary pancreatic epithelial cells (mKate2+)");
  });

  it("writes a brand the way its owner does", () => {
    // The SRA records the platform as ILLUMINA; the company is Illumina. A word
    // in full capitals is otherwise read as an acronym and kept.
    expect(displayLabel("ILLUMINA")).toBe("Illumina");
    expect(displayLabel("Illumina NovaSeq 6000")).toBe("Illumina NovaSeq 6000");
  });

  it("leaves a controlled vocabulary in the case that vocabulary uses", () => {
    expect(displayLabel("TRANSCRIPTOMIC")).toBe("TRANSCRIPTOMIC");
  });

  it("capitalises the first letter even when it is not the first character", () => {
    expect(displayLabel("(renal tumor)")).toBe("(Renal tumor)");
    expect(displayLabel("Kidney Neoplasm (Renal Tumor)")).toBe(
      "Kidney neoplasm (renal tumor)",
    );
  });

  it("keeps a person's name capitalised wherever it sits", () => {
    // Sentence case cannot tell "Parkinson Disease" from "Bone Marrow" — both
    // are two capitalised words — so without a list the man's name becomes a
    // common noun. The corpus holds Brodmann, Parkinson and Wilms.
    expect(displayLabel("Parkinson Disease")).toBe("Parkinson disease");
    expect(displayLabel("Wilms Tumor")).toBe("Wilms tumor");
    expect(displayLabel("Brodmann Area 38")).toBe("Brodmann area 38");
    expect(displayLabel("Idiopathic Parkinson's disease (IPD)")).toBe(
      "Idiopathic Parkinson's disease (IPD)",
    );
  });

  it("keeps a species binomial as written", () => {
    expect(displayLabel("Homo sapiens")).toBe("Homo sapiens");
    expect(displayLabel("Mus musculus")).toBe("Mus musculus");
  });

  it("handles absence without inventing a value", () => {
    expect(displayLabel(null)).toBe("");
    expect(displayLabel(undefined)).toBe("");
    expect(displayLabel("")).toBe("");
  });
});

describe("displayCellType", () => {
  it("opens a slash between two words", () => {
    expect(displayCellType("Hematopoietic stem/progenitor cells from BM")).toBe(
      "Hematopoietic stem progenitor cells from BM",
    );
    expect(displayCellType("Fibroblasts and pericytes/vSMCs")).toBe(
      "Fibroblasts and pericytes vSMCs",
    );
  });

  it("keeps a slash that belongs to a marker name", () => {
    // F4/80 is an antigen, not a choice between F4 and 80.
    expect(
      displayCellType(
        "Non-parenchymal liver cells enriched for F4/80 positive macrophages",
      ),
    ).toBe("Non-parenchymal liver cells enriched for F4/80 positive macrophages");
    expect(
      displayCellType("7AAD-CD45+Ly6G-CD11b int F4/80 hi monocytes"),
    ).toBe("7AAD-CD45+Ly6G-CD11b int F4/80 hi monocytes");
  });

  it("still applies the casing rule", () => {
    expect(displayCellType("Interstitual Cells")).toBe("Interstitual cells");
  });
});
