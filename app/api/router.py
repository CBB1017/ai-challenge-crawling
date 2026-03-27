import os
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Path
from loguru import logger
from typing import Optional, Any
from app.core.scheduler import annotate_member_status, all_checked_in, remove_all_morning_jobs
from app.crawler.attendance import AttendanceCrawler
from app.crawler.meeting import MeetingRoomCrawler
import httpx

from app.crawler.member import MemberCrawler

router = APIRouter()


async def send_slack_message(webhook_url, text, channel=None):
    payload = {"text": text}
    if channel:
        payload["channel"] = channel
    async with httpx.AsyncClient() as client:
        await client.post(webhook_url, json=payload)

class CrawlerRequest(BaseModel):
    params: Optional[dict[str, Any]] = None

@router.post("/crawling/{action}")
async def crawling_action(
    action: str = Path(..., description="실행할 액션 이름"),
    request_data: Optional[CrawlerRequest] = None # AI가 분석한 세부 파라미터 수신
):
    login_info = {
        "login_url": os.environ["GROUPWARE_DOMAIN"],
        "username": os.environ["LOGIN_ID"],
        "password": os.environ["LOGIN_PW"],
    }

    if action == "attendance":
        crawler = AttendanceCrawler(login_info)
        data = await crawler.run_attendance()
        if not data:
            logger.info("근태 데이터를 가져올 수 없습니다.")
            return None
        # DX 2Team 멤버 상태 주석 달기
        for team, members in data.items():
            from datetime import datetime
            data[team] = annotate_member_status(members, datetime.now())
        # 출근 완료된 상태면 morning 체크 스케줄 제거!
        if all_checked_in(data):
            remove_all_morning_jobs()
            logger.info("초기 상태에서 전원 출근 완료, 아침 출근체크(morning) 스케줄 즉시 삭제")
        msg = f"근태 데이터: {data}"
    elif action == "member":
        crawler = MemberCrawler(login_info)
        data = await crawler.run_team_member_tree()
        if not data:
            logger.info("회원 데이터를 가져올 수 없습니다.")
            return None
        msg = f"회원 데이터: {data}"
    # 2. 회의실 예약 (Meeting Room)
    elif action == "meeting-room":
        # AI가 특정 시간이나 회의실 이름을 보냈을 경우 처리 가능
        target_room = request_data.params.get("room_name") if request_data else None
        crawler = MeetingRoomCrawler(login_info)
        data = await crawler.fetch_reservations(room_name=target_room)
        return data
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 action")

    return data;
