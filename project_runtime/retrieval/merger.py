"""RetrievalMerger — normalize + deduplicate + fuse provider results (08-规范)."""

from __future__ import annotations


class RetrievalMerger:
    """Fuses results from multiple retrievers.

    Normalization: provider results → common shape.
    Dedup: same (source, source_id, version) kept once (best score wins);
           ripgrep path matches dedupe by (path, line).
    Fusion: FTS5/BM25 results ranked first (scored), then path, then ripgrep
    (code context is qualitative — appended after ranked results).
    """

    def merge(self, *result_lists: list[dict]) -> list[dict]:
        seen_repo: dict[tuple, dict] = {}
        seen_files: set[tuple] = set()
        ordered: list[dict] = []

        def _key(r: dict) -> tuple | None:
            if r.get("source") == "FILE":
                return None  # ripgrep — dedupe separately
            return (r.get("source"), r.get("source_id"), r.get("version"))

        # Priority: scored providers first (fts5 has bm25 score), then path,
        # then ripgrep — the caller passes lists in that order.
        for lst in result_lists:
            for r in lst:
                if r.get("source") == "FILE":
                    fk = (r.get("path"), r.get("line"))
                    if fk in seen_files:
                        continue
                    seen_files.add(fk)
                    ordered.append(r)
                    continue
                k = _key(r)
                if k is None:
                    ordered.append(r)
                    continue
                if k in seen_repo:
                    prev = seen_repo[k]
                    # keep the better (lower bm25 = better) score
                    if r.get("score", 1e9) < prev.get("score", 1e9):
                        ordered[ordered.index(prev)] = r
                        seen_repo[k] = r
                    continue
                seen_repo[k] = r
                ordered.append(r)
        return ordered
