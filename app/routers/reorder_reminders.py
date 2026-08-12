from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config.config import settings
from app.db.mongodb import get_database
from app.services.reorder_reminder_service import ReorderReminderService
from app.utils.deps import get_current_user_optional

router = APIRouter(prefix="/reorder-reminders", tags=["reorder-reminders"])


async def _require_admin_or_cron(
    current_user=Depends(get_current_user_optional),
    x_cron_secret: str | None = Header(default=None),
):
    """Allow either an authenticated admin (manual testing) or the shared
    cron secret (unattended GitHub Actions call) — same trusted-caller
    pattern as the revalidation endpoint's `x-revalidate-secret` header.
    """
    if current_user and current_user.get("is_admin", False):
        return
    if (
        settings.REORDER_REMINDER_CRON_SECRET
        and x_cron_secret
        and x_cron_secret == settings.REORDER_REMINDER_CRON_SECRET
    ):
        return
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Admin access or valid x-cron-secret header required",
    )


@router.post("/run")
async def run_reorder_reminders(
    dry_run: bool = True,
    db=Depends(get_database),
    _auth=Depends(_require_admin_or_cron),
):
    """Sweep delivered orders and send "running low" reminders that are due.

    `dry_run=true` (default) computes eligibility and returns the summary
    without sending any email or writing to `reorder_reminders` — use this
    to sanity-check depletion dates/skip reasons before flipping the daily
    cron over to real sends (`dry_run=false`).
    """
    service = ReorderReminderService(db)
    return await service.send_due_reminders(dry_run=dry_run)
