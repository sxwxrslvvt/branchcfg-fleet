FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home --shell /usr/sbin/nologin appuser
COPY main.py ./
COPY templates ./templates

USER appuser
ENTRYPOINT ["python", "/app/main.py"]
CMD ["generate"]
