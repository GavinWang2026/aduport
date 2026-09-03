"""E · 评分基线：对外不得把自评 88 写成当前分。"""

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_score_baseline_anchors_66_not_88():
    score = (_ROOT / "enterprise" / "SCORE_93.md").read_text(encoding="utf-8")
    assert "66.2" in score
    assert "禁止" in score or "不得" in score
    # 88 may appear as historical 自评, but current 对外分 must be 66.2
    assert "**当前对外分**" in score or "当前对外分" in score
    acc = (_ROOT / "enterprise" / "ACCEPTANCE.md").read_text(encoding="utf-8")
    assert "66.2" in acc
    assert "KNOWN_ILLUSIONS" in acc
    illusions = (_ROOT / "enterprise" / "KNOWN_ILLUSIONS.md").read_text(encoding="utf-8")
    assert "66.2" in illusions
    assert "R-01" in illusions and "R-02" in illusions and "R-03" in illusions
    risk = (_ROOT / "enterprise" / "ANYDOC_RISK.md").read_text(encoding="utf-8")
    assert "xlsb" in risk.lower()
    assert "MIT" in risk
