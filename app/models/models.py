from datetime import datetime, timedelta
from typing import Optional, Literal, Any, List

from loguru import logger
from pydantic import BaseModel, Field, AliasChoices, field_validator, model_validator


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


class OvertimeRequestModel(BaseModel):
    request_type: Literal["oneday", "monthly"] = Field(
        default="oneday",
        description="'이번 달', '지난 달' 등 월 전체면 'monthly', 특정 하루면 'oneday'"
    )

    # 🚀 LLM에게 '오늘'을 계산하지 말라고 강력히 경고
    ot_date: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices('ot_date', 'work_date', 'date'),
        description="[oneday] 명확한 날짜(YYYY-MM-DD)만 입력. 만약 사용자가 '오늘', '어제'라고 하면 절대 임의로 날짜를 계산하지 말고 그 단어('오늘', '어제')를 그대로 문자열로 입력하거나 null로 두세요."
    )

    # 🚀 연도/월도 임의 계산 금지
    target_year: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices('target_year', 'request_year', 'year'),
        description="[monthly] 명확히 '2024년'이라고 한 경우만 입력. '올해', '이번달'인 경우 절대 계산하지 말고 null로 두세요."
    )

    target_month: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices('target_month', 'request_month', 'month'),
        description="[monthly] 명확히 '5월'인 경우만 숫자 5 입력. '이번달', '저번달' 같은 표현이면 절대 임의로 숫자를 계산하지 말고 '이번달', '지난달'을 그대로 문자열로 넣거나 null로 두세요."
    )

    action_type: Literal["T", "F"] = Field(default="T")
    memo: str = Field(default=".")
    doc_type: Literal["OT", "특근"] = Field(default="OT")

    @field_validator('action_type', mode='before')
    @classmethod
    def transform_action_type(cls, v):
        if isinstance(v, bool):
            return "T" if v else "F"
        return v

    # 🚀 [업데이트] YYYY-MM 파싱 로직 최상단 추가
    @model_validator(mode='before')
    @classmethod
    def preprocess_and_resolve_dates(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        now = datetime.now()
        rt = data.get('request_type', 'oneday')

        if rt == 'monthly':
            ty = data.get('target_year') or data.get('year') or data.get('request_year')
            tm = data.get('target_month') or data.get('month') or data.get('request_month')
            od = data.get('ot_date') or data.get('date') or data.get('work_date')

            # 1. "2026-03" 또는 "2026-03-01" 형태가 ty, tm, od 중 어디라도 들어오면 가로채서 쪼갭니다.
            for val in (ty, tm, od):
                if isinstance(val, str) and '-' in val:
                    parts = val.split('-')
                    if len(parts) >= 2:
                        data['target_year'] = parts[0]
                        data['target_month'] = parts[1]
                        break  # 하나라도 정상적으로 쪼갰으면 루프 종료

            # 방금 쪼개진 값이 있을 수 있으므로 tm 다시 확인
            tm = data.get('target_month') or data.get('month')

            # 2. LLM이 "이번달", "지난달" 등의 자연어를 보냈을 때 처리
            # (만약 위 1번에서 "03" 등으로 파싱되었으면 isdigit()이 True라 이 부분은 건너뜁니다)
            if isinstance(tm, str) and not tm.isdigit():
                if '지난' in tm or '저번' in tm or '전' in tm:
                    prev_month_date = now.replace(day=1) - timedelta(days=1)
                    data['target_year'] = prev_month_date.year
                    data['target_month'] = prev_month_date.month
                elif '이번' in tm or '이' in tm:
                    data['target_year'] = now.year
                    data['target_month'] = now.month

            # 3. 그래도 값이 아예 비워져 있다면, 시스템의 현재 연/월로 방어
            if not data.get('target_year'):
                data['target_year'] = now.year
            if not data.get('target_month'):
                data['target_month'] = now.month

        elif rt == 'oneday':
            od = data.get('ot_date') or data.get('date') or data.get('work_date')

            if isinstance(od, str):
                if '어제' in od or '전날' in od:
                    data['ot_date'] = (now - timedelta(days=1)).strftime("%Y-%m-%d")
                elif '오늘' in od or '금일' in od:
                    data['ot_date'] = now.strftime("%Y-%m-%d")

            # 비워둔 경우 무조건 '오늘'로 세팅
            if not data.get('ot_date'):
                data['ot_date'] = now.strftime("%Y-%m-%d")

        return data

    @model_validator(mode='after')
    def validate_final_state(self) -> 'OvertimeRequestModel':
        # 변환이 끝난 후, 최종적으로 값이 예쁘게 들어갔는지 확인 (타입 보장)
        if self.request_type == 'monthly':
            try:
                self.target_year = int(self.target_year)
                self.target_month = int(self.target_month)
            except (ValueError, TypeError):
                raise ValueError("연도와 월이 정상적으로 계산되지 않았습니다.")
        return self

class LeaveItemModel(BaseModel):
    leave_type: str = Field(
        default="연차",
        description="휴가 종류: '연차', '반차', '경조', '출산', '병가', '공가', '장기근속포상휴가', '대체휴가', '특별휴가', '휴직', '반반차', '하기휴가', '생일휴가', '기타휴가', '보상휴가(종일)', '보상휴가(반일)'"
    )

    start_date: Optional[Any] = Field(
        default=None,
        validation_alias=AliasChoices('start_date', 'date', 'leave_date'),
        description="휴가 시작일(YYYY-MM-DD)."
    )

    end_date: Optional[Any] = Field(
        default=None,
        description="휴가 종료일(YYYY-MM-DD)."
    )

    # 🚨 반차 / 보상휴가(반일) 전용 필드
    half_day_type: Optional[Literal["오전", "오후"]] = Field(
        default=None,
        description="반차나 보상휴가(반일)일 경우 '오전' 또는 '오후' 선택"
    )

    # 🚨 반반차 전용 필드 (HH:MM)
    start_time: Optional[str] = Field(
        default=None,
        description="반반차 시작 시간 (예: '08:30')."
    )
    end_time: Optional[str] = Field(
        default=None,
        description="반반차 종료 시간 (예: '10:30')."
    )

    memo: str = Field(default=".")

    @model_validator(mode='before')
    @classmethod
    def preprocess_item(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data

        now = datetime.now()

        # 1. 날짜 필드 통합 및 자연어 처리
        # 별칭(leave_date, date)으로 들어온 값을 start_date로 통일
        raw_date = data.get('start_date') or data.get('leave_date') or data.get('date')
        if raw_date:
            data['start_date'] = raw_date

        for date_field in ['start_date', 'end_date']:
            val = data.get(date_field)
            if isinstance(val, str):
                if '오늘' in val or '금일' in val:
                    data[date_field] = now.strftime("%Y-%m-%d")
                elif '내일' in val or '명일' in val:
                    data[date_field] = (now + timedelta(days=1)).strftime("%Y-%m-%d")
                elif '모레' in val:
                    data[date_field] = (now + timedelta(days=2)).strftime("%Y-%m-%d")

        # 필수 값 방어: start_date가 없으면 오늘로, end_date가 없으면 start_date와 동일하게
        if not data.get('start_date'):
            data['start_date'] = now.strftime("%Y-%m-%d")
        if not data.get('end_date'):
            data['end_date'] = data['start_date']
        if data.get('leave_type'):
            data['leave_type'] = data['leave_type'].replace(" ", "")
        # 2. 반차/보상휴가(반일) 디폴트 처리
        l_type = data.get('leave_type', '연차')
        if l_type in ['반차', '보상휴가(반일)']:
            if not data.get('half_day_type'):
                data['half_day_type'] = "오후"  # 기본값을 오후로 (필요시 오전으로 변경)

        # 3. 반반차 디폴트 및 자동 2시간 계산 로직
        if l_type == '반반차':
            s_time = data.get('start_time')
            if not s_time:
                # 사용자가 시간을 안 주면 디폴트 근무 시작시간(예: 08:30)으로 세팅
                s_time = "08:30"
                data['start_time'] = s_time

            # 종료 시간이 없으면 시작 시간 기준 2시간 뒤로 자동 세팅
            if not data.get('end_time'):
                try:
                    time_obj = datetime.strptime(s_time, "%H:%M")
                    data['end_time'] = (time_obj + timedelta(hours=2)).strftime("%H:%M")
                except ValueError:
                    data['end_time'] = "10:30"

        return data


from app.core.scheduler import is_weekend, is_holiday

# 전체 상신을 감싸는 루트 모델
class LeaveRequestModel(BaseModel):
    action_type: Literal["T", "F"] = Field(default="T")

    # 여러 개의 휴가를 리스트로 받음
    leave_data_list: List[LeaveItemModel] = Field(
        description="신청할 휴가 목록. '월요일부터 금요일까지 연차'처럼 범위를 요청하면 서버에서 자동으로 주말/공휴일을 제외하고 확장합니다."
    )

    @field_validator('action_type', mode='before')
    @classmethod
    def transform_action_type(cls, v):
        if isinstance(v, bool):
            return "T" if v else "F"
        return v

    @model_validator(mode='after')
    def expand_date_ranges(self) -> 'LeaveRequestModel':
        """
        1. 각 LeaveItemModel의 start_date와 end_date가 다를 경우(범위) 영업일로 확장합니다.
        2. 리스트 내의 모든 항목에 대해 주말과 공휴일을 최종 필터링합니다.
        """
        expanded_list = []
        for item in self.leave_data_list:
            try:
                start_dt = datetime.strptime(item.start_date, "%Y-%m-%d")
                end_dt = datetime.strptime(item.end_date, "%Y-%m-%d")

                # 범위(Range) 처리
                if start_dt != end_dt:
                    curr_dt = start_dt
                    while curr_dt <= end_dt:
                        if not is_weekend(curr_dt) and not is_holiday(curr_dt):
                            new_item = item.model_copy()
                            new_item.start_date = curr_dt.strftime("%Y-%m-%d")
                            new_item.end_date = new_item.start_date
                            expanded_list.append(new_item)
                        curr_dt += timedelta(days=1)
                else:
                    # 단일 날짜 처리 (여기서도 주말/공휴일 체크)
                    if not is_weekend(start_dt) and not is_holiday(start_dt):
                        expanded_list.append(item)
                    else:
                        logger.info(f"🚫 주말/공휴일 신청 건 제외됨: {item.start_date}")

            except Exception as e:
                logger.error(f"날짜 처리 중 에러: {e}")
                expanded_list.append(item)

        self.leave_data_list = expanded_list
        return self