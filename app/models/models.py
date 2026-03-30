from pydantic import BaseModel, Field, AliasChoices, field_validator
from typing import List, Optional, Literal


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
    # LLM이 시스템 프롬프트에서 읽어서 채워 넣도록 지시
    target_user_id: str = Field(
        ...,
        description="시스템 프롬프트(System Prompt)에 명시된 '현재 로그인한 사용자 ID'를 반드시 그대로 입력하세요."
    )

    # default를 지우고 ... 을 넣어 필수값으로 만듭니다.
    target_user_name: str = Field(
        ...,
        validation_alias=AliasChoices('target_user_name', 'user', 'name'),
        description="OT 대상자 이름. 대화 컨텍스트에 제공된 '현재 로그인한 사용자 이름'을 입력하세요. 단, 사용자가 다른 사람을 명시적으로 지목한 경우 그 이름을 넣으세요."
    )

    dept_name: str = Field(
        ...,
        validation_alias=AliasChoices('dept_name', 'dept', 'department', '부서'),
        description="대상자의 소속 부서명 (예: DX 2Team). 대화 컨텍스트에 제공된 '현재 로그인한 사용자의 부서'를 입력하세요. 부서 정보가 없으면 임의로 지어내지 말고 사용자에게 물어보세요."
    )

    ot_date: str = Field(
        ...,
        validation_alias=AliasChoices('ot_date', 'work_date', 'date'),
        description="초과근무 일자 (반드시 YYYY-MM-DD 형식). '오늘'인 경우 시스템의 현재 날짜를 계산해서 입력하세요."
    )

    # Literal을 사용하여 T와 F 외의 이상한 문자열 생성을 원천 차단
    action_type: Literal["T", "F"] = Field(
        default="T",
        validation_alias=AliasChoices('action_type', 'is_임시_저장', 'save_mode'),
        description="처리 방식. '상신해줘', '올려줘', '결재해줘' 등의 요청이면 'F'(결재상신), 그 외 '저장' 또는 별도 명시가 없으면 'T'(임시저장)를 입력하세요."
    )

    memo: str = Field(
        default=".",
        validation_alias=AliasChoices('memo', 'reason', 'contents'),
        description="연장근로 사유. 사용자가 명시하지 않으면 기본값 '.' 을 사용하세요."
    )

    # LLM이 자꾸 넣으려고 하는 시간 필드 방어용
    start_time: Optional[str] = Field(default=None, description="OT 시작 시간 (내부적으로 자동 계산되므로 무시됨)")
    end_time: Optional[str] = Field(default=None, description="OT 종료 시간 (내부적으로 자동 계산되므로 무시됨)")

    doc_type: Literal["OT", "특근"] = Field(default="OT", description="결재 양식 종류. 평일은 'OT', 주말/휴일은 '특근'")

    @field_validator('action_type', mode='before')
    @classmethod
    def transform_action_type(cls, v):
        if isinstance(v, bool):
            return "T" if v else "F"
        return v