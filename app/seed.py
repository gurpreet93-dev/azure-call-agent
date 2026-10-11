"""Create the demo contractor and load the sample knowledge base.

    python -m app.seed            # creates demo@summit.test / demo1234 if missing
"""
from pathlib import Path

from app import accounts, knowledge
from app.db import get_db, init_db

DEMO_EMAIL = "demo@summit.test"
DEMO_PASSWORD = "demo1234"
SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_kb"


def seed() -> int:
    init_db()
    with get_db() as db:
        row = db.execute("SELECT id FROM contractors WHERE email=?", (DEMO_EMAIL,)).fetchone()
    if row:
        print(f"Demo contractor already exists (id {row['id']}).")
        return row["id"]
    cid = accounts.create_contractor(
        DEMO_EMAIL, DEMO_PASSWORD, "Summit Roofing & Exteriors", "Alex Morgan", trade="roofing contractor",
        notify_email="owner@summit.test", timezone="America/Toronto",
        business_hours={"mon": ["08:00", "18:00"], "tue": ["08:00", "18:00"], "wed": ["08:00", "18:00"],
                        "thu": ["08:00", "18:00"], "fri": ["08:00", "18:00"], "sat": ["09:00", "14:00"], "sun": None},
        visit_duration_min=60,
    )
    for path in sorted(SAMPLE_DIR.iterdir()):
        if path.suffix.lower() in knowledge.SUPPORTED:
            text = knowledge.extract_text(path.name, path.read_bytes())
            result = knowledge.add_document(cid, path.name, text)
            print(f"  loaded {path.name}: {result['chunks']} chunks")
    print(f"Created demo contractor {DEMO_EMAIL} / {DEMO_PASSWORD} (id {cid}). KB: {knowledge.kb_status(cid)}")
    return cid


if __name__ == "__main__":
    seed()
