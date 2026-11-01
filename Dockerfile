FROM python:3.12-slim
ENV TZ=Asia/Jerusalem PYTHONUNBUFFERED=1
WORKDIR /srv
COPY app ./app
COPY config.json .
VOLUME /srv/data
EXPOSE 8080
CMD ["python", "-m", "app.main", "serve"]
