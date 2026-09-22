import { looksLikeAccession } from "@/lib/accession";

describe("looksLikeAccession", () => {
  it.each(["SRR10079362", "ERR1234567", "DRR000001", "GSM4065391"])(
    "recognises %s",
    (value) => {
      expect(looksLikeAccession(value)).toBe(value);
    },
  );

  it("normalises case and surrounding space, because both get pasted", () => {
    expect(looksLikeAccession("  srr10079362 ")).toBe("SRR10079362");
  });

  it.each([
    ["yolk sac", "a description"],
    ["SRR", "a prefix with no number"],
    ["10079362", "a number with no prefix"],
    ["SRR10079362 yolk sac", "an accession inside a longer query"],
    ["", "nothing"],
  ])("rejects %s (%s)", (value) => {
    expect(looksLikeAccession(value)).toBeNull();
  });

  it("does not claim an accession that only starts like one", () => {
    // The search page navigates on a match, so a false positive is a page of
    // "not found" in place of the search someone asked for.
    expect(looksLikeAccession("SRRNA-seq")).toBeNull();
  });
});
