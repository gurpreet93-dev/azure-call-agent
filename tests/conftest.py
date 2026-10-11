import os
import tempfile

os.environ["DATABASE_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.pop("PINECONE_API_KEY", None)
os.environ.pop("MS_TENANT_ID", None)

import pytest  # noqa: E402

from app.db import get_db, init_db  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_db():
    init_db()
    with get_db() as db:
        for t in ("outbox", "events", "actions", "transcript", "calls", "chunks", "documents", "contractors"):
            db.execute(f"DELETE FROM {t}")
    yield


@pytest.fixture
def contractor():
    from app import accounts, agent
    cid = accounts.create_contractor("t@example.com", "password1", "Summit Roofing & Exteriors", "Alex",
                                     notify_email="owner@example.com")
    return agent.get_contractor(cid)
