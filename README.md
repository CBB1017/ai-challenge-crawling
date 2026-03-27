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