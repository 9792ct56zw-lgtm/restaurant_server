"""定时任务：AI 经营报告自动生成（日报/周报/月报），密钥未配置时走规则模板兜底。"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .database import SessionLocal
from .services import ai_service


def _gen(report_type: str):
    db = SessionLocal()
    try:
        ai_service.generate_report(db, report_type)
    except Exception:
        pass
    finally:
        db.close()


scheduler = BackgroundScheduler(timezone="Asia/Shanghai")


def start_scheduler():
    try:
        if not scheduler.get_job("ai_daily"):
            scheduler.add_job(_gen, CronTrigger(hour=21, minute=30), args=["daily"], id="ai_daily")
        if not scheduler.get_job("ai_weekly"):
            scheduler.add_job(_gen, CronTrigger(day_of_week="mon", hour=22, minute=0),
                              args=["weekly"], id="ai_weekly")
        if not scheduler.get_job("ai_monthly"):
            scheduler.add_job(_gen, CronTrigger(day=1, hour=22, minute=30),
                              args=["monthly"], id="ai_monthly")
        if not scheduler.get_job("ai_yearly"):
            scheduler.add_job(_gen, CronTrigger(month=1, day=1, hour=23, minute=0),
                              args=["yearly"], id="ai_yearly")
        if not scheduler.running:
            scheduler.start()
    except Exception:
        pass


def stop_scheduler():
    try:
        scheduler.shutdown(wait=False)
    except Exception:
        pass
