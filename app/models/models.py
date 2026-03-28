from pydantic import BaseModel, Field, AliasChoices, field_validator
from typing import List, Optional

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
    # LLM이 'name', 'user' 등으로 불러도 인식
    target_user_name: str = Field(
        default="",
        validation_alias=AliasChoices('target_user_name', 'user', 'name'),
        description="OT 대상자 이름 (예: 문병찬). 미입력 시 본인"
    )

    # 'dept', 'department'를 'dept_name'로 매핑
    dept_name: str = Field(
        default="",
        validation_alias=AliasChoices('dept_name', 'dept', 'department', '부서'),
        description="부서, 부서명, 소속 (예:DX2팀)"
    )

    # 'work_date', 'date'를 'ot_date'로 매핑
    ot_date: str = Field(
        default="",
        validation_alias=AliasChoices('ot_date', 'work_date', 'date'),
        description="초과근무 일자 (YYYY-MM-DD)"
    )

    # 'reason'을 'memo'로 매핑
    memo: str = Field(
        default=".",
        validation_alias=AliasChoices('memo', 'reason', 'contents'),
        description="연장근로 사유"
    )

    # 'is_임시_저장' 불리언 값을 'T' 또는 'F'로 변환
    action_type: str = Field(
        default="T",
        validation_alias=AliasChoices('action_type', 'is_임시_저장', 'save_mode'),
        description="처리 방식. 'T'는 임시저장, 'F'는 결재상신"
    )

    # LLM이 자꾸 넣으려고 하는 시간 필드들을 에러 없이 받아내기 위해 추가 (내부 로직에서 참조 가능)
    start_time: Optional[str] = Field(default=None, description="OT 시작 시간 (HH:mm)")
    end_time: Optional[str] = Field(default=None, description="OT 종료 시간 (HH:mm)")

    doc_type: str = Field(default="OT", description="결재 양식 종류")
    cookies: List[dict] = Field(default_factory=list, exclude=True)

    @field_validator('action_type', mode='before')
    @classmethod
    def transform_action_type(cls, v):
        if isinstance(v, bool):
            return "T" if v else "F"
        return v