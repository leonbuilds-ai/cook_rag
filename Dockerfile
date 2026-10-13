FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 API_HOST=0.0.0.0
WORKDIR /app
COPY requirements*.txt ./
ARG INSTALL_RERANK=false
ARG INSTALL_EVAL=false
RUN pip install --no-cache-dir -r requirements.txt && \
    if [ "$INSTALL_RERANK" = "true" ]; then pip install --no-cache-dir -r requirements-rerank.txt; fi && \
    if [ "$INSTALL_EVAL" = "true" ]; then pip install --no-cache-dir -r requirements-eval.txt; fi
COPY . .
RUN useradd --create-home cookrag && mkdir -p /app/logs /app/vector_index /app/runtime && chown -R cookrag:cookrag /app
USER cookrag
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=15s --start-period=180s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=12)" || exit 1
CMD ["python", "serve.py"]
