import json

from cron.autoresearch.pending_memory_updates import (
    read_pending_memory_updates,
    write_pending_memory_updates,
)


def test_write_and_read_roundtrip(tmp_path):
    path = tmp_path / "pending_memory_updates.json"
    proposals = [
        {
            "target": "memory",
            "action": "replace",
            "old_text": "old",
            "content": "new",
            "reason": "fix stale",
            "confidence": 0.9,
            "evidence_count": 3,
            "trigger_metric": "contradiction_evidence=3",
        }
    ]
    text = write_pending_memory_updates(proposals, path=path)
    assert path.exists()
    loaded = read_pending_memory_updates(path)
    assert len(loaded) == 1
    assert loaded[0]["target"] == "memory"
    assert loaded[0]["action"] == "replace"
    assert "generated_at" in loaded[0]
    assert json.loads(text)[0]["old_text"] == "old"


def test_read_missing_file_returns_empty(tmp_path):
    assert read_pending_memory_updates(tmp_path / "none.json") == []
