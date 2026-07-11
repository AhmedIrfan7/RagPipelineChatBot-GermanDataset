# Runtime image for the Fahrschule chatbot API.
# Python 3.12 (stable wheels for the whole runtime stack).
#
# IMPORTANT: the confidential data tree (prices, knowledge, PDFs) is NOT copied into
# the image. It is mounted at /app/data at runtime, and secrets come from the
# environment (.env / platform secrets). The image contains only application code.
FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# install runtime deps first (better layer caching)
COPY requirements-api.txt ./
RUN pip install --no-cache-dir -r requirements-api.txt

# application code only
COPY src ./src

EXPOSE 8123

# healthcheck hits the liveness endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8123/api/health').status==200 else 1)" || exit 1

CMD ["python", "-m", "uvicorn", "fahrschule.api:app", "--app-dir", "src", \
     "--host", "0.0.0.0", "--port", "8123"]
