#!/usr/bin/env python3
"""Build a per-species FAISS index from the DB, for a given embedding model.

The repo ships a FAISS index that was built with PubMedBERT. The paper design uses
SapBERT (human) and MiniLLM (mouse), so switching the model means re-embedding. The
source text for every sample is stored verbatim in the `sra_vectors.embedding_text`
column (with `vector_index` giving the original FAISS order), so this script re-encodes
that exact text with a new model — no guessing about how the document was composed.

It writes three files that VectorService expects:
    <out-index>              FAISS IndexFlatIP (cosine via normalized inner product)
    <out-mapping>            JSON list of run_ids, aligned to the index rows
    <out-index>.meta.json    build-model sidecar {model_name, dim, count, organisms}

The sidecar lets the backend refuse to query an index with the wrong model.

Examples
--------
    # Human index with SapBERT
    build_faiss_index.py --db ../data/sra_hybrid.db \
        --model cambridgeltl/SapBERT-from-PubMedBERT-fulltext \
        --organism "Homo sapiens" \
        --out-index ../data/faiss_human.bin --out-mapping ../data/run_mapping_human.json

    # Mouse index with MiniLLM (once the exact HF repo is confirmed)
    build_faiss_index.py --db ../data/sra_hybrid.db --model <mouse-model-id> \
        --organism "Mus musculus" \
        --out-index ../data/faiss_mouse.bin --out-mapping ../data/run_mapping_mouse.json

    # Tiny smoke test without downloading a model (uses a random encoder)
    build_faiss_index.py --db ../data/sra_hybrid.db --organism "Mus musculus" \
        --limit 50 --fake-encoder --out-index /tmp/idx.bin --out-mapping /tmp/map.json
"""

import argparse
import json
import sqlite3
from pathlib import Path

import numpy as np


def load_documents(
    conn: sqlite3.Connection,
    organisms: list[str] | None = None,
    limit: int | None = None,
) -> tuple[list[str], list[str]]:
    """Return (run_ids, texts) from sra_vectors, in original vector_index order.

    If `organisms` is given, restrict to runs whose sra_extended Organism is in the
    list (this is how per-species indexes are carved out of the shared corpus).
    """
    params: list = []
    where = ""
    if organisms:
        placeholders = ",".join("?" for _ in organisms)
        where = (
            " WHERE v.Run IN ("
            "   SELECT Run FROM sra_extended"
            f"   WHERE field_name = 'Organism' AND field_value IN ({placeholders})"
            " )"
        )
        params.extend(organisms)
    sql = (
        "SELECT v.Run, v.embedding_text FROM sra_vectors v"
        f"{where} ORDER BY v.vector_index"
    )
    if limit is not None:
        sql += " LIMIT ?"
        params.append(limit)

    run_ids: list[str] = []
    texts: list[str] = []
    for run, text in conn.execute(sql, params):
        run_ids.append(run)
        texts.append(text or "")
    return run_ids, texts


def encode_texts(
    model_name: str,
    texts: list[str],
    cache_dir: str | None,
    batch_size: int,
) -> np.ndarray:
    """Encode texts into L2-normalized float32 vectors with SentenceTransformer."""
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name, cache_folder=cache_dir)
    vectors = model.encode(
        texts,
        batch_size=batch_size,
        normalize_embeddings=True,
        show_progress_bar=True,
        convert_to_numpy=True,
    )
    return np.asarray(vectors, dtype=np.float32)


def fake_encode(texts: list[str], dim: int = 768) -> np.ndarray:
    """Deterministic random encoder for smoke tests (no model download).

    Vectors are derived from a hash of each text so runs are reproducible without
    Date/random state; they are L2-normalized like the real encoder's output.
    """
    vecs = np.empty((len(texts), dim), dtype=np.float32)
    for i, t in enumerate(texts):
        rng = np.random.RandomState(abs(hash(t)) % (2**32))
        vecs[i] = rng.randn(dim).astype(np.float32)
    norms = np.linalg.norm(vecs, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return vecs / norms


def write_index(
    vectors: np.ndarray,
    run_ids: list[str],
    out_index: str,
    out_mapping: str,
    model_name: str,
    organisms: list[str] | None,
) -> None:
    """Write the FAISS index, run mapping, and build-model sidecar."""
    import faiss

    if vectors.shape[0] != len(run_ids):
        raise ValueError(
            f"vectors ({vectors.shape[0]}) != run_ids ({len(run_ids)}) — refusing to write"
        )
    dim = int(vectors.shape[1])
    index = faiss.IndexFlatIP(dim)
    index.add(np.ascontiguousarray(vectors, dtype=np.float32))

    Path(out_index).parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, out_index)
    with open(out_mapping, "w") as f:
        json.dump(run_ids, f)
    meta = {
        "model_name": model_name,
        "dim": dim,
        "count": len(run_ids),
        "organisms": organisms or "all",
    }
    with open(out_index + ".meta.json", "w") as f:
        json.dump(meta, f, indent=2)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a per-species FAISS index")
    ap.add_argument("--db", required=True, help="Path to sra_hybrid.db")
    ap.add_argument(
        "--model",
        default="cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
        help="HuggingFace embedding model id (default: SapBERT)",
    )
    ap.add_argument(
        "--organism",
        action="append",
        default=None,
        help="Restrict to this organism (repeatable). Omit to index all samples.",
    )
    ap.add_argument("--out-index", required=True)
    ap.add_argument("--out-mapping", required=True)
    ap.add_argument("--cache-dir", default=None, help="HuggingFace model cache dir")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--limit", type=int, default=None, help="Cap rows (for testing)")
    ap.add_argument(
        "--fake-encoder",
        action="store_true",
        help="Use a deterministic random encoder (smoke test; no model download)",
    )
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        run_ids, texts = load_documents(conn, args.organism, args.limit)
    finally:
        conn.close()
    if not run_ids:
        raise SystemExit("No documents matched — nothing to index.")

    print(f"[build] {len(run_ids)} documents; organism={args.organism or 'all'}")
    if args.fake_encoder:
        print("[build] WARNING: using fake encoder (smoke test only)")
        vectors = fake_encode(texts)
        model_label = f"FAKE::{args.model}"
    else:
        print(f"[build] encoding with {args.model} ...")
        vectors = encode_texts(args.model, texts, args.cache_dir, args.batch_size)
        model_label = args.model

    write_index(vectors, run_ids, args.out_index, args.out_mapping, model_label, args.organism)
    print(
        f"[build] wrote {args.out_index} (dim={vectors.shape[1]}, n={len(run_ids)}), "
        f"{args.out_mapping}, and {args.out_index}.meta.json"
    )


if __name__ == "__main__":
    main()
