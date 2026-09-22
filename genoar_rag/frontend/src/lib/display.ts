/** How a curated value is written on screen.
 *
 * The curated tables are submitters' text, so the same thing arrives spelled
 * several ways — "Bone Marrow", "Bone marrow" and "bone marrow" are all in
 * there. The filters already fold those together when counting; this is how the
 * one they fold to is written.
 *
 * The rule is sentence case: first letter up, the rest down. Applied blindly it
 * would also write "Cd45+ cells", "Pbmc", "Hiv infections" and "Rna-seq", so it
 * is applied per word and a word that carries capitals of its own is left
 * alone. Nothing here changes what is stored or what a filter sends — only what
 * is read.
 */

/** Names their owners write a particular way, which is not the way this file
 *  would work out from the letters.
 *
 *  `ILLUMINA` is how the SRA records the platform and `Illumina` is how the
 *  company writes itself; a word in full capitals is otherwise taken for an
 *  acronym and left as it is, so it needs saying. Keyed lowercase. */
const BRAND_CASING: Record<string, string> = {
  illumina: "Illumina",
};

/** People's names, which keep their capital wherever they sit in a phrase.
 *
 * Sentence case has no way to tell "Parkinson Disease" from "Bone Marrow" —
 * both are two capitalised words — so it writes "Parkinson disease", and the
 * name of the man it is called after becomes a common noun. The corpus holds
 * three: Brodmann, Parkinson, Wilms. The rest are here because a curated table
 * that grows will meet them, and a list is cheaper to extend than a rule that
 * guesses.
 *
 * Compared against the word's letters only, so `Parkinson's` matches.
 */
const PROPER_NOUNS = new Set([
  "alzheimer", "alzheimers", "barrett", "barretts", "brodmann", "burkitt",
  "crohn", "crohns", "cushing", "cushings", "duchenne", "ewing", "graves",
  "hodgkin", "hodgkins", "huntington", "huntingtons", "kaposi", "kupffer",
  "langerhans", "lewy", "marfan", "merkel", "paneth", "parkinson", "parkinsons",
  "purkinje", "schwann", "sjogren", "sjogrens", "sjögren", "sjögrens", "turner",
  "wilms",
]);

/** Whether a word carries meaning in its capitals that lowercasing would lose.
 *
 * True when a capital appears anywhere but the first letter — `RNA-Seq`,
 * `PBMC`, `CD45+`, `mKate2+`, `vSMCs`, `COVID-19` — and for anything holding a
 * digit. False for an ordinary word that merely starts a sentence or was typed
 * with a capital, which is exactly what this is here to normalise: `Marrow`,
 * `Cells`, `Groups`.
 */
function carriesItsOwnCase(word: string): boolean {
  // Strip the punctuation a value arrives wrapped in — commas, parentheses,
  // quotes — so the two tests below see the word itself. The trailing class
  // spares `+` so `core` is the marker as written (`CD45+`); no verdict below
  // turns on it, since neither test asks about `+`.
  const core = word.replace(/^[^\p{L}\p{N}]+|[^\p{L}\p{N}+]+$/gu, "");
  if (!core) return true; // punctuation only — nothing to case
  if (/\d/.test(core)) return true;
  return /[A-Z]/.test(core.slice(1));
}

/** A curated value as it should be read: sentence case, names intact. */
export function displayLabel(value: string | null | undefined): string {
  if (!value) return "";
  const words = value.trim().replace(/\s+/g, " ").split(" ");
  const cased = words.map((word) => {
    const brand = BRAND_CASING[word.toLowerCase()];
    if (brand) return brand;
    if (carriesItsOwnCase(word)) return word;
    const letters = word.replace(/[^\p{L}]/gu, "").toLowerCase();
    if (PROPER_NOUNS.has(letters)) return capitaliseFirstLetter(word);
    return word.toLowerCase();
  });
  return capitaliseFirstLetter(cased.join(" "));
}

/** Upper-case the first letter, wherever it is — "(renal tumor)" opens on a
 *  bracket, and the letter that needs raising is the one after it. */
function capitaliseFirstLetter(text: string): string {
  const first = text.search(/\p{L}/u);
  if (first === -1) return text;
  return text.slice(0, first) + text[first].toUpperCase() + text.slice(first + 1);
}

/** A cell type, with the slashes that separate alternatives opened out.
 *
 * "Hematopoietic stem/progenitor cells" reads as two words run together. But a
 * slash is also part of some marker names — `F4/80` is an antigen, not a choice
 * between F4 and 80 — so only slashes between plain words are opened. A slash
 * with a digit on either side is left where it is.
 *
 * Four of the 310 cell types contain one; two of those four are `F4/80`.
 */
export function displayCellType(value: string | null | undefined): string {
  if (!value) return "";
  const opened = value.replace(
    /([\p{L}]+)\/([\p{L}]+)/gu,
    (match, left: string, right: string) =>
      /\d/.test(left) || /\d/.test(right) ? match : `${left} ${right}`,
  );
  return displayLabel(opened);
}
