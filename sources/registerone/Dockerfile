FROM python:3.12-slim

WORKDIR /srv

COPY pyproject.toml ./
COPY registerone ./registerone

RUN pip install --no-cache-dir .

EXPOSE 8100
CMD ["uvicorn", "registerone.api:app", "--host", "0.0.0.0", "--port", "8100"]
