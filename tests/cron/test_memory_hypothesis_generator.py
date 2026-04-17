from cron.autoresearch.memory_hypothesis_generator import generate_memory_proposals


BASE_ANOMALY = {
    "target": "memory",
    "old_text": "Always use branch main for deploy.",
    "entry_text": "Always use branch main for deploy.",
    "evidence_count": 3,
    "weighted_evidence_score": 2.1,
    "evidence_snippets": ["that's wrong, never use branch main for deploy"],
    "trigger_metric": "contradiction_evidence=3",
}


def test_generates_valid_replace_proposal():
    def llm_call(_messages):
        return (
            '{"action":"replace","target":"memory","old_text":"Always use branch main for deploy.",'
            '"content":"Use release branches for deploy.","reason":"policy changed","confidence":0.92}'
        )

    out = generate_memory_proposals([BASE_ANOMALY], llm_call)
    assert len(out) == 1
    assert out[0]["action"] == "replace"
    assert out[0]["target"] == "memory"
    assert out[0]["confidence"] == 0.92


def test_rejects_invalid_action():
    def llm_call(_messages):
        return (
            '{"action":"add","target":"memory","old_text":"Always use branch main for deploy.",'
            '"content":"x","reason":"x","confidence":0.99}'
        )

    assert generate_memory_proposals([BASE_ANOMALY], llm_call) == []


def test_rejects_low_confidence():
    def llm_call(_messages):
        return (
            '{"action":"remove","target":"memory","old_text":"Always use branch main for deploy.",'
            '"content":"","reason":"weak evidence","confidence":0.3}'
        )

    assert generate_memory_proposals([BASE_ANOMALY], llm_call, min_confidence=0.7) == []


def test_rejects_invalid_target():
    def llm_call(_messages):
        return (
            '{"action":"replace","target":"project","old_text":"Always use branch main for deploy.",'
            '"content":"x","reason":"x","confidence":0.9}'
        )

    assert generate_memory_proposals([BASE_ANOMALY], llm_call) == []


def test_rejects_old_text_not_linked_to_entry():
    def llm_call(_messages):
        return (
            '{"action":"replace","target":"memory","old_text":"something unrelated",'
            '"content":"x","reason":"x","confidence":0.9}'
        )

    assert generate_memory_proposals([BASE_ANOMALY], llm_call) == []


def test_rejects_when_anomaly_evidence_below_threshold():
    low = dict(BASE_ANOMALY)
    low["evidence_count"] = 1

    def llm_call(_messages):
        return (
            '{"action":"replace","target":"memory","old_text":"Always use branch main for deploy.",'
            '"content":"Use release branches for deploy.","reason":"x","confidence":0.95}'
        )

    assert generate_memory_proposals([low], llm_call, min_evidence=2) == []


def test_rejects_when_weighted_evidence_below_threshold():
    low = dict(BASE_ANOMALY)
    low["weighted_evidence_score"] = 0.2

    def llm_call(_messages):
        return (
            '{"action":"replace","target":"memory","old_text":"Always use branch main for deploy.",'
            '"content":"Use release branches for deploy.","reason":"x","confidence":0.95}'
        )

    assert generate_memory_proposals([low], llm_call, min_evidence_score=1.25) == []


def test_remove_action_normalizes_content_to_empty():
    def llm_call(_messages):
        return (
            '{"action":"remove","target":"memory","old_text":"Always use branch main for deploy.",'
            '"content":"should be dropped","reason":"obsolete","confidence":0.9}'
        )

    out = generate_memory_proposals([BASE_ANOMALY], llm_call)
    assert len(out) == 1
    assert out[0]["action"] == "remove"
    assert out[0]["content"] == ""
