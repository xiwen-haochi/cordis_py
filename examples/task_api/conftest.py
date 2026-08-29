"""pytest 配置：测试使用独立 SQLite 库（与开发库隔离）。

app.yml 的 sqlite 插件默认写 ``examples/task_api/tasks.db``；测试若复用
它会跨会话残留数据（本 bundle 的 CRUD/隔离测试假定“每测试全新数据”）。
这里对所有测试注入 ``CORDIS_SQLITE_PATH``（每测试独立临时文件），
优先级：env > config path，sqlite 插件已支持。生产/开发运行不受影响。
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_sqlite(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORDIS_SQLITE_PATH", str(tmp_path / "test_tasks.db"))
