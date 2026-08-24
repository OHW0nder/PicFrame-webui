FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends libimage-exiftool-perl fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-web.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

COPY . /app
RUN useradd --create-home --uid 1000 picframe \
 && mkdir -p /data/input /data/output \
 && chown -R picframe:picframe /app /data
USER picframe

EXPOSE 8090
CMD ["uvicorn", "webui.app:app", "--host", "0.0.0.0", "--port", "8090"]
