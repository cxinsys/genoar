#!/usr/bin/env python3
"""
[사용자용] GENOAR에서 내려받은 SRR별 h5를, 같은 10x 라이브러리끼리 합친다.
[EN] [For users] Merge per-SRR h5 files downloaded from GENOAR that belong to the
same 10x library.

왜 필요한가 / [EN] Why:
  GEO 샘플(GSM) 하나가 여러 SRR로 나뉘어 올라온 경우가 많다. 그중 상당수는
  같은 10x 라이브러리를 여러 lane/run으로 시퀀싱한 것이라, SRR마다 같은 세포가
  들어 있다. 이런 SRR을 각각 샘플로 쓰면 세포가 중복 집계된다.
  반대로 같은 GSM이어도 서로 다른 10x channel(GEM well)에서 나온 SRR은 세포가
  다르므로 합치면 안 된다.
  [EN] Many GEO samples (GSMs) are split across several SRRs. Often these are the
  same 10x library sequenced on several lanes/runs, so each SRR contains the same
  cells; using them as separate samples counts those cells twice. Conversely, SRRs
  of the same GSM that come from different 10x channels (GEM wells) contain
  different cells and must not be merged.

판정 방법 / [EN] How SRRs are grouped:
  같은 GSM 안에서 SRR별 filtered cell barcode의 겹침을 계산한다.
  [EN] Within each GSM, compute the overlap of filtered cell barcodes between SRRs.
    겹침 = |A∩B| / min(|A|,|B|)   / [EN] overlap = |A∩B| / min(|A|,|B|)
    >= 0.5 : 같은 라이브러리 -> 합침 / [EN] same library -> merged
    <  0.1 : 다른 channel    -> 각각 따로 / [EN] different channel -> kept separate
    그 사이: ambiguous       -> 기본 제외 / [EN] in between: ambiguous -> skipped by default
  메타 테이블에 library_group 컬럼이 있으면 판정 대신 그 값을 쓴다.
  [EN] If the metadata already has a library_group column, it is used instead.

내려받은 SRR만 처리한다. 사용자가 read 수 등으로 일부 SRR을 제외했다면 남은 SRR끼리
판정하고 합친다 (제외된 SRR은 리포트에 기록).
[EN] Only downloaded SRRs are processed. If the user excluded some SRRs (e.g. by read
count), grouping and merging use the remaining SRRs (excluded ones are logged).

합치는 방식 / [EN] Merge method:
  - --raw 를 주고 raw h5가 모두 있으면: raw를 더한 뒤 각 SRR에서 세포로 판정된
    barcode의 합집합을 세포로 사용 (한쪽 lane에서만 잡힌 세포의 UMI까지 회수)
    [EN] With --raw and all raw h5 present: sum raw matrices, then keep the union of
    barcodes called as cells in any SRR (recovers UMIs of cells called in one lane only)
  - 아니면 filtered끼리 더함 (실용적으로 충분)
    [EN] Otherwise filtered matrices are summed (sufficient in practice)
  - lane 간 UMI 중복 제거는 복원되지 않는다. 고발현 유전자가 약간 과대 집계될 수 있다.
    [EN] UMI deduplication across lanes is not recovered; highly expressed genes may be
    slightly over-counted.

출력 / [EN] Output:
  {out}/{library_group}.h5  (Cell Ranger 형식, Read10X_h5 / sc.read_10x_h5 로 읽힘)
                            [EN] Cell Ranger format, readable with Read10X_h5 / sc.read_10x_h5
      library_group 이름: GSM에 그룹이 하나면 GSM, 여러 개면 GSM_g1, GSM_g2 ...
      [EN] Naming: GSM if one group, otherwise GSM_g1, GSM_g2 ...
  {out}/merge_report.csv      그룹별 처리 결과 / [EN] per-group result
  {out}/pairwise_overlap.csv  SRR 쌍별 겹침 / [EN] pairwise SRR overlap

사용 예 / [EN] Usage:
  python genoar_merge_library_groups.py \\
    --meta HS_tis_dis_cell_merged_meta_table_filtered.csv \\
    --h5 "./downloads/{run}_filtered_feature_bc_matrix.h5" \\
    --out merged/
  # 특정 study / GSM만 / [EN] only selected studies / GSMs:
  #   --series GSE123456 GSE234567   --gsm GSM4096123
  # 판정만 하고 파일은 만들지 않기 / [EN] grouping report only:
  #   --dry-run
"""
import argparse
import os
import shutil
from itertools import combinations

import h5py
import numpy as np
import pandas as pd
import scanpy as sc
import scipy.sparse as sp

HI, LO = 0.5, 0.1


# ---------- 판정 / [EN] grouping ----------

def read_barcodes(path):
    """h5에서 barcode만 읽는다 (Cell Ranger v3+ 및 v2 형식 지원).
    [EN] Read only barcodes from an h5 (supports Cell Ranger v3+ and legacy v2 layouts)."""
    with h5py.File(path, "r") as f:
        grp = f["matrix"] if "matrix" in f else f[list(f.keys())[0]]  # v2: 최상위가 genome 이름 / [EN] v2: top level is the genome name
        bcs = grp["barcodes"][:]
    # suffix("-1") 제거해 SRR 간 비교 / [EN] strip the "-1" suffix to compare across SRRs
    return {(b.decode() if isinstance(b, bytes) else str(b)).split("-")[0] for b in bcs}


def group_gsm(gsm, runs, paths):
    """한 GSM의 SRR들을 겹침으로 묶는다 (union-find).
    [EN] Group one GSM's SRRs by barcode overlap (union-find)."""
    runs = sorted(runs)
    if len(runs) == 1:
        return {runs[0]: (gsm, "single")}, []
    bc = {r: read_barcodes(paths[r]) for r in runs}
    parent = {r: r for r in runs}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ov, pairs = {}, []
    for x, y in combinations(runs, 2):
        o = len(bc[x] & bc[y]) / max(1, min(len(bc[x]), len(bc[y])))
        ov[(x, y)] = o
        pairs.append(dict(GSM=gsm, SRR_1=x, SRR_2=y, n_cells_1=len(bc[x]), n_cells_2=len(bc[y]),
                          overlap=round(o, 4),
                          call="same_library" if o >= HI else ("diff_channel" if o < LO else "ambiguous")))
        if o >= HI:
            parent[find(x)] = find(y)
    groups = {}
    for r in runs:
        groups.setdefault(find(r), []).append(r)
    groups = sorted(groups.values(), key=lambda g: g[0])  # 결정적 순서 / [EN] deterministic order

    out = {}
    for i, g in enumerate(groups, 1):
        name = gsm if len(groups) == 1 else f"{gsm}_g{i}"
        if len(g) > 1:
            status = "lane_split"
        else:
            mine = [o for (x, y), o in ov.items() if g[0] in (x, y)]
            status = "ambiguous" if any(o >= LO for o in mine) else "separate_channel"
        for r in g:
            out[r] = (name, status)
    return out, pairs


# ---------- 합치기 / [EN] merging ----------

def load(path):
    a = sc.read_10x_h5(path, gex_only=True)
    a.var_names_make_unique()
    a.obs_names = [b.split("-")[0] for b in a.obs_names]  # suffix 제거 / [EN] strip suffix
    return a


def sum_by_barcode(adatas, keep=None):
    """barcode가 같은 세포끼리 count를 더한다. / [EN] Sum counts of identical barcodes."""
    genes = adatas[0].var_names
    for a in adatas[1:]:
        if not a.var_names.equals(genes):
            genes = genes.intersection(a.var_names)  # reference가 다르면 교집합 / [EN] shared genes if references differ
    bcs = sorted(set().union(*[set(a.obs_names) for a in adatas]))
    if keep is not None:
        bcs = [b for b in bcs if b in keep]
    idx = pd.Index(bcs)
    X = sp.csr_matrix((len(bcs), len(genes)), dtype=np.float32)
    for a in adatas:
        a = a[a.obs_names.isin(idx), genes]
        rows = idx.get_indexer(a.obs_names)
        P = sp.csr_matrix((np.ones(len(rows)), (rows, np.arange(len(rows)))),
                          shape=(len(bcs), len(rows)))
        X = X + P @ sp.csr_matrix(a.X)
    var = adatas[0][:, genes].var.copy()
    return sp.csr_matrix(X), bcs, var


def write_10x_h5(path, X, barcodes, var):
    """Cell Ranger v3 형식 h5로 저장 (cells x genes CSR = genes x cells CSC).
    [EN] Write a Cell Ranger v3-format h5 (cells x genes CSR == genes x cells CSC)."""
    X = sp.csr_matrix(X)
    X.sort_indices()
    n = len(var)
    gid = var["gene_ids"].astype(str).values if "gene_ids" in var else var.index.astype(str).values
    ftype = var["feature_types"].astype(str).values if "feature_types" in var else np.repeat("Gene Expression", n)
    genome = var["genome"].astype(str).values if "genome" in var else np.repeat("", n)
    enc = lambda v: np.array([s.encode() for s in v])
    with h5py.File(path, "w") as f:
        m = f.create_group("matrix")
        m.create_dataset("barcodes", data=enc([b + "-1" for b in barcodes]))
        m.create_dataset("data", data=np.rint(X.data).astype(np.int32), compression="gzip")
        m.create_dataset("indices", data=X.indices.astype(np.int64), compression="gzip")
        m.create_dataset("indptr", data=X.indptr.astype(np.int64))
        m.create_dataset("shape", data=np.array([n, X.shape[0]], dtype=np.int32))
        ft = m.create_group("features")
        ft.create_dataset("id", data=enc(gid))
        ft.create_dataset("name", data=enc(var.index.astype(str)))
        ft.create_dataset("feature_type", data=enc(ftype))
        ft.create_dataset("genome", data=enc(genome))
        ft.create_dataset("_all_tag_keys", data=np.array([b"genome"]))


# ---------- main ----------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--meta", required=True, help="GENOAR 메타 테이블 (Run, Sample Name 컬럼) / [EN] GENOAR metadata (Run, Sample Name)")
    p.add_argument("--h5", required=True, help="filtered h5 경로 템플릿, {run} 포함 / [EN] filtered h5 path template with {run}")
    p.add_argument("--raw", default=None, help="raw h5 경로 템플릿 (선택) / [EN] raw h5 path template (optional)")
    p.add_argument("--out", required=True)
    p.add_argument("--gsm-col", default="Sample Name")
    p.add_argument("--series", nargs="*", help="이 GSE만 처리 / [EN] process only these GSEs")
    p.add_argument("--gsm", nargs="*", help="이 GSM만 처리 / [EN] process only these GSMs")
    p.add_argument("--include-ambiguous", action="store_true",
                   help="ambiguous SRR도 단독으로 복사 / [EN] also copy ambiguous SRRs as single units")
    p.add_argument("--dry-run", action="store_true", help="판정 리포트만 작성 / [EN] write the grouping report only")
    a = p.parse_args()
    os.makedirs(a.out, exist_ok=True)

    meta = pd.read_csv(a.meta).drop_duplicates("Run")  # Run이 여러 행이면 하나로 / [EN] one row per Run
    if a.series:
        meta = meta[meta["Series"].isin(a.series)]
    if a.gsm:
        meta = meta[meta[a.gsm_col].isin(a.gsm)]
    meta = meta.assign(_path=[a.h5.format(run=r) for r in meta["Run"]])
    meta = meta.assign(_have=meta["_path"].map(os.path.exists))
    not_dl = meta[~meta["_have"]].groupby(a.gsm_col)["Run"].apply(lambda s: ";".join(sorted(s)))
    meta = meta[meta["_have"]]
    paths = dict(zip(meta["Run"], meta["_path"]))

    # 그룹 판정 (메타에 library_group이 있으면 그대로 사용) / [EN] grouping (use library_group if present)
    assign, pair_rows = {}, []
    if "library_group" in meta and meta["library_group"].notna().all():
        for lg, g in meta.groupby("library_group"):
            st = g["group_status"].iloc[0] if "group_status" in g else ("lane_split" if len(g) > 1 else "single")
            for r in g["Run"]:
                assign[r] = (lg, st)
    else:
        for gsm, g in meta.groupby(a.gsm_col):
            res, pairs = group_gsm(gsm, list(g["Run"]), paths)
            assign.update(res)
            pair_rows += pairs
    meta = meta.assign(library_group=meta["Run"].map(lambda r: assign[r][0]),
                       group_status=meta["Run"].map(lambda r: assign[r][1]))

    report = []
    for lg, g in meta.groupby("library_group"):
        gsm, status, runs = g[a.gsm_col].iloc[0], g["group_status"].iloc[0], sorted(g["Run"])
        rec = dict(library_group=lg, GSM=gsm, Series=g["Series"].iloc[0] if "Series" in g else "",
                   group_status=status, SRRs=";".join(runs), action="", n_cells=np.nan,
                   GSM_SRRs_not_downloaded=not_dl.get(gsm, ""), note="")
        dst = os.path.join(a.out, f"{lg}.h5")

        if status == "ambiguous" and not a.include_ambiguous:
            rec["action"] = "excluded"
            rec["note"] = "ambiguous: pairwise_overlap.csv 확인 / [EN] check pairwise_overlap.csv"
        elif a.dry_run:
            rec["action"] = "dry_run"
        elif status == "lane_split":
            filt = [load(paths[r]) for r in runs]
            called = set().union(*[set(x.obs_names) for x in filt])
            raw_paths = [a.raw.format(run=r) for r in runs] if a.raw else []
            if raw_paths and all(os.path.exists(x) for x in raw_paths):
                X, bcs, var = sum_by_barcode([load(x) for x in raw_paths], keep=called)
                rec["action"] = "merged_raw"
            else:
                X, bcs, var = sum_by_barcode(filt)
                rec["action"] = "merged_filtered"
            write_10x_h5(dst, X, bcs, var)
            rec["n_cells"] = len(bcs)
            rec["note"] = "cells per SRR: " + ";".join(str(x.n_obs) for x in filt)
        else:
            shutil.copyfile(paths[runs[0]], dst)  # 단독 SRR은 그대로 / [EN] single SRR copied as is
            rec["action"] = "copied"
            with h5py.File(dst, "r") as f:
                k = "matrix" if "matrix" in f else list(f.keys())[0]
                rec["n_cells"] = len(f[k]["barcodes"])
        if rec["GSM_SRRs_not_downloaded"]:
            print(f"[INFO] {gsm}: 내려받지 않은 SRR 제외 / [EN] not downloaded: {rec['GSM_SRRs_not_downloaded']}", flush=True)
        report.append(rec)
        print(lg, status, rec["action"], rec["SRRs"], flush=True)

    rep = pd.DataFrame(report)
    rep.to_csv(os.path.join(a.out, "merge_report.csv"), index=False)
    pd.DataFrame(pair_rows).to_csv(os.path.join(a.out, "pairwise_overlap.csv"), index=False)
    print("\n" + rep["group_status"].value_counts().to_string())


if __name__ == "__main__":
    main()
