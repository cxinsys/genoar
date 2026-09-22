/** The accession in a string, or null if it does not look like one.
 *
 * An accession is a lookup, not a search, and the two need telling apart before
 * anything is done with the text. Asking the AI Librarian for SRR10079362
 * returns a page of unrelated samples at around 0.57: the embedding model reads
 * an identifier as meaningless subwords, so the query vector points nowhere in
 * particular and everything in the corpus is equally far from it. The string is
 * in the indexed text — searching by meaning is simply not searching by string.
 *
 * SRR, ERR and DRR are the archives' run ids; GSM is a GEO sample. Both resolve
 * on the sample route, which is where a match is sent.
 */
const ACCESSION = /^(SRR|ERR|DRR|GSM)\d+$/i;

export function looksLikeAccession(value: string): string | null {
  const trimmed = value.trim();
  return ACCESSION.test(trimmed) ? trimmed.toUpperCase() : null;
}
