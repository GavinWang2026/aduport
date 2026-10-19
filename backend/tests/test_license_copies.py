"""许可与署名文件的副本一致性门禁。

`NOTICE` 与 `THIRD_PARTY_NOTICES.md` 在两处存在：
- 仓库根：GitHub / 法务阅读用；
- `backend/`：构建根目录，wheel 与 sdist 从这里取（hatch 的 force-include 与 include）。

为什么不能只留一份：`python -m build` 会先打 sdist、解包到临时目录、再从那里构建 wheel。
构建根目录之外的路径（`../NOTICE`）在临时目录里不存在，会直接构建失败（已实测）。
所以必须复制，并用本测试守住两份不许漂移。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# (仓库根文件, backend 内的构建副本)
PAIRS = [
    ("NOTICE", "NOTICE"),
    ("THIRD_PARTY_NOTICES.md", "THIRD_PARTY_NOTICES.md"),
]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize(("root_name", "build_name"), PAIRS)
def test_license_copy_matches_repo_root(root_name: str, build_name: str) -> None:
    root_file = REPO_ROOT / root_name
    build_file = BACKEND_DIR / build_name

    assert root_file.is_file(), f"仓库根缺少 {root_name}"
    assert build_file.is_file(), f"backend/ 缺少 {build_name}（打包会漏掉署名）"
    assert _digest(root_file) == _digest(build_file), (
        f"{root_name} 与 backend/{build_name} 内容不一致。"
        "改了其中一份必须同步另一份，否则发出去的 wheel 里的署名会和仓库对不上。"
    )


def test_notice_names_the_copyright_holder() -> None:
    """NOTICE 不能留着占位符，否则等于没有授权人。"""
    text = (BACKEND_DIR / "NOTICE").read_text(encoding="utf-8")
    assert "[yyyy]" not in text
    assert "[name of copyright owner]" not in text
    assert "Heying Huitong Intelligent Technology (Suzhou) Co., Ltd." in text


def test_license_appendix_is_filled() -> None:
    """Apache-2.0 附录的占位符必须已填，打包进 wheel 的那份同样要查。"""
    text = (BACKEND_DIR / "LICENSE").read_text(encoding="utf-8")
    assert "[yyyy]" not in text
    assert "[name of copyright owner]" not in text
    assert "Apache License" in text
