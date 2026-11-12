"""Embedding requests are split into slices a local model server accepts.

The semantic chunker embeds every sentence of a section in one call. Ollama
rejects a request with a few hundred texts ("connection reset by peer" on its
tokenize step), which ended the ingest on the first long section. The split has
to keep the vectors in input order, because the chunker pairs each vector with
the sentence at the same position.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

import llm


def _response(texts: list[str]):
    # Shuffled on purpose: litellm returns `index`, not guaranteed order.
    data = [{"index": i, "embedding": [float(t.split()[-1])]} for i, t in enumerate(texts)]
    return SimpleNamespace(data=list(reversed(data)))


@pytest.fixture
def calls(monkeypatch):
    seen: list[int] = []

    def fake_embedding(**kwargs):
        seen.append(len(kwargs["input"]))
        return _response(kwargs["input"])

    async def fake_aembedding(**kwargs):
        return fake_embedding(**kwargs)

    monkeypatch.setattr(llm.litellm, "embedding", fake_embedding)
    monkeypatch.setattr(llm.litellm, "aembedding", fake_aembedding)
    return seen


TEXTS = [f"sentence {i}" for i in range(150)]


def test_sync_embedding_never_sends_more_than_one_slice(calls):
    vectors = llm.embed_sync(TEXTS)

    assert calls == [64, 64, 22]
    assert vectors == [[float(i)] for i in range(150)], "vectors left input order"


def test_async_embedding_never_sends_more_than_one_slice(calls):
    vectors = asyncio.run(llm.embed(TEXTS))

    assert calls == [64, 64, 22]
    assert vectors == [[float(i)] for i in range(150)], "vectors left input order"


def test_a_small_request_stays_one_call(calls):
    llm.embed_sync(TEXTS[:5])

    assert calls == [5]
