"""FastAPI app: contractor dashboard, knowledge-base upload, call simulator, Twilio webhooks."""
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from app import accounts, agent, chunking, knowledge, voice
from app.accounts import DEFAULT_HOURS
from app.config import settings
from app.db import get_db, init_db, row_to_dict

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="Contractor Call Agent", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret, same_site="lax")
app.include_router(voice.router)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024


class LoginRequired(Exception):
    pass


@app.exception_handler(LoginRequired)
def _to_login(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def current_contractor(request: Request) -> dict:
    cid = request.session.get("contractor_id")
    contractor = agent.get_contractor(cid) if cid else None
    if contractor is None:
        raise LoginRequired()
    return contractor


def render(request: Request, name: str, contractor: dict | None = None, **ctx) -> HTMLResponse:
    return templates.TemplateResponse(request, name, {"me": contractor, **ctx})


# ---------------------------------------------------------------- auth

@app.get("/", include_in_schema=False)
def home(request: Request):
    return RedirectResponse("/dashboard" if request.session.get("contractor_id") else "/login", status_code=303)


@app.get("/login")
def login_page(request: Request):
    return render(request, "login.html")


@app.post("/login")
def login(request: Request, email: str = Form(...), password: str = Form(...)):
    contractor = accounts.authenticate(email, password)
    if not contractor:
        return render(request, "login.html", error="Wrong email or password.")
    request.session["contractor_id"] = contractor["id"]
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/signup")
def signup_page(request: Request):
    return render(request, "signup.html")


@app.post("/signup")
def signup(request: Request, email: str = Form(...), password: str = Form(...), business_name: str = Form(...),
           owner_name: str = Form(...), trade: str = Form("roofing contractor")):
    if len(password) < 8:
        return render(request, "signup.html", error="Password must be at least 8 characters.")
    try:
        cid = accounts.create_contractor(email, password, business_name, owner_name, trade)
    except Exception:
        return render(request, "signup.html", error="That email is already registered.")
    request.session["contractor_id"] = cid
    return RedirectResponse("/knowledge", status_code=303)


@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------------------------------------------------------------- dashboard

@app.get("/dashboard")
def dashboard(request: Request, me: dict = Depends(current_contractor)):
    with get_db() as db:
        calls = [dict(r) for r in db.execute(
            "SELECT * FROM calls WHERE contractor_id=? ORDER BY id DESC LIMIT 10", (me["id"],))]
        events = [row_to_dict(r) for r in db.execute(
            "SELECT * FROM events WHERE contractor_id=? ORDER BY start LIMIT 10", (me["id"],))]
        stats = dict(db.execute(
            "SELECT count(*) AS calls, sum(outcome='visit_booked') AS visits, sum(outcome='proposal_sent') AS proposals, "
            "sum(outcome='escalated') AS escalations FROM calls WHERE contractor_id=?", (me["id"],)).fetchone())
    return render(request, "dashboard.html", me, calls=calls, events=events, stats=stats,
                  kb=knowledge.kb_status(me["id"]))


# ---------------------------------------------------------------- knowledge base

@app.get("/knowledge")
def knowledge_page(request: Request, me: dict = Depends(current_contractor)):
    return render(request, "knowledge.html", me, docs=knowledge.list_documents(me["id"]),
                  kb=knowledge.kb_status(me["id"]), supported=", ".join(knowledge.SUPPORTED),
                  flash=request.session.pop("flash", None))


@app.post("/knowledge/upload")
async def upload(request: Request, files: list[UploadFile] = File(...), me: dict = Depends(current_contractor)):
    messages = []
    before = knowledge.kb_status(me["id"])["mode"]
    for f in files:
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            messages.append(f"{f.filename}: too large (max 20 MB)")
            continue
        try:
            text = knowledge.extract_text(f.filename, data)
        except Exception as exc:
            messages.append(f"{f.filename}: {exc}")
            continue
        if not text.strip():
            messages.append(f"{f.filename}: no readable text (scanned PDF?)")
            continue
        result = knowledge.add_document(me["id"], f.filename, text)
        messages.append(f"{f.filename}: {result['chunks']} chunks")
        if result.get("sync_error"):
            messages.append(f"Pinecone sync failed, using local search for now: {result['sync_error']}")
    after = knowledge.kb_status(me["id"])
    if after["mode"] != before:
        messages.append(f"Knowledge base is now {after['tokens']:,} tokens, so it switched to "
                        f"{'Pinecone retrieval' if after['mode'] == 'retrieval' else 'full-context'} mode.")
    request.session["flash"] = messages
    return RedirectResponse("/knowledge", status_code=303)


@app.post("/knowledge/{doc_id}/delete")
def delete_doc(request: Request, doc_id: int, me: dict = Depends(current_contractor)):
    status = knowledge.delete_document(me["id"], doc_id)
    request.session["flash"] = [f"Deleted. Knowledge base mode: {status['mode']}."]
    return RedirectResponse("/knowledge", status_code=303)


@app.get("/knowledge/{doc_id}")
def view_doc(request: Request, doc_id: int, me: dict = Depends(current_contractor)):
    """Stored chunks, or a live preview of any chunking strategy (?method=&size=&overlap=&context=)."""
    with get_db() as db:
        doc = db.execute("SELECT * FROM documents WHERE id=? AND contractor_id=?", (doc_id, me["id"])).fetchone()
        if not doc:
            raise HTTPException(404)
        stored = [dict(r) for r in db.execute("SELECT * FROM chunks WHERE document_id=? ORDER BY idx", (doc_id,))]
    q = request.query_params
    preview, cfg, error = None, chunking.config_from_env(), None
    if q.get("method"):
        try:
            cfg = chunking.ChunkConfig(method=q["method"], max_chars=int(q.get("size") or 800),
                                       overlap=int(q.get("overlap") or 0),
                                       context=q.get("context") if q.get("context") in ("none", "path") else "path")
            preview = chunking.chunk(doc["content"], doc["filename"], cfg)
        except ValueError as exc:
            error = str(exc)
    return render(request, "document.html", me, doc=dict(doc), chunks=stored, preview=preview, cfg=cfg,
                  error=error, methods=chunking.METHODS)


@app.get("/knowledge-search")
def try_search(request: Request, q: str = "", me: dict = Depends(current_contractor)):
    """Debug view: what would retrieval return for this question?"""
    found = knowledge.search(me["id"], q) if q else None
    return render(request, "search.html", me, q=q, found=found, kb=knowledge.kb_status(me["id"]))


# ---------------------------------------------------------------- settings

@app.get("/settings")
def settings_page(request: Request, me: dict = Depends(current_contractor)):
    return render(request, "settings.html", me, days=list(DEFAULT_HOURS), saved=request.query_params.get("saved"))


@app.post("/settings")
async def save_settings(request: Request, me: dict = Depends(current_contractor)):
    form = await request.form()
    hours = {}
    for day in DEFAULT_HOURS:
        o, c = form.get(f"{day}_open"), form.get(f"{day}_close")
        hours[day] = [o, c] if o and c and not form.get(f"{day}_closed") else None
    accounts.update_settings(
        me["id"], business_name=form.get("business_name"), owner_name=form.get("owner_name"),
        trade=form.get("trade"), phone_number=form.get("phone_number"), notify_email=form.get("notify_email"),
        notify_phone=form.get("notify_phone"), timezone=form.get("timezone"), calendar_mailbox=form.get("calendar_mailbox"),
        visit_duration_min=int(form.get("visit_duration_min") or 60), business_hours=hours)
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------------------------------------------------------------- calls, outbox

@app.get("/calls")
def calls_page(request: Request, me: dict = Depends(current_contractor)):
    with get_db() as db:
        calls = [dict(r) for r in db.execute("SELECT * FROM calls WHERE contractor_id=? ORDER BY id DESC", (me["id"],))]
    return render(request, "calls.html", me, calls=calls)


@app.get("/calls/{call_id}")
def call_detail(request: Request, call_id: int, me: dict = Depends(current_contractor)):
    with get_db() as db:
        call = db.execute("SELECT * FROM calls WHERE id=? AND contractor_id=?", (call_id, me["id"])).fetchone()
        if not call:
            raise HTTPException(404)
        transcript = [dict(r) for r in db.execute("SELECT * FROM transcript WHERE call_id=? ORDER BY id", (call_id,))]
        actions = [row_to_dict(r) for r in db.execute("SELECT * FROM actions WHERE call_id=? ORDER BY id", (call_id,))]
        emails = [dict(r) for r in db.execute("SELECT id, to_addr, subject, provider, created_at FROM outbox "
                                              "WHERE call_id=? ORDER BY id", (call_id,))]
        events = [row_to_dict(r) for r in db.execute("SELECT * FROM events WHERE call_id=?", (call_id,))]
    return render(request, "call_detail.html", me, call=dict(call), transcript=transcript, actions=actions,
                  emails=emails, events=events)


@app.get("/outbox")
def outbox(request: Request, me: dict = Depends(current_contractor)):
    with get_db() as db:
        emails = [dict(r) for r in db.execute("SELECT id, call_id, to_addr, subject, provider, created_at, "
                                              "ics IS NOT NULL AS has_invite FROM outbox WHERE contractor_id=? "
                                              "ORDER BY id DESC", (me["id"],))]
    return render(request, "outbox.html", me, emails=emails)


@app.get("/outbox/{email_id}")
def email_detail(request: Request, email_id: int, me: dict = Depends(current_contractor)):
    with get_db() as db:
        email = db.execute("SELECT * FROM outbox WHERE id=? AND contractor_id=?", (email_id, me["id"])).fetchone()
    if not email:
        raise HTTPException(404)
    return render(request, "email.html", me, email=dict(email))


# ---------------------------------------------------------------- simulator (browser "call")

@app.get("/simulator")
def simulator(request: Request, me: dict = Depends(current_contractor)):
    return render(request, "simulator.html", me, kb=knowledge.kb_status(me["id"]))


class Turn(BaseModel):
    text: str


@app.post("/api/sim/start")
def sim_start(request: Request, me: dict = Depends(current_contractor)):
    return agent.start_call(me["id"], "simulator", caller_number=request.query_params.get("caller") or "+15555550123")


def _own_call(external_id: str, me: dict) -> dict:
    call = agent.get_call(external_id)
    if not call or call["contractor_id"] != me["id"]:
        raise HTTPException(404)
    return call


@app.post("/api/sim/{external_id}/turn")
def sim_turn(external_id: str, body: Turn, me: dict = Depends(current_contractor)):
    call = _own_call(external_id, me)
    result = agent.handle_turn(external_id, body.text)
    return {**result, "call_db_id": call["id"]}


@app.post("/api/sim/{external_id}/hangup")
def sim_hangup(external_id: str, me: dict = Depends(current_contractor)):
    _own_call(external_id, me)
    agent.hang_up(external_id)
    return {"ok": True}


@app.get("/health")
def health():
    return {"ok": True, "model": settings.agent_model, "pinecone": bool(settings.pinecone_api_key),
            "graph": settings.graph_enabled, "llm_provider": settings.llm_provider, "llm_configured": settings.llm_configured}


templates.env.filters["fromjson"] = json.loads
