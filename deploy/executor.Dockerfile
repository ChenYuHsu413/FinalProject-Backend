# 數位孿生調參驗證 executor（side-car）
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/executor
COPY executor/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY executor/ .
# 與 api/worker 相同 uid，才能寫 /srv/data/engine
RUN useradd --create-home --uid 10001 appuser
USER appuser
CMD ["python", "run_executor.py"]
