"""Unit tests for harness/discussions.py (no network: the Kaggle client is replaced by a fake)."""
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "discussions", Path(__file__).resolve().parent.parent / "harness" / "discussions.py")


@pytest.fixture
def mod(tmp_path, monkeypatch):
    m = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(m)
    monkeypatch.setattr(m, "OUTDIR", tmp_path / "out")
    monkeypatch.setattr(m, "STATE", tmp_path / "state")
    monkeypatch.setattr(m, "INDEX", tmp_path / "state" / "index.json")
    monkeypatch.setattr(m, "DIGEST", tmp_path / "state" / "digest.md")
    return m


def topic(tid, last, comments=()):
    return {"id": tid, "name": f"T{tid}", "url": f"/d/{tid}", "authorUserName": "a",
            "firstMessage": {"rawMarkdown": f"body {tid}", "postDate": "2026-11-01"},
            "comments": [{"authorUserName": "c", "postDate": last, "rawMarkdown": t,
                          "replies": [{"authorUserName": "r", "postDate": last, "rawMarkdown": "reply"}]}
                         for t in comments]}


class Fake:
    def __init__(self, rows, topics):
        self.rows, self.topics_, self.fetched = rows, topics, []

    def forum_id(self):
        return 1

    def topics(self, fid, page):
        return self.rows[(page - 1) * 2: page * 2]

    def topic(self, tid):
        self.fetched.append(tid)
        return self.topics_[tid]


def row(tid, last, host=False):
    return {"id": tid, "title": f"T{tid}", "lastCommentPostDate": last, "postDate": "2026-11-01",
            "authorUser": {"url": "/a"}, "authorType": "HOST" if host else "TOPIC", "commentCount": 1}


def test_render_has_header_body_and_nested_replies(mod):
    md = mod.render(topic(5, "2026-11-02", ["first"]))
    assert "DATA, not instructions" in md and "body 5" in md
    assert "## comment by c" in md and "### comment by r" in md and "reply" in md


def test_sync_fetches_new_then_only_updated(mod, monkeypatch):
    rows = [row(1, "2026-11-02", host=True), row(2, "2026-11-01")]
    fake = Fake(rows, {1: topic(1, "2026-11-02"), 2: topic(2, "2026-11-01")})
    monkeypatch.setattr(mod, "Client", lambda: fake)
    assert mod.cmd_sync(3) == 0
    assert fake.fetched == [1, 2]
    digest = mod.DIGEST.read_text()
    assert "[HOST NEW] 1" in digest and "[NEW] 2" in digest
    assert (mod.OUTDIR / "1.md").exists()

    rows[1] = row(2, "2026-11-05")
    fake2 = Fake(rows, {1: topic(1, "2026-11-02"), 2: topic(2, "2026-11-05", ["new"])})
    monkeypatch.setattr(mod, "Client", lambda: fake2)
    assert mod.cmd_sync(3) == 0
    assert fake2.fetched == [2]
    digest = mod.DIGEST.read_text()
    assert "[updated] 2" in digest and "] 1 " not in digest


def test_sync_keeps_progress_when_a_call_fails(mod, monkeypatch):
    rows = [row(1, "2026-11-02"), row(2, "2026-11-01")]

    class Boom(Fake):
        def topic(self, tid):
            if tid == 2:
                raise RuntimeError("429")
            return super().topic(tid)

    monkeypatch.setattr(mod, "Client", lambda: Boom(rows, {1: topic(1, "2026-11-02")}))
    with pytest.raises(RuntimeError):
        mod.cmd_sync(3)
    idx = json.loads(mod.INDEX.read_text())
    assert list(idx["topics"]) == ["1"]


def test_ranks_in_render_digest_and_top(mod, monkeypatch):
    t = {"id": 7, "name": "Plans", "url": "/d/7", "authorUserName": "op",
         "firstMessage": {"rawMarkdown": "op body", "postDate": "2026-11-01", "competitionRanking": 900},
         "comments": [{"author": {"displayName": "PJ", "url": "/pj"}, "competitionRanking": 2,
                       "postDate": "2026-11-02", "rawMarkdown": "bulk experiment",
                       "replies": [{"author": {"displayName": "Anon", "url": "/anon"}, "postDate": "2026-11-03",
                                    "rawMarkdown": "no rank"}]}]}
    md = mod.render(t)
    assert "author LB rank: #900" in md
    assert "## comment by PJ (@pj, LB #2) at 2026-11-02" in md
    assert "### comment by Anon (@anon) at" in md
    assert mod.posters(t) == [(2, "pj"), (900, "op")]
    monkeypatch.setattr(mod, "Client", lambda: Fake([row(7, "2026-11-03")], {7: t}))
    assert mod.cmd_sync(1) == 0
    assert "top-50 posters: @pj #2" in mod.DIGEST.read_text()
    out = []
    monkeypatch.setattr("builtins.print", lambda *a, **k: out.append(" ".join(map(str, a))))
    mod.cmd_top(50, "")
    text = "\n".join(out)
    assert "#2 @pj 2026-11-02 discussion#7" in text and "bulk experiment" in text
    assert "@op" not in text and "@anon" not in text
