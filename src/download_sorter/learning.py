"""Lightweight rule-learning tally and local statistical route learner."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any

from download_sorter.config import Config
from download_sorter.dest import to_relative_dest
from download_sorter.router import _normalize_tokens, extract_host

_LEARN_STOPWORDS: frozenset[str] = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "from",
        "file",
        "files",
        "copy",
        "new",
        "final",
        "draft",
        "download",
        "downloads",
        "untitled",
        "document",
        "img",
        "image",
        "screenshot",
        "screen",
        "shot",
        "pdf",
        "png",
        "jpg",
        "jpeg",
        "zip",
    }
)


def _extract_filename_tokens(filename: str) -> list[str]:
    stem = Path(filename).stem if filename else ""
    norm = _normalize_tokens(stem)
    out: list[str] = []
    seen: set[str] = set()
    for tok in norm.split():
        if len(tok) >= 3 and not tok.isdigit() and tok not in _LEARN_STOPWORDS and tok not in seen:
            seen.add(tok)
            out.append(tok)
    return out


def _store_path(data_dir: Path) -> Path:
    return data_dir / "learned_routes.json"


def _load_store(data_dir: Path) -> dict[str, Any]:
    path = _store_path(data_dir)
    if not path.is_file():
        return {"tallies": {}, "dismissed": [], "samples": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("tallies", {})
            data.setdefault("dismissed", [])
            data.setdefault("samples", [])
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"tallies": {}, "dismissed": [], "samples": []}


def _save_store(data_dir: Path, data: dict[str, Any]) -> None:
    path = _store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def record_manual_route(
    *,
    destination: str,
    data_dir: Path,
    source_url: str = "",
    host: str = "",
    ext: str = "",
    filename: str = "",
) -> None:
    """Record a manual file move/re-route so repeated patterns can suggest a rule."""
    clean_dest = to_relative_dest(destination, fallback="")
    if not clean_dest:
        return
    clean_host = extract_host(host or source_url)
    clean_ext = ext.strip().lower()
    if not clean_ext and filename:
        clean_ext = Path(filename).suffix.strip().lower()
    if clean_ext and not clean_ext.startswith("."):
        clean_ext = "." + clean_ext
    tokens = _extract_filename_tokens(filename)

    store = _load_store(data_dir)
    tallies: dict[str, dict[str, Any]] = store["tallies"]

    if clean_host:
        key = f"host:{clean_host}->{clean_dest.lower()}"
        entry = tallies.get(key) or {
            "kind": "host",
            "host": clean_host,
            "ext": "",
            "keyword": "",
            "destination": clean_dest,
            "count": 0,
        }
        entry["count"] = int(entry.get("count", 0)) + 1
        tallies[key] = entry
    elif clean_ext and not tokens:
        key = f"ext:{clean_ext}->{clean_dest.lower()}"
        entry = tallies.get(key) or {
            "kind": "ext",
            "host": "",
            "ext": clean_ext,
            "keyword": "",
            "destination": clean_dest,
            "count": 0,
        }
        entry["count"] = int(entry.get("count", 0)) + 1
        tallies[key] = entry

    for tok in tokens[:6]:
        key = f"kw:{tok}->{clean_dest.lower()}"
        entry = tallies.get(key) or {
            "kind": "keyword",
            "host": "",
            "ext": "",
            "keyword": tok,
            "destination": clean_dest,
            "count": 0,
        }
        entry["count"] = int(entry.get("count", 0)) + 1
        tallies[key] = entry

    if clean_host or clean_ext or tokens:
        samples: list[dict[str, Any]] = list(store.get("samples") or [])
        samples.append(
            {
                "destination": clean_dest,
                "host": clean_host,
                "ext": clean_ext,
                "tokens": tokens[:10],
            }
        )
        # Keep the newest 500 manual samples to bound file size
        store["samples"] = samples[-500:]

    _save_store(data_dir, store)


def get_rule_suggestions(
    cfg: Config,
    data_dir: Path,
    *,
    threshold: int = 2,
) -> list[dict[str, Any]]:
    """Return learned rule suggestions that meet ``threshold`` and aren't already rules."""
    store = _load_store(data_dir)
    dismissed = set(store.get("dismissed") or [])
    tallies: dict[str, dict[str, Any]] = store.get("tallies") or {}

    suggestions: list[dict[str, Any]] = []
    for key, entry in tallies.items():
        if key in dismissed:
            continue
        count = int(entry.get("count", 0))
        if count < threshold:
            continue
        dest = to_relative_dest(str(entry.get("destination") or ""), fallback="")
        if not dest:
            continue
        host = str(entry.get("host") or "").strip().lower()
        ext = str(entry.get("ext") or "").strip().lower()
        keyword = str(entry.get("keyword") or "").strip().lower()

        already_covered = any(
            r.enabled
            and to_relative_dest(r.destination, fallback="").lower() == dest.lower()
            and (
                (host and host in [extract_host(d) for d in r.domains])
                or (ext and ext in r.extensions)
                or (keyword and keyword in [k.lower() for k in r.keywords])
            )
            for r in cfg.rules
        )
        if already_covered:
            continue

        if host:
            label = f"files from {host}"
            kind = "host"
        elif keyword:
            label = f"files with '{keyword}'"
            kind = "keyword"
        else:
            label = f"{ext} files"
            kind = "ext"
        suggestions.append(
            {
                "key": key,
                "kind": entry.get("kind", kind),
                "host": host,
                "ext": ext,
                "keyword": keyword,
                "destination": dest,
                "count": count,
                "summary": f"You moved {count} {label} to '{dest.replace(chr(92), '/')}'",
            }
        )

    return sorted(suggestions, key=lambda s: s["count"], reverse=True)


def dismiss_suggestion(key: str, data_dir: Path) -> None:
    """Persistently dismiss a learned rule suggestion."""
    store = _load_store(data_dir)
    dismissed = list(store.get("dismissed") or [])
    if key not in dismissed:
        dismissed.append(key)
        store["dismissed"] = dismissed
        _save_store(data_dir, store)


def clear_learned_routes(data_dir: Path) -> None:
    """Reset all locally learned tallies, dismissed keys, and manual samples."""
    _save_store(data_dir, {"tallies": {}, "dismissed": [], "samples": []})


@dataclass(frozen=True)
class RoutePrediction:
    """Explainable local statistical folder prediction."""

    destination: str
    confidence: float
    margin: float
    top_reasons: tuple[str, ...]


class TokenRouteLearner:
    """Pure-Python smoothed Naive Bayes route predictor trained on local move history.

    Zero external ML dependencies, <2 ms per prediction, 100% offline, and
    returns the exact features (filename tokens, host, extension) that drove
    the recommendation.
    """

    def __init__(self, *, alpha: float = 0.75) -> None:
        self.alpha = max(0.05, float(alpha))
        self._class_docs: Counter[str] = Counter()
        self._class_features: dict[str, Counter[str]] = {}
        self._class_total_feats: Counter[str] = Counter()
        self._vocab: set[str] = set()
        self._total_docs = 0

    @staticmethod
    def _featurize(
        *,
        filename: str = "",
        source_url: str = "",
        host: str = "",
        ext: str = "",
        tokens: list[str] | None = None,
    ) -> list[str]:
        feats: list[str] = []
        clean_host = extract_host(host or source_url)
        if clean_host:
            # Host is a strong routing signal (weighted 2x)
            feats.append(f"host:{clean_host}")
            feats.append(f"host:{clean_host}")
        clean_ext = ext.strip().lower()
        if not clean_ext and filename:
            clean_ext = Path(filename).suffix.strip().lower()
        if clean_ext:
            if not clean_ext.startswith("."):
                clean_ext = "." + clean_ext
            feats.append(f"ext:{clean_ext}")
        tok_list = tokens if tokens is not None else _extract_filename_tokens(filename)
        for tok in tok_list:
            feats.append(f"tok:{tok}")
        return feats

    def add_sample(
        self,
        destination: str,
        *,
        filename: str = "",
        source_url: str = "",
        host: str = "",
        ext: str = "",
        tokens: list[str] | None = None,
    ) -> None:
        clean_dest = to_relative_dest(destination, fallback="")
        if not clean_dest:
            return
        feats = self._featurize(
            filename=filename,
            source_url=source_url,
            host=host,
            ext=ext,
            tokens=tokens,
        )
        if not feats:
            return
        self._total_docs += 1
        self._class_docs[clean_dest] += 1
        bucket = self._class_features.setdefault(clean_dest, Counter())
        for f in feats:
            bucket[f] += 1
            self._class_total_feats[clean_dest] += 1
            self._vocab.add(f)

    def train_from_store(self, data_dir: Path) -> int:
        """Load manual route samples from ``learned_routes.json``."""
        store = _load_store(data_dir)
        added = 0
        for sample in store.get("samples") or []:
            if not isinstance(sample, dict):
                continue
            self.add_sample(
                str(sample.get("destination") or ""),
                host=str(sample.get("host") or ""),
                ext=str(sample.get("ext") or ""),
                tokens=list(sample.get("tokens") or []),
            )
            added += 1
        return added

    def train_from_db_and_store(self, db: Any, data_dir: Path, *, limit: int = 200) -> int:
        """Train on manual route samples plus recent rule/domain-sorted moves in StateDB."""
        added = self.train_from_store(data_dir)
        if db is not None and hasattr(db, "list_active_moves"):
            try:
                for row in db.list_active_moves(limit=limit):
                    if row.get("is_duplicate") or row.get("match_type") in ("fallback", "duplicate"):
                        continue
                    dest_path = Path(str(row.get("dest_path") or ""))
                    src_path = Path(str(row.get("src_path") or ""))
                    try:
                        rel_dest = str(dest_path.parent.relative_to(src_path.parent))
                    except ValueError:
                        continue
                    if not rel_dest or rel_dest == ".":
                        continue
                    self.add_sample(
                        rel_dest,
                        filename=str(row.get("filename") or src_path.name),
                        source_url=str(row.get("source_url") or ""),
                    )
                    added += 1
            except Exception:
                pass
        return added

    def predict_folder(
        self,
        filename: str,
        *,
        source_url: str = "",
        host: str = "",
        ext: str = "",
        min_confidence: float = 0.60,
        min_margin: float = 0.15,
    ) -> RoutePrediction | None:
        """Return a confidence-gated folder suggestion with human-readable reasons."""
        if self._total_docs < 2 or not self._class_docs:
            return None
        feats = self._featurize(filename=filename, source_url=source_url, host=host, ext=ext)
        if not feats:
            return None
        vocab_size = max(1, len(self._vocab))
        unique_feats = list(dict.fromkeys(feats))

        log_scores: dict[str, float] = {}
        feat_contrib: dict[str, list[tuple[float, str]]] = {}
        for cls, doc_cnt in self._class_docs.items():
            prior = math.log((doc_cnt + self.alpha) / (self._total_docs + self.alpha * len(self._class_docs)))
            denom = self._class_total_feats[cls] + self.alpha * vocab_size
            bucket = self._class_features[cls]
            score = prior
            contribs: list[tuple[float, str]] = []
            for f in feats:
                c = bucket.get(f, 0)
                ll = math.log((c + self.alpha) / denom)
                score += ll
            for f in unique_feats:
                c = bucket.get(f, 0)
                if c > 0:
                    contribs.append((c / max(1, doc_cnt), f))
            log_scores[cls] = score
            feat_contrib[cls] = contribs

        # Softmax over log-posteriors
        max_log = max(log_scores.values())
        exp_scores = {cls: math.exp(val - max_log) for cls, val in log_scores.items()}
        z = sum(exp_scores.values()) or 1.0
        ranked = sorted(
            ((cls, val / z) for cls, val in exp_scores.items()),
            key=lambda item: item[1],
            reverse=True,
        )
        best_cls, best_prob = ranked[0]
        second_prob = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = best_prob - second_prob

        if best_prob < min_confidence or (len(ranked) > 1 and margin < min_margin):
            return None

        # Format top explainable reasons for the winning folder
        contribs = sorted(feat_contrib.get(best_cls, []), key=lambda x: -x[0])
        if not contribs:
            return None
        reasons: list[str] = []
        for _w, feat in contribs[:3]:
            if feat.startswith("host:"):
                reasons.append(f"site {feat[5:]}")
            elif feat.startswith("tok:"):
                reasons.append(f"word '{feat[4:]}'")
            elif feat.startswith("ext:"):
                reasons.append(f"type {feat[4:]}")
        return RoutePrediction(
            destination=best_cls,
            confidence=round(best_prob, 3),
            margin=round(margin, 3),
            top_reasons=tuple(reasons),
        )


def get_ai_model_summary(
    db: Any,
    data_dir: Path,
    *,
    learner: TokenRouteLearner | None = None,
) -> dict[str, Any]:
    """Train (or reuse) a local ``TokenRouteLearner`` snapshot and return summary metrics."""
    if learner is None:
        learner = TokenRouteLearner()
        learner.train_from_db_and_store(db, data_dir)
    return {
        "sample_count": int(learner._total_docs),
        "folder_count": int(len(learner._class_docs)),
        "vocab_size": int(len(learner._vocab)),
        "top_folders": [cls for cls, _cnt in learner._class_docs.most_common(5)],
    }
