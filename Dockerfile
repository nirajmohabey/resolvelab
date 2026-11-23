FROM python:3.12.14-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/app/src
COPY requirements.lock /app/
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . /app
RUN useradd --uid 10001 --create-home app && chown -R app:app /app
USER app
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "resolvelab.api:app", "--host", "0.0.0.0", "--port", "8000"]
