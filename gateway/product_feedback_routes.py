"""Private voluntary owner feedback. No email/outreach or public case consent."""
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
import config
from logic import accounts, csrf, db as control_db, product_feedback
from rate_limiter import RateLimiter
from template_setup import create_templates

router = APIRouter()
templates = create_templates()
limiter = RateLimiter(requests_per_window=10, window_seconds=60, storage_backend="db", db_connect=control_db.connect)


def _owner(request):
    token = request.cookies.get(config.SESSION_COOKIE_NAME)
    if not token:
        return None
    with control_db.connect() as conn:
        account = accounts.resolve_session(conn,token)
        if account is None:
            return None
        tenant = accounts.get_tenant_for_account(conn,int(account["id"]))
        if tenant is None or tenant["slug"] == "demo":
            raise HTTPException(403,"Форма доступна владельцу собственного кабинета.")
    return account


def _page(request, account, *, error="", status=200):
    with control_db.connect() as conn:
        responses = [dict(r) for r in conn.execute("SELECT * FROM product_feedback WHERE account_id=? ORDER BY updated_at DESC LIMIT 20",(account["id"],))]
    token = csrf.get_or_create_token(request)
    today = datetime.now(timezone.utc).date()
    response = templates.TemplateResponse(request,"product_feedback.html",{"csrf_token":token,"error":error,"saved":request.query_params.get("saved")=="1","scenarios":product_feedback.SCENARIOS,"reasons":product_feedback.REASONS,"responses":responses,"start":(today-timedelta(days=29)).isoformat(),"end":today.isoformat()},status_code=status)
    response.headers["Cache-Control"] = "private, no-store"
    csrf.set_cookie(response,token)
    return response


@router.get("/feedback",response_class=HTMLResponse)
def feedback_page(request:Request):
    account = _owner(request)
    if account is None:
        return RedirectResponse("/login?next=%2Ffeedback",status_code=303)
    return _page(request,account)


@router.post("/feedback")
def feedback_save(request:Request, scenario:str=Form(...,max_length=32), period_start:str=Form(...,max_length=10), period_end:str=Form(...,max_length=10), reason:str=Form("none",max_length=32), actual_minutes:str=Form("",max_length=5), csrf_token:str=Form("",max_length=128), action:str=Form("save",max_length=8)):
    account = _owner(request)
    if account is None:
        return RedirectResponse("/login?next=%2Ffeedback",status_code=303)
    if not csrf_token.isascii() or not csrf.verify(request,csrf_token):
        raise HTTPException(403,"Обновите страницу формы и повторите отправку.")
    if not limiter.is_allowed(f"feedback:{account['id']}"):
        raise HTTPException(429,"Слишком много отправок. Повторите через минуту.")
    try:
        with control_db.connect() as conn:
            if action == "save":
                product_feedback.save(conn,int(account["id"]),scenario,period_start,period_end,reason,actual_minutes)
            elif action == "delete":
                conn.execute("DELETE FROM product_feedback WHERE account_id=? AND scenario=? AND period_start=? AND period_end=?",(account["id"],scenario,period_start,period_end))
            else:
                raise ValueError("Неизвестное действие.")
    except ValueError as exc:
        return _page(request,account,error=str(exc),status=422)
    return RedirectResponse("/feedback?saved=1",status_code=303,headers={"Cache-Control":"private, no-store"})
