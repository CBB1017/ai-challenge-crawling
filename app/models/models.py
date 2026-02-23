from pydantic import BaseModel
from typing import Optional

class ActionEnum(str):
    attendance = "attendance"
    meeting_room = "meeting-room"
    checkin_missing = "checkin-missing"

class CrawlRequest(BaseModel):
    id: str
    password: str
    year: Optional[int] = None
    month: Optional[int] = None
    slack_webhook: Optional[str] = None
    slack_channel: Optional[str] = None
