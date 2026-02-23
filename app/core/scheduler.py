import os

import holidays
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from app.constants.attendance_const import AttendanceType, AttendanceStatus, AttendanceTime
from app.crawler.attendance import AttendanceCrawler
from datetime import datetime, timedelta, date
import httpx
from collections import defaultdict

TEST_TEAM = "DX 2Team"

scheduler = AsyncIOScheduler()
JOB_ID_PREFIX_ATTENDANCE = "attendance_"
JOB_ID_PREFIX_MORNING = "attendance_morning_"
KR_HOLIDAYS = holidays.country_holidays('KR', years=datetime.now().year)

def is_weekend(d: date | datetime) -> bool:
    if isinstance(d, datetime):
        d = d.date()
    return d.weekday() >= 5  # 5=토, 6=일

def is_holiday(d: date | datetime) -> bool:
    if isinstance(d, datetime):
        d = d.date()
    return d in KR_HOLIDAYS

def round_up_to_half_hour(dt):
    """dt를 30분 단위로 올림 (예: 07:57 → 08:00, 08:16 → 08:30)"""
    minute = (dt.minute // 30 + (1 if dt.minute % 30 else 0)) * 30
    if minute == 60:
        dt = dt.replace(hour=dt.hour + 1, minute=0)
    else:
        dt = dt.replace(minute=minute)
    return dt.replace(second=0, microsecond=0)

async def scheduled_attendance_task():
    logger.info(f"scheduled_attendance_task at {datetime.now()}")
    today = date.today()
    mute = is_weekend(today) or is_holiday(today)
    login_info = {
        "login_url": os.environ["GROUPWARE_DOMAIN"],
        "username": os.environ["LOGIN_ID"],
        "password": os.environ["LOGIN_PW"],
    }
    logger.info(f"Task 실행: {datetime.now()}")

    crawler = AttendanceCrawler(login_info)
    data = await crawler.run_attendance()
    if not data:
        logger.warning("근태 데이터를 가져올 수 없습니다.")
        return
    # DX 2Team 멤버 상태 주석 달기
    for team, members in data.items():
        data[team] = annotate_member_status(members, datetime.now(), mute_not_checked_in=mute)

    # (중요) 퇴근·OT 등 새로운 스케줄 등록: 출근자가 있으면 항상 최신 데이터로 퇴근/OT 등 등록
    schedule_attendance_jobs(data)

    # 출근 체크가 모두 완료되면 morning job만 삭제
    if all_checked_in(data):
        remove_all_morning_jobs()

    # 퇴근 조건까지 모두 만족 시 전체 job 삭제
    if should_reset_today(data):
        remove_all_attendance_jobs()

    # 백엔드로 데이터 전송
    backend_domain = os.environ["BACKEND_DOMAIN"]
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(backend_domain + '/api/crawling/attendance/import', json=data)
            logger.info(f"전송 결과: {resp.status_code} {resp.text}")
        except Exception as e:
            logger.error(f"HTTP 전송 실패: {e}")

def should_reset_today(data):
    test_team = data.get(TEST_TEAM, [])
    for member in test_team:
        plan_type = member.get("planType", "")
        actual_type = member.get("actualType", "")
        actual_in = member.get("actualIn", "")
        actual_out = member.get("actualOut", "")
        vacation = member.get("vacation", "")

        if ("근무일" in (plan_type, actual_type)) or ("외근" in (plan_type, actual_type)):
            if not actual_in or not actual_out:
                return False
            continue

        elif actual_type in ("반차", "반반차", "연차", "보상반일", "보상종일"):
            if vacation == "" or vacation == "반려":
                return False
            if actual_type in ("반차", "반반차", "보상반일") and not actual_out:
                return False
            continue

        else:
            return False

    return True

def all_checked_in(data):
    test_team = data.get(TEST_TEAM, [])
    for member in test_team:
        # 휴가, 연차 등은 제외
        if member.get("actualType", "") in ("연차", "휴가", "보상종일"):
            continue
        if not member.get("actualIn"):
            return False
    return True

def remove_all_attendance_jobs():
    """퇴근/저녁/초과근무 등 근무일 스케줄 job 전체 삭제"""
    for job in scheduler.get_jobs():
        if job.id.startswith(JOB_ID_PREFIX_ATTENDANCE):
            scheduler.remove_job(job.id)
            logger.info(f"퇴근 관련 attendance job id => {job.id} 삭제 완료.")

def remove_all_morning_jobs():
    """아침 출근체크 관련 job 전체 삭제"""
    for job in scheduler.get_jobs():
        if job.id.startswith(JOB_ID_PREFIX_MORNING):
            scheduler.remove_job(job.id)
            logger.info(f"아침 출근 체크(morning) job id => {job.id} 삭제 완료.")


def schedule_attendance_jobs(data):
    test_team = data.get(TEST_TEAM, [])
    scheduled_times = defaultdict(set)
    now = datetime.now()

    for member in test_team:
        actual_in = member.get("actualIn", "")
        actual_type = member.get("actualType", "")
        plan_type = member.get("planType", "")
        name = member.get("memberName", "")
        position = member.get("position", "")
        logger.info(f"name: {name} position:{position}")

        if not actual_in:
            continue

        try:
            base_time = datetime.combine(datetime.today(), datetime.strptime(actual_in, "%H:%M").time())
        except Exception:
            continue

        start_time = round_up_to_half_hour(base_time)

        if actual_type in (AttendanceType.HALF_DAY, AttendanceType.COMP_HALF):
            end_time = start_time + timedelta(hours=AttendanceTime.HALF_DAY_HOURS)
            special_check = end_time + timedelta(minutes=AttendanceTime.HALF_DAY_CHECK_MINUTES)
            if special_check > now:
                scheduled_times[special_check].add("퇴근")
                logger.info(f"반차/보상반일 퇴근 +{AttendanceTime.HALF_DAY_CHECK_MINUTES}m 체크: {special_check}")

        elif actual_type == AttendanceType.QUARTER_DAY:
            end_time = start_time + timedelta(hours=AttendanceTime.QUARTER_DAY_HOURS)
            special_check = end_time + timedelta(minutes=AttendanceTime.QUARTER_DAY_CHECK_MINUTES)
            if special_check > now:
                scheduled_times[special_check].add("퇴근")
                logger.info(f"반반차 퇴근 +{AttendanceTime.QUARTER_DAY_CHECK_MINUTES}m 체크: {special_check}")

        elif plan_type in (AttendanceType.WORKDAY, AttendanceType.OUTWORK) or actual_type in (AttendanceType.WORKDAY, AttendanceType.OUTWORK):
            work_end = start_time + timedelta(hours=AttendanceTime.WORK_HOURS)
            break_end = start_time + timedelta(hours=AttendanceTime.BREAK_HOURS)
            ot_start = start_time + timedelta(hours=AttendanceTime.OVERTIME_HOURS)

            t1 = work_end + timedelta(minutes=AttendanceTime.AFTER_WORK_CHECK_MINUTES)
            if t1 > now:
                scheduled_times[t1].add("퇴근")
                logger.info(f"일반근무 퇴근 {AttendanceTime.AFTER_WORK_CHECK_MINUTES}분후 체크: {t1}")

            # t2 = break_end - timedelta(minutes=AttendanceTime.BEFORE_BREAK_CHECK_MINUTES)
            # if t2 > now and t2 > t1:
            #     scheduled_times[t2].add("휴식")
            #     logger.info(f"일반근무 휴식 끝 {AttendanceTime.BEFORE_BREAK_CHECK_MINUTES}분전 체크: {t2}")
            #
            # t3 = ot_start - timedelta(minutes=AttendanceTime.BEFORE_OVERTIME_CHECK_MINUTES)
            # if t3 > now and t3 > t2:
            #     scheduled_times[t3].add("OT")
            #     logger.info(f"일반근무 초과근무 {AttendanceTime.BEFORE_OVERTIME_CHECK_MINUTES}분전 체크: {t3}")

    for t in sorted(scheduled_times):
        job_id = f"{JOB_ID_PREFIX_ATTENDANCE}_{t.strftime('%H%M')}"
        scheduler.add_job(
            scheduled_attendance_task,
            'date',
            run_date=t,
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"Attendance 스케줄 등록(중복 없음): {t}, types: {list(scheduled_times[t])}")

def schedule_morning_check_jobs():
    now = datetime.now()
    t = now.replace(hour=7, minute=55, second=0, microsecond=0)
    end_time = now.replace(hour=16, minute=0, second=0, microsecond=0)

    while t <= end_time:
        job_id = f"{JOB_ID_PREFIX_MORNING}{t.strftime('%H%M')}"
        scheduler.add_job(
            scheduled_attendance_task,
            'date',
            run_date=t,
            id=job_id,
            replace_existing=True,
        )
        logger.info(f"아침 출근 체크 스케줄 등록: {t}")
        t += timedelta(minutes=30)


def annotate_member_status(members, now, mute_not_checked_in: bool = False):
    for m in members:
        actual_in = m.get("actualIn")
        actual_out = m.get("actualOut")
        actual_type = m.get("actualType", "")
        vacation = m.get("vacation", "")
        m["status"] = AttendanceStatus.IDLE

        # 휴가 처리
        if actual_type in (AttendanceType.LEAVE, AttendanceType.VACATION, AttendanceType.COMP_FULL) or vacation not in ("", "반려"):
            m["status"] = AttendanceStatus.ON_LEAVE
            continue
        # 미출근
        if not actual_in:
            if ( now.hour == 9 and now.minute == 55 ) and not mute_not_checked_in:
                m["status"] = AttendanceStatus.NOT_CHECKED_IN_NOTIFICATION
            else:
                m["status"] = AttendanceStatus.NOT_CHECKED_IN
            continue

        base_time = datetime.combine(datetime.today(), datetime.strptime(actual_in, "%H:%M").time())
        start_time = round_up_to_half_hour(base_time)

        work_end = start_time + timedelta(hours=AttendanceTime.WORK_HOURS)
        ot_start = start_time + timedelta(hours=AttendanceTime.OVERTIME_HOURS)
        break_end = start_time + timedelta(hours=AttendanceTime.BREAK_HOURS)

        if actual_type in (AttendanceType.HALF_DAY, AttendanceType.COMP_HALF):
            work_end = start_time + timedelta(hours=AttendanceTime.HALF_DAY_HOURS)
        elif actual_type == AttendanceType.QUARTER_DAY:
            work_end = start_time + timedelta(hours=AttendanceTime.QUARTER_DAY_HOURS)

        # 멤버별 알림/상태 판정 (순서 중요!)
        if not actual_out:
            if work_end < now < break_end:
                m["status"] = AttendanceStatus.NEED_BREAK_NOTIFICATION
            elif now >= ot_start:
                m["status"] = AttendanceStatus.NEED_OVERTIME_NOTIFICATION
            elif now < work_end:
                m["status"] = AttendanceStatus.WORKING
            elif work_end <= now < ot_start:
                m["status"] = AttendanceStatus.WORK_TIME_OVER
            continue

        out_time = datetime.combine(datetime.today(), datetime.strptime(actual_out, "%H:%M").time())
        if out_time < work_end:
            m["status"] = AttendanceStatus.EARLY_CHECKED_OUT  # 조기 퇴근
        elif work_end <= out_time <= ot_start:
            m["status"] = AttendanceStatus.CHECKED_OUT  # 정상 퇴근
        else:
            m["status"] = AttendanceStatus.CHECKED_OUT_OT  # 초과근무 퇴근
    return members



async def daily_scheduler():
    logger.info("새로운 하루 스케줄 등록")
    remove_all_attendance_jobs()
    remove_all_morning_jobs()
    today = date.today()
    if is_holiday(today):
        logger.info("공휴일이라 스케줄러 실행하지 않음")
        return

    schedule_morning_check_jobs()
    # 오늘자 출근자 최신 데이터로 스케줄링
    login_info = {
        "login_url": os.environ["GROUPWARE_DOMAIN"],
        "username": os.environ["LOGIN_ID"],
        "password": os.environ["LOGIN_PW"],
    }
    crawler = AttendanceCrawler(login_info)
    data = await crawler.run_attendance()
    if data:
        schedule_attendance_jobs(data)
    else:
        logger.warning("스케줄 등록용 근태 데이터 없음")

# 최초 스케줄러 등록(아침 7:54에 매일 스케줄링)
scheduler.add_job(
    daily_scheduler,
    'cron',
    hour=7, minute=54,
    id="main_daily_scheduler",
    replace_existing=True,
)

async def init_scheduler():
    logger.info("init_scheduler 진입")
    today = date.today()
    mute = is_weekend(today) or is_holiday(today)
    schedule_morning_check_jobs()
    # 1. 앱 시작시, 오늘 출근자 기반으로 오늘 스케줄 등록
    login_info = {
        "login_url": os.environ["GROUPWARE_DOMAIN"],
        "username": os.environ["LOGIN_ID"],
        "password": os.environ["LOGIN_PW"],
    }
    crawler = AttendanceCrawler(login_info)
    data = await crawler.run_attendance()
    if data:
        # DX 2Team 멤버 상태 주석 달기
        for team, members in data.items():
            data[team] = annotate_member_status(members, datetime.now(), mute_not_checked_in=mute)
        # 출근 완료된 상태면 morning 체크 스케줄 제거!
        if all_checked_in(data):
            remove_all_morning_jobs()
            logger.info("초기 상태에서 전원 출근 완료, 아침 출근체크(morning) 스케줄 즉시 삭제")
        schedule_attendance_jobs(data)
    else:
        logger.warning("초기 스케줄 등록용 데이터 없음")

    logger.info("init_scheduler 완료")
    return scheduler
