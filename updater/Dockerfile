FROM docker:27-cli

RUN apk add --no-cache git docker-cli-compose python3

WORKDIR /app
COPY update_service.py .
COPY entrypoint.sh .
RUN chmod 0755 entrypoint.sh

CMD ["./entrypoint.sh"]
