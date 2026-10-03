# 패키지 설치는 빌드(반입 전) 단계에서 끝낸다. 클러스터 안에서는 pip가 돌지 않는다.
FROM python:3.12-slim@sha256:dddfd7e07f9d15aeeca61529320492139d21cac7f0070c00609243e51e4e0016

WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY rag_app ./rag_app

USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "rag_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
