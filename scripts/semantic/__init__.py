"""Semantic analysis layer over index/corpus.sqlite: embeddings, tone, targets, topics, trends, echoes, briefs.

Entry point: ``uv run python -m scripts.semantic.run [--stage ...]``. Outputs live in index/semantic/ (private)
and reports/ (briefs, validation sample). See scripts/semantic/SCHEMA.md for the aggregate JSON contract.
"""

__all__ = ["config"]
