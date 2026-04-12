python -m ensurepip --upgrade

python -m pip install uv

-- 실행 예시
uv run uvicorn app.main_api:app --host 0.0.0.0 --port 8080 
uvicorn main_api:app --host 0.0.0.0 --port 8080 
### --reload 옵션 사용 시 컨테이너로 실행된 CDP headless 호출 불가

-- 요청 예시
curl -X POST http://localhost:8080/run-crawl \
  -H "Content-Type: application/json" \
  -d '{"id": "your_id", "password": "your_pw", "year":2025, "month":4}'
### registry
docker run -d \
  -p 5000:5000 \
  --name my-registry \
  -v registry-data:/var/lib/registry \
  registry:2

### tag
docker tag my-app:latest localhost:5000/my-app:latest
### push
docker push localhost:5000/my-app:latest

### API doc 
http://localhost:8080/redoc
http://localhost:8080/docs


# # 🚨 [디버깅 1] 도착한 곳이 진짜 근태 페이지인지, 로그인 페이지인지 URL 확인
# logger.info(f"[DEBUG] goto 직후 현재 URL: {self.page.url}")
# 
# # 🚨 [디버깅 2] 현재 화면 상태를 사진으로 찍어서 저장 (눈으로 직접 확인!)
# await self.page.screenshot(path="debug_attendance_fail.png", full_page=True)
