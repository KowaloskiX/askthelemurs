# API server for the demo. The frontend (web/) deploys separately and points NEXT_PUBLIC_API here.
# Secrets (OPENAI_API_KEY, TYPESAFE_API_KEY) come from the platform's environment, never from the image.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYDANTIC_DISABLE_PLUGINS=__all__
COPY requirements-app.txt .
RUN pip install --no-cache-dir -r requirements-app.txt
COPY app/ app/
COPY src/ src/
COPY scenarios/ scenarios/
COPY data/ data/
COPY out/ out/
COPY viz/venues.json viz/venues.json
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.server:app --host 0.0.0.0 --port ${PORT:-8000}"]
