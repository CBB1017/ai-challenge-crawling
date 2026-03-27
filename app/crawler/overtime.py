import math
from typing import Optional, List, Dict, Any, Tuple


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
