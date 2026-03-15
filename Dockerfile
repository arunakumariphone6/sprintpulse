FROM python:3.11-slim

# Labels
LABEL maintainer="Arunakumar Tavva"
LABEL author="Arunakumar Tavva"
LABEL description="Jira Intelligence Dashboard — Real-time Jira Live Dashboard"
LABEL version="2.0"
LABEL org.opencontainers.image.title="Jira Intelligence Dashboard"
LABEL org.opencontainers.image.description="Real-time Jira API integration dashboard"
LABEL org.opencontainers.image.authors="Arunakumar Tavva"
LABEL org.opencontainers.image.version="2.0"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY logo.png .

RUN adduser --disabled-password --gecos '' appuser && \
    chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/status')" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "2", \
     "--timeout", "180", "--keep-alive", "5", \
     "--access-logfile", "-", "--error-logfile", "-", \
     "app:app"]
