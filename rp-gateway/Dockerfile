FROM python:3.12.8-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY rp-gateway/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY rp-gateway/app ./app
COPY rp-gateway/tests ./tests
COPY rp-gateway/pyproject.toml rp-gateway/README.md ./
COPY scripts /scripts
COPY state/schema.json /app/state-schema.json
COPY worldpacks /worldpacks
COPY configs/showroom /app/configs/showroom

EXPOSE 8088

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8088"]
