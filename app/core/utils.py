from loguru import logger

def format_minutes_to_readable(minutes):
    """분을 시간:분 형태로 변환"""
    hours = minutes // 60
    mins = minutes % 60
    if hours > 0 and mins > 0:
        return f"{hours}시간 {mins}분"
    elif hours > 0:
        return f"{hours}시간"
    else:
        return f"{mins}분"


def print_results(overtime_results, total_overtime, minus_consumed, all_entries):
    """개선된 결과 출력 함수"""
    logger.info("\n" + "=" * 100)
    logger.info("                            📊 초과 근무 분석 결과")
    logger.info("=" * 100)

    # 총 초과 근무 시간
    logger.info(f"\n🕐 총 초과 근무 시간: {format_minutes_to_readable(total_overtime)}")

    # 추가 근로 현황
    logger.info("\n" + "=" * 80)
    logger.info("📈 추가 근로 현황 (부족분을 제외하고 초과 근무가 발생한 날)")
    logger.info("=" * 80)

    if overtime_results:
        for result in overtime_results:
            work_type_emoji = "🏢" if result['work_type'] == "근무일" else "📅"

            # 차감 정보 표시
            consumed_info = ""
            if result['consumed_by']:
                consumed_details = []
                for consumed in result['consumed_by']:
                    consumed_details.append(f"{consumed['day']}일 {format_minutes_to_readable(consumed['used'])}")
                consumed_info = f" [차감됨: {', '.join(consumed_details)}]"

            logger.info(f"{work_type_emoji} {result['day']:2d}일 ({result['work_type']})")
            logger.info(f"   ⏰ 실제 근무: {result['original_start']} ~ {result['original_end']}")
            logger.info(f"   ⚡ 조정 시간: {result['adjusted_start']} ~ {result['adjusted_end']}")
            logger.info(f"   💼 인정 근무: {result['work_hours']}시간")
            logger.info(f"   ⏱️  초과 근무: {result['overtime_hours']}시간{consumed_info}")
            logger.info()

        logger.info(f"📊 초과 근무 발생 일수: {len(overtime_results)}일")
    else:
        logger.info("✅ 추가 근로가 발생한 날이 없습니다.")

    # 차감 현황
    if minus_consumed:
        logger.info("\n" + "=" * 80)
        logger.info("📉 초과 근무 차감 현황 (부족한 근무 시간으로 차감된 날)")
        logger.info("=" * 80)

        for consumed in minus_consumed:
            logger.info(f"🔻 {consumed['day']:2d}일: {format_minutes_to_readable(consumed['amount'])} 차감")

    # 일별 상세 현황 (모든 근무일)
    logger.info("\n" + "=" * 80)
    logger.info("📋 일별 상세 근무 현황")
    logger.info("=" * 80)

    work_days = [entry for entry in all_entries if entry['net_work_minutes'] > 0]
    work_days.sort(key=lambda x: x['day'])

    for entry in work_days:
        # 이모지 선택
        if entry['ot_minutes'] > 0:
            emoji = "⬆️"  # 초과 근무
            status = f"초과: {format_minutes_to_readable(entry['ot_minutes'])}"
        elif entry['minus_ot'] < 0:
            emoji = "⬇️"  # 부족 근무
            status = f"부족: {format_minutes_to_readable(-entry['minus_ot'])}"
        else:
            emoji = "✅"  # 정상 근무
            status = "정상 근무"

        logger.info(
            f"{emoji} {entry['day']:2d}일 ({entry['work_type']}): {entry['original_start']} ~ {entry['original_end']} -> {status}")

    logger.info("\n" + "=" * 100)
    logger.info("📝 계산 기준:")
    logger.info("   - 출근시간: 30분 단위 올림 (예: 9:01 -> 9:30)")
    logger.info("   - 퇴근시간: 30분 단위 내림 (예: 19:45 -> 19:30)")
    logger.info("   - 휴게시간: 4시간 초과시 점심 1시간, 9시간 초과시 저녁 1시간 추가")
    logger.info("   - 평일 초과근무: 8시간 초과분만 인정")
    logger.info("   - 휴무일 초과근무: 전체 근무시간 인정")
    logger.info("   - 부족분은 기존 초과근무에서 자동 차감")
    logger.info("=" * 100)
