from datetime import datetime, timedelta
from typing import Optional, Literal, Any

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