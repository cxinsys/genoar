# Public Release Checklist

GENOAR is being prepared for public use. Before a public release is tagged,
the maintainers will close these release gates:

- [x] State the automated end-to-end scope: human transcriptomic samples with
  a GRCh38 Stage 3 reference.
- [x] `LICENSE`: MIT, chosen by the corresponding authors (2026-09-22).
- [x] UMLS-derived CSV files: reviewed against the UMLS licence
  (2026-09-22). The corresponding authors' decision is to keep them in the
  repository as the project's processed subset (not raw Metathesaurus data).
  The NLM acknowledgement and notice are in the READMEs and the dashboard
  footer.
- [x] Citation metadata: `CITATION.cff` and the README Citation section carry
  the author list and title of the article under review at Nucleic Acids
  Research (manuscript NAR-03601-2026). Still to add: the article DOI once
  published.
- [ ] Tag a release and deposit it on Zenodo (deferred by the corresponding
  authors, 2026-09-22). The journal's data policy asks for software in a
  repository that assigns a permanent DOI, with the DOI in the article's
  Data Availability statement. When done, put the DOI in `CITATION.cff`
  (`doi`, `version`, `date-released`) and in the README Citation section.
- [x] The web application (`genoar_rag/`) is part of the public release; it
  is the service at genoar.ai and has its own Compose project. The root
  pipeline Docker context still excludes it, on purpose.
- [ ] Add a clean-clone public smoke test to CI covering the documented
  Stage 1 -> Stage 2 -> bounded download -> Stage 3 command seam.

This checklist records decisions still to be made; it does not itself grant a
license or determine third-party redistribution rights.
