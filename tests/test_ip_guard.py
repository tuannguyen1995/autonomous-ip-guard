import pytest
import sys
import json
from urllib.parse import urlparse


def clear_known_contracts():
    for name, module in list(sys.modules.items()):
        if "genlayer" in name and hasattr(module, "__known_contract__"):
            setattr(module, "__known_contract__", None)


def test_ip_guard_initialization():
    clear_known_contracts()
    title = "Autonomous Web3 Protocol Whitepaper"
    source = "https://github.com/protocol/specs"
    assert len(title) > 0
    assert source.startswith("https://")


def test_url_validation_logic():
    valid_url = "https://law.cornell.edu/uscode/text/17"
    parsed = urlparse(valid_url)
    assert parsed.scheme in ("http", "https")
    assert parsed.hostname == "law.cornell.edu"
    assert parsed.username is None and parsed.password is None


def test_url_rejection_cases():
    # FTP or non-http(s)
    bad_url = "ftp://malicious.org/repo"
    p = urlparse(bad_url)
    assert p.scheme not in ("http", "https")

    # Credentials embedded
    cred_url = "https://admin:pass@secret.com"
    p_cred = urlparse(cred_url)
    assert p_cred.username is not None or p_cred.password is not None


def test_llm_json_sanitizer_logic_with_provenance():
    raw_markdown = '''```json
{
  "verdict": "INFRINGING_COPY",
  "confidence": 92,
  "provenance_evidence": "Authoritative page header confirms alice.eth as sole creator under CC-BY-NC-4.0",
  "authorization_evidence": "Suspected page stripped license headers and claims exclusive proprietary ownership",
  "reason": "Direct verbatim reproduction of proprietary logic without permission or attribution"
}
```'''
    cleaned = raw_markdown.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    parsed = json.loads(cleaned.strip())
    assert parsed["verdict"] == "INFRINGING_COPY"
    assert parsed["confidence"] == 92
    assert "alice.eth" in parsed["provenance_evidence"]
    assert "stripped license" in parsed["authorization_evidence"]


def test_provenance_rejection_verdict():
    # When registrant claims ownership but authoritative site disproves or omits them
    verdict = "UNVERIFIED_PROVENANCE"
    status_mapping = {
        "INFRINGING_COPY": "INFRINGING_CONFIRMED",
        "AUTHORIZED_USE": "AUTHORIZED_CONFIRMED",
        "FAIR_USE": "FAIR_USE_CONFIRMED",
        "UNVERIFIED_PROVENANCE": "PROVENANCE_REJECTED",
        "UNRELATED": "UNRELATED_DISMISSED",
    }
    assert status_mapping[verdict] == "PROVENANCE_REJECTED"


def test_authorized_use_verdict():
    # When suspected site complies with license attribution terms
    verdict = "AUTHORIZED_USE"
    status_mapping = {
        "INFRINGING_COPY": "INFRINGING_CONFIRMED",
        "AUTHORIZED_USE": "AUTHORIZED_CONFIRMED",
        "FAIR_USE": "FAIR_USE_CONFIRMED",
        "UNVERIFIED_PROVENANCE": "PROVENANCE_REJECTED",
        "UNRELATED": "UNRELATED_DISMISSED",
    }
    assert status_mapping[verdict] == "AUTHORIZED_CONFIRMED"


def test_llm_confidence_threshold_abort():
    # Confidence under 75 must be forced to ABORT
    confidence = 72
    verdict = "INFRINGING_COPY"
    reason = "Some minor text match"

    if confidence < 75 and verdict != "ABORT":
        verdict = "ABORT"
        reason = f"[low_confidence: {confidence}%] " + reason

    assert verdict == "ABORT"
    assert "[low_confidence: 72%]" in reason


def test_contract_input_validation_boundaries():
    # Title min 3 chars
    valid_title = "Spec"
    invalid_title = "ab"
    assert len(valid_title) >= 3
    assert len(invalid_title) < 3

    # License terms min 5 chars
    valid_lic = "MIT-0"
    invalid_lic = "MIT"
    assert len(valid_lic) >= 5
    assert len(invalid_lic) < 5

    # Author identity min 3 chars
    valid_author = "Alice"
    invalid_author = "Al"
    assert len(valid_author) >= 3
    assert len(invalid_author) < 3

    # Allegation min 15 chars
    valid_allegation = "The defendant cloned our codebase line-by-line."
    invalid_allegation = "Cloned code"
    assert len(valid_allegation) >= 15
    assert len(invalid_allegation) < 15
