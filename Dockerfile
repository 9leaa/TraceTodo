FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md run_web.py ./
COPY src ./src

# Install package with FastAPI + Uvicorn + Qdrant optional extras
RUN pip install --no-cache-dir -e ".[full]"

EXPOSE 18090

ENV TRACE_MEMORY_WEB_HOST=0.0.0.0
ENV TRACE_MEMORY_WEB_PORT=18090
ENV TRACE_MEMORY_DATA_DIR=/app/data
ENV TRACE_MEMORY_KNOWLEDGE_DIR=/app/knowledge

CMD ["python3", "run_web.py"]
