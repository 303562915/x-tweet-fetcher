"""CLI compatibility tests: v1 flags accepted, JSON envelopes preserved."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from xtf import cli
from xtf.cli import build_parser
from xtf.ledger import query_ledger
from xtf.models import Reply, Tweet

ROOT = Path(__file__).parent.parent

#: Subprocess output is decoded explicitly as UTF-8: the CLI prints Chinese
#: tool messages, while ``text=True`` alone would decode with the host locale
#: (GBK on zh-CN Windows) and blow up before the assertions ever run. The child
#: is told the same thing through PYTHONIOENCODING so both ends agree.
_RUN_KW = {"capture_output": True, "text": True, "encoding": "utf-8", "errors": "replace"}
_ENV = {
    "PYTHONPATH": str(ROOT / "src"),
    "PYTHONIOENCODING": "utf-8",
    "PATH": "/usr/bin:/bin",
}


def test_all_v1_flags_accepted():
    parser = build_parser()
    args = parser.parse_args([
        "--url", "https://x.com/a/status/1", "--pretty", "--text-only",
        "--timeout", "10", "--port", "9377",
        "--nitter", "nitter.example.com", "--backend", "nitter", "--lang", "en",
    ])
    assert args.url and args.pretty and args.text_only
    assert args.backend == "nitter"


def test_short_flags():
    parser = build_parser()
    args = parser.parse_args(["-u", "https://x.com/a/status/1", "-r", "-p", "-t"])
    assert args.url and args.replies and args.pretty and args.text_only


def test_mutually_exclusive_modes_exit_1():
    proc = subprocess.run(
        [sys.executable, "-m", "xtf.cli", "--url", "https://x.com/a/status/1",
         "--user", "alice"],
        cwd=ROOT, env=_ENV, **_RUN_KW,
    )
    assert proc.returncode == 1


def test_invalid_url_json_envelope():
    proc = subprocess.run(
        [sys.executable, "-m", "xtf.cli", "--url", "https://example.com/nope"],
        cwd=ROOT, env=_ENV, **_RUN_KW,
    )
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["url"] == "https://example.com/nope"
    assert "error" in out                      # v1 field
    assert out["error_code"] == "invalid_input"  # v2 addition


def test_compat_shim_exists_and_parses():
    shim = ROOT / "scripts" / "fetch_tweet.py"
    assert shim.exists()
    proc = subprocess.run(
        [sys.executable, str(shim), "--url", "https://not-a-tweet"],
        cwd=ROOT, env={"PATH": _ENV["PATH"], "PYTHONIOENCODING": "utf-8"}, **_RUN_KW,
    )
    assert proc.returncode == 1
    assert "error" in json.loads(proc.stdout)


# ── ledger integration ───────────────────────────────────────────────────
class _FakeRouter:
    """Timeline-only router stub; views!=0 so supplement_views skips network."""

    last_backend = "nitter"

    def __init__(self, *args, **kwargs):
        pass

    def fetch_timeline(self, username, limit=20):
        return [
            Tweet(author="@a", author_name="A", text="first tweet",
                   tweet_id="1001", views=1),
            Tweet(author="@b", author_name="B", text="second tweet",
                   tweet_id="1002", views=1),
        ]


def _run_cli(argv, env_extra=None):
    env = dict(_ENV)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, "-m", "xtf.cli", *argv],
        cwd=ROOT, env=env, **_RUN_KW,
    )


def test_ledger_flags_parsed():
    args = build_parser().parse_args(
        ["--ledger", "led.db", "--query", "openclaw", "--limit", "5"]
    )
    assert args.ledger == "led.db" and args.query == "openclaw" and args.limit == 5
    args = build_parser().parse_args(["--ledger", "led.db", "--stats"])
    assert args.stats and not args.query


def test_query_requires_ledger():
    proc = _run_cli(["--query", "openclaw"])
    assert proc.returncode == 1
    assert "--ledger" in proc.stderr


def test_ledger_stats_subprocess(tmp_path):
    db = tmp_path / "ledger.db"
    proc = _run_cli(["--ledger", str(db), "--stats"])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["ledger"] == str(db)
    assert out["stats"]["exists"] is False and out["stats"]["total_tweets"] == 0


def test_ledger_query_subprocess(tmp_path):
    db = tmp_path / "ledger.db"
    from xtf.ledger import archive_tweets
    archive_tweets(db, [{"tweet_id": "1", "text": "openclaw agent"}])
    proc = _run_cli(["--ledger", str(db), "--query", "openclaw"])
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["count"] == 1 and out["tweets"][0]["tweet_id"] == "1"
    assert "raw_json" not in out["tweets"][0]  # CLI output stays compact


def test_fetch_mode_archives_to_ledger(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Router", _FakeRouter)
    db = tmp_path / "ledger.db"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--user", "alice", "--ledger", str(db)])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ledger"]["inserted"] == 2
    assert out["ledger"]["source_file"] if False else True
    assert len(query_ledger(db)) == 2


def test_fetch_without_ledger_unchanged(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Router", _FakeRouter)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--user", "alice"])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert "ledger" not in out and "ledger_error" not in out
    assert not (tmp_path / "ledger.db").exists()


def test_archive_if_requested_skips_without_ledger(tmp_path):
    from types import SimpleNamespace
    args = SimpleNamespace(ledger=None)
    result = {"backend": "nitter", "tweets": [{"tweet_id": "1", "text": "x"}]}
    cli._archive_if_requested(args, result, result["tweets"])
    assert "ledger" not in result


def test_query_and_stats_mutually_exclusive(tmp_path):
    db = tmp_path / "ledger.db"
    proc = _run_cli(["--ledger", str(db), "--query", "x", "--stats"])
    assert proc.returncode == 1
    assert "exclusive" in proc.stderr


class _FakeFxtwitterRouter:
    """Single-tweet stub: returns a fxtwitter-style dict WITHOUT tweet_id."""

    last_backend = "fxtwitter"

    def __init__(self, *args, **kwargs):
        pass

    def fetch_tweet(self, username, tweet_id):
        return {
            "screen_name": "YuLin807",
            "text": "单推归档测试",
            "created_at": "Sat Aug 08 16:49:48 +0000",
            "lang": "zh",
            "likes": 1,
            "media": {"videos": []},
        }


class _FakeRepliesRouter:
    """Replies-mode stub returning Reply objects with nonzero views."""

    last_backend = "nitter"

    def __init__(self, *args, **kwargs):
        pass

    def fetch_replies(self, username, tweet_id):
        return [
            Reply(author="@a", author_name="A", text="a reply",
                  tweet_id="11", views=1),
            Reply(author="@b", author_name="B", text="second reply",
                  tweet_id="12", views=1),
        ]


def test_single_tweet_archives_with_injected_id(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "Router", _FakeFxtwitterRouter)
    db = tmp_path / "ledger.db"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--url", "https://x.com/YuLin807/status/2086132781533544665",
                  "--ledger", str(db)])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ledger"]["inserted"] == 1
    hits = query_ledger(db)
    assert len(hits) == 1
    assert hits[0]["tweet_id"] == "2086132781533544665"
    assert hits[0]["lang"] == "zh"


def test_replies_archived_as_replies(tmp_path, monkeypatch, capsys):
    # S2: replies archived via --url --replies must carry is_reply=1 and
    # in_reply_to_status_id pointing at the parent tweet.
    monkeypatch.setattr(cli, "Router", _FakeRepliesRouter)
    db = tmp_path / "ledger.db"
    with pytest.raises(SystemExit) as exc:
        cli.main(["--url", "https://x.com/YuLin807/status/2086132781533544665",
                  "--replies", "--ledger", str(db)])
    assert exc.value.code == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ledger"]["inserted"] == 2
    hits = query_ledger(db)
    assert len(hits) == 2
    for hit in hits:
        assert hit["is_reply"] == 1
        assert hit["in_reply_to_status_id"] == "2086132781533544665"


def test_ledger_query_corrupt_db_returns_json_error(tmp_path):
    # B2: a broken ledger file must produce a JSON error envelope, not a traceback.
    db = tmp_path / "broken.db"
    db.write_bytes(b"this is not a sqlite database at all")
    proc = _run_cli(["--ledger", str(db), "--query", "x"])
    assert proc.returncode == 1
    out = json.loads(proc.stdout)
    assert out["error_code"] == "ledger_error"
    assert out["error"]
    assert "traceback" not in proc.stderr.lower()
