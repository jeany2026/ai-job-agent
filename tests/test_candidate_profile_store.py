"""Step A: CandidateProfile persistence. Storage only; no Agent Loop, no LLM."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from storage.candidate_profile import (
    CandidateProfileStore,
    compute_source_hash,
    create_candidate_profile,
    load_candidate_profile,
    update_candidate_profile,
)
from storage.errors import CorruptStorageError, MissingStorageError, StorageError


def _profile(*, summary: str, extra_capability: str | None = None) -> dict:
    capabilities = []
    if extra_capability:
        capabilities.append(
            {
                "name": extra_capability,
                "kind": "direct",
                "category": "business",
                "explicit": True,
                "evidence": [{"quote": extra_capability}],
                "confidence": 0.9,
            }
        )
    return {
        "analysis_status": "ok",
        "error": None,
        "candidate_id": "cand-1",
        "summary": summary,
        "target_roles": ["高级产品经理"],
        "years_experience": "16年",
        "education": None,
        "locations": ["深圳"],
        "product_capabilities": [
            {
                "name": "B端产品",
                "kind": "direct",
                "category": "product",
                "explicit": True,
                "evidence": [{"quote": "B端产品"}],
                "confidence": 0.9,
            }
        ],
        "business_capabilities": capabilities
        or [
            {
                "name": "支付",
                "kind": "direct",
                "category": "business",
                "explicit": True,
                "evidence": [{"quote": "支付"}],
                "confidence": 0.9,
            }
        ],
        "technical_capabilities": [],
        "industry_experience": [],
        "management_experience": [],
        "project_experience": [],
        "transferable_capabilities": [],
        "knowledge_gaps": [],
        "direct_capabilities": [],
        "raw_evidence_notes": None,
    }


def test_create_load_round_trip(tmp_path):
    profile = _profile(summary="金融科技产品经理")
    digest = compute_source_hash("鲍玲俐 简历文本")
    created = create_candidate_profile(
        tmp_path,
        profile,
        candidate_id="cand-1",
        source_resume_name="鲍玲俐_简历.docx",
        source_resume_hash=digest,
        source_kinds=["resume"],
    )
    assert created["schema_version"] == 1
    assert created["candidate_id"] == "cand-1"
    assert created["profile_version"] == 1
    assert created["source_resume_name"] == "鲍玲俐_简历.docx"
    assert created["source_resume_hash"] == digest
    assert created["source_kinds"] == ["resume"]
    assert created["created_at"]
    assert created["updated_at"] == created["created_at"]
    assert created["profile"]["summary"] == "金融科技产品经理"

    loaded = load_candidate_profile(tmp_path, "cand-1")
    assert loaded == created
    again = CandidateProfileStore(tmp_path).load("cand-1")
    assert again == loaded


def test_create_without_resume_from_user_statement(tmp_path):
    profile = _profile(summary="16年金融科技产品经验")
    created = create_candidate_profile(
        tmp_path,
        profile,
        source_kinds=["user_statement"],
    )
    assert created["candidate_id"] == "cand-1"
    assert created["source_resume_name"] is None
    assert created["source_resume_hash"] is None
    assert created["source_kinds"] == ["user_statement"]
    assert created["profile_version"] == 1


def test_create_defaults_candidate_id(tmp_path):
    profile = _profile(summary="默认候选人")
    profile["candidate_id"] = None
    created = create_candidate_profile(tmp_path, profile)
    assert created["candidate_id"] == "default"
    assert CandidateProfileStore(tmp_path).exists("default")


def test_create_existing_fails_closed(tmp_path):
    create_candidate_profile(tmp_path, _profile(summary="v1"), candidate_id="cand-1")
    with pytest.raises(StorageError, match="already exists"):
        create_candidate_profile(tmp_path, _profile(summary="v2"), candidate_id="cand-1")
    loaded = load_candidate_profile(tmp_path, "cand-1")
    assert loaded["profile_version"] == 1
    assert loaded["profile"]["summary"] == "v1"


def test_update_increments_profile_version_not_resume_file_version(tmp_path):
    first_hash = compute_source_hash("resume A")
    create_candidate_profile(
        tmp_path,
        _profile(summary="支付结算"),
        candidate_id="cand-1",
        source_resume_name="a.docx",
        source_resume_hash=first_hash,
        source_kinds=["resume"],
    )
    updated = update_candidate_profile(
        tmp_path,
        "cand-1",
        _profile(summary="支付结算 + 对账风控", extra_capability="对账"),
        source_resume_name="b.docx",
        source_resume_hash=compute_source_hash("resume B"),
        source_kinds=["resume", "project_document"],
    )
    assert updated["profile_version"] == 2
    assert updated["created_at"] == load_candidate_profile(tmp_path, "cand-1")["created_at"]
    assert updated["updated_at"] >= updated["created_at"]
    assert updated["profile"]["summary"] == "支付结算 + 对账风控"
    assert updated["source_kinds"] == ["resume", "project_document"]


def test_update_archives_previous_profile_so_old_capabilities_are_not_lost(tmp_path):
    create_candidate_profile(
        tmp_path,
        _profile(summary="旧画像含支付"),
        candidate_id="cand-1",
        source_resume_hash=compute_source_hash("old resume"),
        source_kinds=["resume"],
    )
    update_candidate_profile(
        tmp_path,
        "cand-1",
        _profile(summary="新证据更新后的画像", extra_capability="风控"),
        source_resume_hash=compute_source_hash("new resume"),
    )
    store = CandidateProfileStore(tmp_path)
    current = store.load("cand-1")
    archived = store.load_version("cand-1", 1)
    assert current["profile_version"] == 2
    assert archived["profile_version"] == 1
    assert archived["profile"]["summary"] == "旧画像含支付"
    assert archived["profile"]["business_capabilities"][0]["name"] == "支付"
    assert current["profile"]["business_capabilities"][0]["name"] == "风控"
    assert store.list_versions("cand-1") == [1, 2]


def test_update_preserves_hash_when_not_provided(tmp_path):
    digest = compute_source_hash("same resume")
    create_candidate_profile(
        tmp_path,
        _profile(summary="v1"),
        candidate_id="cand-1",
        source_resume_hash=digest,
        source_kinds=["resume"],
    )
    updated = update_candidate_profile(tmp_path, "cand-1", _profile(summary="v2"))
    assert updated["source_resume_hash"] == digest
    assert updated["source_kinds"] == ["resume"]
    assert updated["profile_version"] == 2


def test_find_by_resume_hash(tmp_path):
    digest = compute_source_hash("identical extracted text")
    create_candidate_profile(
        tmp_path,
        _profile(summary="已分析"),
        candidate_id="cand-1",
        source_resume_hash=digest,
        source_kinds=["resume"],
    )
    store = CandidateProfileStore(tmp_path)
    found = store.find_by_resume_hash(digest)
    assert found is not None
    assert found["candidate_id"] == "cand-1"
    assert store.find_by_resume_hash(compute_source_hash("different text")) is None
    assert store.find_by_resume_hash("") is None


def test_compute_source_hash_normalizes_whitespace():
    assert compute_source_hash("支付  结算\n对账") == compute_source_hash("支付结算对账")
    with pytest.raises(StorageError):
        compute_source_hash("   ")
    with pytest.raises(StorageError):
        compute_source_hash(None)


def test_refuses_failed_profile(tmp_path):
    with pytest.raises(StorageError, match="analysis_status"):
        create_candidate_profile(
            tmp_path,
            {"analysis_status": "llm_error", "error": "timeout", "summary": None},
        )


def test_load_missing_fails_closed(tmp_path):
    with pytest.raises(MissingStorageError) as exc:
        load_candidate_profile(tmp_path, "no-such")
    assert exc.value.path
    assert "no-such" in str(exc.value)


def test_update_missing_fails_closed(tmp_path):
    with pytest.raises(MissingStorageError):
        update_candidate_profile(tmp_path, "cand-1", _profile(summary="x"))


def test_load_empty_candidate_id_fails_closed(tmp_path):
    with pytest.raises(StorageError):
        load_candidate_profile(tmp_path, "  ")


def test_corrupt_json_fails_closed(tmp_path):
    create_candidate_profile(tmp_path, _profile(summary="ok"), candidate_id="cand-1")
    path = tmp_path / "cand-1.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        load_candidate_profile(tmp_path, "cand-1")


def test_missing_required_fields_fails_closed(tmp_path):
    path = tmp_path / "cand-1.json"
    path.write_text(json.dumps({"schema_version": 1, "candidate_id": "cand-1"}), encoding="utf-8")
    with pytest.raises(CorruptStorageError):
        load_candidate_profile(tmp_path, "cand-1")


def test_unknown_schema_fails_closed(tmp_path):
    path = tmp_path / "cand-1.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "candidate_id": "cand-1",
                "profile_version": 1,
                "profile": {"analysis_status": "ok"},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CorruptStorageError):
        load_candidate_profile(tmp_path, "cand-1")


def test_illegal_source_kind_rejected(tmp_path):
    with pytest.raises(StorageError, match="illegal source kind"):
        create_candidate_profile(
            tmp_path,
            _profile(summary="x"),
            source_kinds=["keyword_list"],
        )


def test_exists_false_when_absent(tmp_path):
    store = CandidateProfileStore(tmp_path)
    assert store.exists("cand-1") is False
    assert store.list_versions("cand-1") == []


def test_persists_semantic_layers_not_profile_as_sole_truth(tmp_path):
    evidence = [
        {
            "id": "ev-1",
            "content_type": "resume",
            "content": "做过支付",
            "origin": "resume",
        }
    ]
    claims = [{"id": "cl-1", "statement": "做过支付", "derived_from": ["ev-1"], "layer": "claim"}]
    created = create_candidate_profile(
        tmp_path,
        _profile(summary="支付画像"),
        candidate_id="cand-1",
        source_kinds=["resume"],
        evidence=evidence,
        claims=claims,
        interpretations=[{"id": "int-1", "kind": "analyze_candidate", "derived_from": ["ev-1"]}],
        verifications=[],
    )
    assert created["profile"]["kind"] == "interpretation_projection"
    assert created["evidence"][0]["content"] == "做过支付"
    assert created["claims"][0]["id"] == "cl-1"
    loaded = load_candidate_profile(tmp_path, "cand-1")
    assert loaded["evidence"] == created["evidence"]
    assert loaded["claims"] == created["claims"]
    assert loaded["profile"]["kind"] == "interpretation_projection"
    updated = update_candidate_profile(
        tmp_path,
        "cand-1",
        _profile(summary="更新后的投影"),
        claims=[{"id": "cl-2", "statement": "也对过账", "derived_from": ["ev-1"], "layer": "claim"}],
    )
    assert updated["profile_version"] == 2
    assert updated["evidence"][0]["id"] == "ev-1"
    assert updated["claims"][0]["id"] == "cl-2"
    assert updated["profile"]["summary"] == "更新后的投影"
    archived = CandidateProfileStore(tmp_path).load_version("cand-1", 1)
    assert archived["claims"][0]["id"] == "cl-1"


def test_load_legacy_record_without_layers_defaults_empty_arrays(tmp_path):
    path = tmp_path / "cand-1.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "candidate_id": "cand-1",
                "profile_version": 1,
                "profile": {"analysis_status": "ok", "summary": "旧画像"},
                "created_at": "2026-01-01T00:00:00+00:00",
                "updated_at": "2026-01-01T00:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    loaded = load_candidate_profile(tmp_path, "cand-1")
    assert loaded["evidence"] == []
    assert loaded["claims"] == []
    assert loaded["interpretations"] == []
    assert loaded["verifications"] == []
    assert loaded["profile"]["kind"] == "interpretation_projection"
    assert loaded["profile"]["summary"] == "旧画像"
