"""Artifact Schema 单元测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hermes.qc.artifact import (
    ExecutionPlanArtifact,
    ExecutionStep,
    FindingsArtifact,
    QCResultArtifact,
    QCVerdict,
    ResearchFinding,
    RiskItem,
    RiskSeverity,
    StepType,
    TaskComplexity,
    load_artifact,
    save_artifact,
    validate_artifact,
)


class TestFindingsArtifact:
    def test_valid_artifact(self):
        artifact = FindingsArtifact(
            key_findings=[
                ResearchFinding(
                    finding="Auth module uses JWT tokens",
                    evidence="src/auth/jwt.py:42",
                    confidence=0.9,
                ),
            ],
            files_in_scope=["src/auth/jwt.py", "src/auth/middleware.py"],
            tech_stack=["python", "fastapi"],
            scope_decision="Focus on JWT validation, not OAuth flow",
        )
        assert len(artifact.key_findings) == 1
        assert artifact.task_complexity == TaskComplexity.MEDIUM

    def test_tier1_summary(self):
        artifact = FindingsArtifact(
            key_findings=[
                ResearchFinding(
                    finding="Database connection pool is exhausted",
                    evidence="src/db/pool.py:15",
                    confidence=0.85,
                ),
            ],
            tech_stack=["python", "sqlalchemy"],
            scope_decision="Fix connection pool sizing",
        )
        summary = artifact.tier1_summary()
        assert "Fix connection pool sizing" in summary
        assert "python" in summary

    def test_tier2_details(self):
        artifact = FindingsArtifact(
            key_findings=[
                ResearchFinding(
                    finding=f"Test finding number {i} for validation",
                    evidence=f"file{i}.py:1",
                    confidence=0.8,
                )
                for i in range(5)
            ],
            scope_decision="Test scope",
        )
        details = artifact.tier2_details()
        assert "finding number 0" in details.lower() or "Finding" in details
        assert "file0.py" in details

    def test_max_findings_limit(self):
        with pytest.raises(Exception):
            FindingsArtifact(
                key_findings=[
                    ResearchFinding(
                        finding=f"F{i}", evidence=f"e{i}", confidence=0.5
                    )
                    for i in range(20)  # > 15 max
                ],
                scope_decision="test",
            )

    def test_confidence_rounding(self):
        f = ResearchFinding(
            finding="test finding here",
            evidence="test evidence here",
            confidence=0.8567,
        )
        assert f.confidence == 0.86


class TestExecutionPlanArtifact:
    def test_valid_plan(self):
        plan = ExecutionPlanArtifact(
            steps=[
                ExecutionStep(id=1, action="Add validation function", files_affected=["src/v.py"]),
                ExecutionStep(id=2, action="Update API handler", files_affected=["src/api.py"],
                              depends_on=[1]),
            ],
            acceptance_criteria=["Tests pass", "No lint errors"],
        )
        assert len(plan.steps) == 2

    def test_topological_order(self):
        plan = ExecutionPlanArtifact(
            steps=[
                ExecutionStep(id=3, action="Implement Step C integration", depends_on=[1, 2]),
                ExecutionStep(id=1, action="Create base module A"),
                ExecutionStep(id=2, action="Build helper module B", depends_on=[1]),
            ],
            acceptance_criteria=["Done"],
        )
        ordered = plan.topological_order()
        ids = [s.id for s in ordered]
        assert ids.index(1) < ids.index(2)
        assert ids.index(2) < ids.index(3)

    def test_max_steps_limit(self):
        with pytest.raises(Exception):
            ExecutionPlanArtifact(
                steps=[
                    ExecutionStep(id=i, action=f"Step {i}")
                    for i in range(1, 15)  # > 12 max
                ],
                acceptance_criteria=["Done"],
            )


class TestQCResultArtifact:
    def test_pass_verdict(self):
        qc = QCResultArtifact(verdict=QCVerdict.PASS, hard_check_passed=True)
        assert qc.is_pass
        assert not qc.is_conditional

    def test_fail_verdict(self):
        qc = QCResultArtifact(
            verdict=QCVerdict.FAIL,
            issues_found=["Missing tests", "Unhandled exception"],
        )
        assert not qc.is_pass
        assert len(qc.issues_found) == 2


class TestArtifactIO:
    def test_save_and_load(self, tmp_path: Path):
        artifact = FindingsArtifact(
            key_findings=[
                ResearchFinding(
                    finding="Test finding here",
                    evidence="test.py:1",
                    confidence=0.9,
                ),
            ],
            scope_decision="Test scope decision",
        )
        path = tmp_path / "findings.json"
        save_artifact(artifact, path)

        loaded = load_artifact(FindingsArtifact, path)
        assert isinstance(loaded, FindingsArtifact)
        assert loaded.scope_decision == "Test scope decision"

    def test_atomic_write(self, tmp_path: Path):
        """验证原子写入（.tmp → rename）。"""
        artifact = QCResultArtifact(verdict=QCVerdict.PASS)
        path = tmp_path / "qc.json"
        save_artifact(artifact, path)

        assert path.exists()
        assert not (tmp_path / "qc.tmp").exists()  # tmp 文件已被替换


class TestValidateArtifact:
    def test_valid_findings(self):
        data = {
            "schema_version": "1.0",
            "key_findings": [
                {"finding": "Test finding here", "evidence": "file.py:1", "confidence": 0.8}
            ],
            "scope_decision": "Test scope",
        }
        is_valid, errors = validate_artifact(FindingsArtifact, data)
        assert is_valid
        assert errors == []

    def test_invalid_findings_missing_scope(self):
        data = {
            "key_findings": [
                {"finding": "Test finding here", "evidence": "file.py:1", "confidence": 0.8}
            ],
            "scope_decision": "   ",  # empty
        }
        is_valid, errors = validate_artifact(FindingsArtifact, data)
        assert not is_valid

    def test_schema_validation_failure(self):
        data = {"key_findings": [], "scope_decision": "test"}  # empty findings (min_length=1)
        is_valid, errors = validate_artifact(FindingsArtifact, data)
        assert not is_valid
