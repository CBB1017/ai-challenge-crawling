import math
import re
from typing import Optional, List, Dict, Any, Tuple

from loguru import logger


# =====================================================================
# [어댑터 1] 결재 상신용 포맷터
# =====================================================================
def get_list_for_submission(analyzed_data: Dict[str, Any], memo: str) -> List[Dict[str, str]]:
    """
    상계 처리가 끝난 후 '실제 상신해야 할' 데이터만 웹 폼 입력용 리스트로 변환합니다.
    """
    ot_data_list = []
    for ot in analyzed_data['ot_pool']:
        if ot['remain'] > 0:  # 상계되고 남은 시간이 있는 경우만
            item = ot['info']
            ot_start = item["adjusted_start"] if item.get("work_type") == "휴무일" else "19:00"

            ot_data_list.append({
                "date": item["day"].replace(".", "-"),
                "start": ot_start,
                "end": item["adjusted_end"],
                "reason": memo
            })
    return ot_data_list


# =====================================================================
# [어댑터 2] 조회/보고서용 포맷터
# =====================================================================
def get_summary_for_report(analyzed_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    사용자에게 근태 현황을 보여주기 위한 요약 데이터를 생성합니다.
    """
    ot_pool = analyzed_data['ot_pool']
    ot_entries = analyzed_data['ot_entries']

    results = []
    total_overtime_minutes = 0

    for ot in ot_pool:
        if ot['remain'] > 0:
            info = ot['info']
            results.append({
                'day': ot['day'],
                'work_type': info['work_type'],
                'overtime_hours': round(ot['remain'] / 60, 2),
                'overtime_minutes': ot['remain'],
                'consumed_by': ot['consumed_by'],
            })
            total_overtime_minutes += ot['remain']

    # 필요에 따라 daily_details 등 추가 구성
    return {
        "results": results,
        "total_overtime_minutes": total_overtime_minutes,
        "minus_consumed": analyzed_data['minus_consumed'],
        "total_overtime_hours": round(total_overtime_minutes / 60, 2)
    }


class OvertimeCalculator:
    def __init__(self):
        self.standard_work_minutes = 480  # 8시간 = 480분

    @staticmethod
    def time_to_minutes(time_str: Optional[str]) -> Optional[int]:
        """시간 문자열을 분으로 변환"""
        if not time_str or time_str == '-' or time_str == '00:00':
            return None
        try:
            hours, minutes = map(int, time_str.split(':'))
            return hours * 60 + minutes
        except ValueError:
            return None

    @staticmethod
    def minutes_to_time(minutes: Optional[int]) -> Optional[str]:
        """분을 시간 문자열로 변환"""
        if minutes is None:
            return None
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours:02d}:{mins:02d}"

    @staticmethod
    def round_up_to_30(minutes: Optional[int]) -> Optional[int]:
        """30분 단위로 올림 (출근시간용)"""
        if minutes is None:
            return None
        return math.ceil(minutes / 30) * 30

    @staticmethod
    def round_down_to_30(minutes: Optional[int]) -> Optional[int]:
        """30분 단위로 내림 (퇴근시간, OT용)"""
        if minutes is None:
            return None
        return (minutes // 30) * 30

    @staticmethod
    def calculate_work_and_breaks(start_min: Optional[int], end_min: Optional[int]) -> Tuple[int, int, int]:
        """실제 근무시간과 휴게시간 계산"""
        if start_min is None or end_min is None or end_min <= start_min:
            return 0, 0, 0

        total_minutes = end_min - start_min
        lunch_break = 0
        dinner_break = 0

        if total_minutes > 240:
            lunch_break = 60

        if total_minutes > 540:
            overtime_after_9hours = total_minutes - 540
            if overtime_after_9hours >= 60:
                dinner_break = 60
            elif overtime_after_9hours >= 30:
                dinner_break = 30

        net_work_minutes = total_minutes - lunch_break - dinner_break
        return max(0, net_work_minutes), lunch_break, dinner_break

    def analyze_attendance(self, attendance_data: List[Dict[str, Any]], exclude_approved: bool = True) -> Dict[
        str, Any]:
        """
        [공통 코어 로직]
        근태 데이터를 순회하며 시간 계산 및 Minus OT 상계 처리를 수행합니다.
        exclude_approved: True면 이미 결재된 내역은 상계/상신 풀에서 제외합니다.
        """
        ot_entries = []
        logger.info(f"=== 근태 분석 시작 (총 {len(attendance_data)}건) ===")

        for record in attendance_data:
            # 🚨 보내주신 실제 데이터 구조에 맞게 키(Key) 완벽 매핑
            raw_day = record.get('일자', 'Unknown')
            plan_time = record.get('출근~퇴근', '09:00~18:00')  # 근무계획 - 출퇴근시간
            actual_time = record.get('출근~퇴근_1', '~')  # 근무실적 - 출퇴근시간 (중복키)
            work_type = record.get('근무구분', '근무일')  # 근무계획 - 근무구분
            result_type = record.get('근무구분_1', work_type)  # 근무실적 - 근무구분 (중복키, 예: '보상종일')
            ot_approval = record.get('전자결재', '')  # OT 전자결재 상태
            vacation = record.get('휴가', '')  # 휴가 상태 (예: '승인')

            # '4\xa0일(水)' 등에서 숫자('4')만 추출
            day_match = re.search(r'\d+', raw_day)
            day = day_match.group() if day_match else '0'

            logger.debug(f"[{day}일] ----------------------------------------")
            logger.debug(
                f"[{day}일] 원본: 실적={actual_time}, 계획={plan_time}, 계획구분={work_type}, 실적구분={result_type}, 휴가='{vacation}', 결재='{ot_approval}'")

            # 1. 기결재 내역 스킵 분기 (OT 결재가 이미 된 경우)
            if exclude_approved and any(status in ot_approval for status in ['승인', '상신', '결재중']):
                logger.info(f"[{day}일] ⏭️ 스킵됨: 이미 결재가 진행/완료된 내역 ({ot_approval})")
                continue

            # 휴가(연차/보상종일 등)가 '승인'된 날짜의 부족 근무(Minus OT)를 잡지 않게 방어
            if vacation == '승인':
                logger.info(f"[{day}일] ⏭️ 스킵됨: 휴가 승인일")
                continue

            try:
                work_start, work_end = actual_time.split('~')
                work_start, work_end = work_start.strip(), work_end.strip()
            except ValueError:
                logger.debug(f"[{day}일] ❌ 시간 파싱 실패 (actual_time: {actual_time})")
                continue

            original_start_min = self.time_to_minutes(work_start)
            original_end_min = self.time_to_minutes(work_end)

            if original_start_min is None or original_end_min is None:
                logger.debug(f"[{day}일] ❌ 유효하지 않은 출퇴근 시간 (start:{work_start}, end:{work_end})")
                continue

            # 30분 단위 절사 적용
            adjusted_start_min = self.round_up_to_30(original_start_min)
            adjusted_end_min = self.round_down_to_30(original_end_min)

            logger.debug(
                f"[{day}일] 시간 정규화: 출근({work_start} -> {self.minutes_to_time(adjusted_start_min)}), 퇴근({work_end} -> {self.minutes_to_time(adjusted_end_min)})")

            if adjusted_end_min <= adjusted_start_min:
                logger.debug(f"[{day}일] ❌ 퇴근시간이 출근시간보다 같거나 빠름. 계산 스킵.")
                continue

            ot_minutes, minus_ot, lunch_break, dinner_break, net_work_minutes = 0, 0, 0, 0, 0

            # 2. 근무 유형별 OT 계산 (계획된 work_type 기준)
            if work_type == '휴무일':
                net_work_minutes, lunch_break, dinner_break = self.calculate_work_and_breaks(
                    adjusted_start_min, adjusted_end_min
                )
                ot_minutes = self.round_down_to_30(net_work_minutes)
                logger.debug(f"[{day}일] 🏖️ 휴무일 근무: 순근무={net_work_minutes}분, 산정OT={ot_minutes}분")
            else:
                # 반차, 연차, 반반차 등 휴가 사용 여부 식별
                is_vacation_day = (vacation == '승인' or any(kw in result_type for kw in ['반차', '연차', '종일', '경조', '휴가']))
                try:
                    _, p_end_str = plan_time.split('~')
                    plan_end_min = self.time_to_minutes(p_end_str.strip())
                except ValueError:
                    plan_end_min = self.time_to_minutes('18:00')

                # 저녁 식사 1시간 강제 공제 반영 위치
                ot_start_baseline = plan_end_min + 60

                logger.debug(
                    f"[{day}일] 🏢 평일 근무 기준: 계획퇴근={self.minutes_to_time(plan_end_min)}, OT인정시작={self.minutes_to_time(ot_start_baseline)} (저녁휴게 60분 포함)")

                # 1. 실제 근무 시간 기반의 휴게 시간 및 순 근무 시간 계산
                # 30분 단위로 8시간 근무를 했는지 판단하기 위해 calculate_work_and_breaks 사용
                duration_net_work, lunch_break, duration_dinner_break = self.calculate_work_and_breaks(
                    adjusted_start_min, adjusted_end_min
                )

                if adjusted_end_min > ot_start_baseline:
                    ot_minutes = adjusted_end_min - ot_start_baseline
                    # OT 발생 시 저녁 휴게 1시간 강제 공제 (기존 로직 유지)
                    dinner_break = 60
                    logger.info(
                        f"[{day}일] ✅ OT 발생! 퇴근({self.minutes_to_time(adjusted_end_min)}) - OT시작({self.minutes_to_time(ot_start_baseline)}) = {ot_minutes}분")
                else:
                    ot_minutes = 0
                    dinner_break = duration_dinner_break
                    logger.debug(
                        f"[{day}일] ❌ OT 미발생 (실제퇴근 {self.minutes_to_time(adjusted_end_min)} <= OT인정시작 {self.minutes_to_time(ot_start_baseline)})")

                # 최종 순 근무 시간 재계산 (OT 공제 반영)
                net_work_minutes = max(0, (adjusted_end_min - adjusted_start_min) - lunch_break - dinner_break)

                # 3. 부족 근무(Minus OT) 판단
                # 사용자의 요청: 계획 근무 시간이 아니더라도 30분 단위로 8시간 근무를 했다면 부족 근무 아님.
                # 이를 위해 OT 공제 전의 duration_net_work를 기준으로 판단함.
                if duration_net_work < self.standard_work_minutes:
                    if is_vacation_day:
                        logger.info(
                            f"[{day}일] 🌴 휴가/반차 사용일: 부족 근무(Minus OT) 페널티를 면제합니다. (구분: {result_type})")
                    else:
                        overtime_diff = duration_net_work - self.standard_work_minutes
                        minus_ot = -math.ceil(-overtime_diff / 30) * 30
                        logger.info(
                            f"[{day}일] ⚠️ 부족 근무 발생: 기준({self.minutes_to_time(self.standard_work_minutes)}) > 실제({self.minutes_to_time(duration_net_work)}) -> 페널티 {minus_ot}분")

            ot_entries.append({
                'day': day,
                'work_type': work_type,
                'result_type': result_type,
                'original_start': work_start,
                'original_end': work_end,
                'adjusted_start': self.minutes_to_time(adjusted_start_min),
                'adjusted_end': self.minutes_to_time(adjusted_end_min),
                'net_work_minutes': net_work_minutes,
                'lunch_break': lunch_break,
                'dinner_break': dinner_break,
                'ot_minutes': ot_minutes,
                'minus_ot': minus_ot,
                'vacation': vacation,
                'ot_approval': ot_approval
            })

            # 3. Minus OT 상계 처리
        logger.info("=== 부족 근무(Minus OT) 상계 처리 시작 ===")
        pos_ot = [entry for entry in ot_entries if entry['ot_minutes'] > 0]
        neg_ot = [entry for entry in ot_entries if entry['minus_ot'] < 0]

        minus_consumed = []
        ot_pool = [{'day': entry['day'], 'remain': entry['ot_minutes'], 'info': entry, 'consumed_by': []} for entry in
                   pos_ot]

        for entry in neg_ot:
            need_absorb = -entry['minus_ot']
            logger.debug(f"⚠️ [{entry['day']}일] 부족근무 {need_absorb}분 상계 진행")

            i = 0
            while need_absorb > 0 and i < len(ot_pool):
                if ot_pool[i]['remain'] > 0:
                    use = min(ot_pool[i]['remain'], need_absorb)
                    ot_pool[i]['remain'] -= use
                    need_absorb -= use
                    ot_pool[i]['consumed_by'].append({'day': entry['day'], 'used': use})
                    logger.info(f"  -> 🔄 [{ot_pool[i]['day']}일]의 OT에서 {use}분 차감 (남은 OT: {ot_pool[i]['remain']}분)")
                i += 1
            minus_consumed.append({'day': entry['day'], 'amount': -entry['minus_ot']})

        logger.info("=== 근태 분석 완료 ===")

        return {
            "ot_entries": ot_entries,
            "ot_pool": ot_pool,
            "minus_consumed": minus_consumed
        }

    def calculate_overtime(self, attendance_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """초과근무 계산 메인 로직"""
        ot_entries = []

        for record in attendance_data:
            work_start = record.get('work_start')
            work_end = record.get('work_end')
            work_type = record.get('work_type', '')
            result_type = record.get('raw', [])[5] if len(record.get('raw', [])) > 5 else ''
            day = record.get('day')
            vacation = record.get('vacation', '')

            if not work_start or not work_end:
                continue

            original_start_min = self.time_to_minutes(work_start)
            original_end_min = self.time_to_minutes(work_end)

            if original_start_min is None or original_end_min is None:
                continue

            adjusted_start_min = self.round_up_to_30(original_start_min)
            adjusted_end_min = self.round_down_to_30(original_end_min)

            if adjusted_end_min <= adjusted_start_min:
                continue

            net_work_minutes, lunch_break, dinner_break = self.calculate_work_and_breaks(
                adjusted_start_min, adjusted_end_min
            )

            ot_minutes = 0
            minus_ot = 0

            if work_type == '휴무일':
                ot_minutes = self.round_down_to_30(net_work_minutes)
            else:
                overtime = net_work_minutes - 480
                if overtime > 0:
                    ot_minutes = self.round_down_to_30(overtime)
                elif overtime < 0 and work_type == '근무일' and result_type == '근무일':
                    minus_ot = -math.ceil(-overtime / 30) * 30

            ot_entries.append({
                'day': day,
                'work_type': work_type,
                'result_type': result_type,
                'original_start': work_start,
                'original_end': work_end,
                'adjusted_start': self.minutes_to_time(adjusted_start_min),
                'adjusted_end': self.minutes_to_time(adjusted_end_min),
                'net_work_minutes': net_work_minutes,
                'lunch_break': lunch_break,
                'dinner_break': dinner_break,
                'ot_minutes': ot_minutes,
                'minus_ot': minus_ot,
                'vacation': vacation,
            })

        pos_ot = [entry for entry in ot_entries if entry['ot_minutes'] > 0]
        neg_ot = [entry for entry in ot_entries if entry['minus_ot'] < 0]

        minus_consumed = []
        ot_pool = []

        for entry in pos_ot:
            ot_pool.append({
                'day': entry['day'],
                'remain': entry['ot_minutes'],
                'info': entry,
                'consumed_by': []
            })

        for entry in neg_ot:
            need_absorb = -entry['minus_ot']
            i = 0
            while need_absorb > 0 and i < len(ot_pool):
                if ot_pool[i]['remain'] > 0:
                    use = min(ot_pool[i]['remain'], need_absorb)
                    ot_pool[i]['remain'] -= use
                    need_absorb -= use
                    ot_pool[i]['consumed_by'].append({'day': entry['day'], 'used': use})
                i += 1
            minus_consumed.append({'day': entry['day'], 'amount': -entry['minus_ot']})

        deducted_details = []
        for ot in ot_pool:
            if ot['consumed_by']:
                deducted_details.append({
                    'ot_day': ot['day'],
                    'original_ot_minutes': ot['info']['ot_minutes'],
                    'remaining_ot_minutes': ot['remain'],
                    'consumed_by': ot['consumed_by'],
                    'total_consumed': sum(item['used'] for item in ot['consumed_by'])
                })

        daily_details = []
        for entry in ot_entries:
            status = "정상근무"
            if entry['ot_minutes'] > 0:
                if entry['work_type'] == '휴무일':
                    status = f"휴무일 근무 (OT: {entry['ot_minutes']}분)"
                else:
                    status = f"초과근무 (OT: {entry['ot_minutes']}분)"
            elif entry['minus_ot'] < 0:
                status = f"부족근무 ({entry['minus_ot']}분)"

            daily_details.append({
                'day': entry['day'],
                'work_type': entry['work_type'],
                'original_time': f"{entry['original_start']} ~ {entry['original_end']}",
                'adjusted_time': f"{entry['adjusted_start']} ~ {entry['adjusted_end']}",
                'work_hours': round(entry['net_work_minutes'] / 60, 2),
                'lunch_break': entry['lunch_break'],
                'dinner_break': entry['dinner_break'],
                'status': status,
                'vacation': entry['vacation']
            })

        results = []
        total_overtime_minutes = 0

        for ot in ot_pool:
            if ot['remain'] > 0:
                info = ot['info']
                results.append({
                    'day': ot['day'],
                    'work_type': info['work_type'],
                    'original_start': info['original_start'],
                    'original_end': info['original_end'],
                    'adjusted_start': info['adjusted_start'],
                    'adjusted_end': info['adjusted_end'],
                    'work_hours': round(info['net_work_minutes'] / 60, 2),
                    'overtime_hours': round(ot['remain'] / 60, 2),
                    'overtime_minutes': ot['remain'],
                    'lunch_break': info['lunch_break'],
                    'dinner_break': info['dinner_break'],
                    'vacation': info['vacation'],
                    'consumed_by': ot['consumed_by'],
                })
                total_overtime_minutes += ot['remain']

        # Spring AI 연동을 위해 단일 Dict로 묶어서 반환
        return {
            "results": results,
            "total_overtime_minutes": total_overtime_minutes,
            "minus_consumed": minus_consumed,
            "ot_entries": ot_entries,
            "deducted_details": deducted_details,
            "daily_details": daily_details
        }
