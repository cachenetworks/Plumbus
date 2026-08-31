from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.models.models import ApplicationSetting

router = APIRouter(tags=["entry"])


@router.get("/entry", include_in_schema=False)
def entry(db: Session = Depends(get_db)) -> RedirectResponse:
    state = db.get(ApplicationSetting, "setup_state")
    completed = bool(state and isinstance(state.value, dict) and state.value.get("completed"))
    return RedirectResponse("/browse" if completed else "/setup", status_code=302)
