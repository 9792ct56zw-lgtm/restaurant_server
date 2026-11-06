"""AI 经营分析接口：日报/周报/月报/年报生成、报告归档、对话问答。"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..deps import require_roles
from ..services import ai_service

router = APIRouter(prefix="/api/admin/ai", tags=["ai"])

admin_cashier = require_roles(models.ROLE_ADMIN, models.ROLE_CASHIER)


@router.get("/reports")
def reports(type: str = "", db: Session = Depends(get_db), staff=Depends(admin_cashier)):
    q = db.query(models.AIReport)
    if type:
        q = q.filter(models.AIReport.type == type)
    rows = q.order_by(models.AIReport.id.desc()).limit(50).all()
    return [
        {"id": r.id, "type": r.type, "content": r.content,
         "period_start": r.period_start.strftime("%Y-%m-%d") if r.period_start else "",
         "period_end": r.period_end.strftime("%Y-%m-%d") if r.period_end else "",
         "created_at": r.created_at.strftime("%Y-%m-%d %H:%M")}
        for r in rows
    ]


@router.post("/reports")
def generate(body: schemas.ReportGenIn, db: Session = Depends(get_db),
             staff=Depends(admin_cashier)):
    try:
        report = ai_service.generate_report(db, body.type)
    except ValueError:
        return {"error": "报告类型无效（daily/weekly/monthly/yearly）"}
    return {"id": report.id, "type": report.type, "content": report.content,
            "created_at": report.created_at.strftime("%Y-%m-%d %H:%M")}


@router.post("/chat")
def chat(body: schemas.ChatIn, db: Session = Depends(get_db), staff=Depends(admin_cashier)):
    return {"answer": ai_service.chat(db, body.question)}
