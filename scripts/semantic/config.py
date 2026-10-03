"""Paths, model ids and pipeline settings for the semantic layer (one place, immutable)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[2]
CORPUS_DB = ROOT / "index" / "corpus.sqlite"
SEM_DIR = ROOT / "index" / "semantic"
SEM_DB = SEM_DIR / "semantic.sqlite"
EMB_DIR = SEM_DIR / "emb"
MODEL_DIR = SEM_DIR / "models"
AGG_DIR = SEM_DIR / "aggregates"
TONE_MODEL = SEM_DIR / "tone_model.npz"
TOPIC_MODEL = SEM_DIR / "topic_model.npz"
REPORT_DIR = ROOT / "reports" / "semantic"
BRIEF_DIR = ROOT / "reports" / "briefs"

E5_MODEL = "intfloat/multilingual-e5-small"
# Zero-shot teacher. mDeBERTa-v3-base-xnli-multilingual-nli-2mil7 was tried first and rejected: on 48 hand-labelled
# sentences it reached 0.65-0.76 accuracy and marked e.g. sports results as "threat" (reports/semantic/hypothesis_selection.md).
NLI_MODEL = "MoritzLaurer/bge-m3-zeroshot-v2.0"
EMB_DIM = 384

# Tone dimensions. "escalation" and "deescalation" are scored separately; the escalation-framing balance
# reported in series is escalation - deescalation (range -1..1).
DIMS: Tuple[str, ...] = ("hostility", "threat", "conciliation", "grievance", "escalation", "deescalation")
DERIVED = ("esc_balance",)

# NLI hypotheses (English hypotheses with multilingual premises). Wording chosen on a 24-sentence dev set and checked
# on a separate 24-sentence hold-out set (EN/RU/ZH; reports/semantic/hypothesis_selection.md).
HYPOTHESES = {
    "hostility": "This text is hostile toward another country or actor.",
    "threat": "This text is about threats or warnings of retaliation.",
    "conciliation": "This text calls for cooperation, dialogue, partnership, or friendly relations.",
    "grievance": "This text complains that the speaker or its country is being wronged, victimized, or treated unfairly.",
    "escalation": "This text is about escalation of a conflict.",
    "deescalation": "This text urges calm, restraint or a peaceful settlement of a conflict.",
}

# Selection regimes (doc field "sample") that are keyword-filtered and therefore excluded from trend series.
EXCLUDED_SAMPLES = ("backfill", "seed")


@dataclass(frozen=True)
class Settings:
    """Pipeline knobs. Change here, not in stage code."""

    max_tokens: int = 200          # passage chunk size (e5 tokens)
    max_chunks: int = 4            # text chunks per doc (plus the title passage)
    sent_cap: int = 80             # sentences scored for tone: idx < sent_cap ...
    mention_cap: int = 400         # ... plus target-mentioning sentences with idx < mention_cap
    embed_batch_docs: int = 3000
    teacher_total: int = 12000     # stratified sentence sample for the NLI teacher
    teacher_stratum_cap: int = 600
    heldout_frac: float = 0.2
    seed: int = 13
    # trends / alerts
    week_baseline: int = 12
    week_min_base: int = 6
    week_min_n: int = 8
    month_baseline: int = 6
    month_min_base: int = 4
    month_min_n: int = 20
    z_alert: float = 3.0
    min_effect: float = 0.03       # minimum absolute change in mean probability for an alert
    # echo
    echo_threshold: float = 0.93
    echo_window_days: int = 14
    echo_min_chars: int = 60
    echo_min_chars_cjk: int = 24
    topics_k_grid: Tuple[int, ...] = field(default=(40, 50, 60, 70, 80))
    topic_per_stream_cap: int = 3000


SETTINGS = Settings()


def device() -> str:
    """'mps' when available (Apple GPU), else 'cpu'. Override with SEM_DEVICE=cpu."""
    forced = os.environ.get("SEM_DEVICE")
    if forced:
        return forced
    try:
        import torch

        return "mps" if torch.backends.mps.is_available() else "cpu"
    except ImportError:
        return "cpu"


def model_path(repo: str) -> str:
    """Local snapshot of a Hugging Face model under index/semantic/models (downloads weights once)."""
    from huggingface_hub import snapshot_download

    kw = dict(cache_dir=str(MODEL_DIR), allow_patterns=["*.json", "model.safetensors", "*.model", "1_Pooling/*"])
    try:
        return snapshot_download(repo, local_files_only=True, **kw)
    except Exception:  # not cached yet
        return snapshot_download(repo, **kw)
