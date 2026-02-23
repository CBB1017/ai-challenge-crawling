import os

from fastapi import APIRouter, HTTPException, Path
from loguru import logger

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


@router.post("/crawling/{action}")
async def crawling_action(
        action: str = Path(..., description="실행할 액션 이름")
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
    elif action == "meeting-room":
        crawler = MeetingRoomCrawler(login_info)
        data = await crawler.get_meeting_room_reservation()
        msg = f"회의실 예약 현황: {data['summary']}"
    else:
        raise HTTPException(status_code=400, detail="지원하지 않는 action")

    return data;
