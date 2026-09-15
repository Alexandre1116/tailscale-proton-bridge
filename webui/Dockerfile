# Imagem leve para a Web UI de configuração do bridge
FROM python:3.12.14-alpine3.23

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY templates ./templates
COPY static ./static
COPY entrypoint.sh .
COPY healthcheck.sh .
RUN chmod 0755 entrypoint.sh healthcheck.sh

# The UI only writes the explicitly mounted configuration directories.
# The runtime user is still configurable by the image/runtime environment.
RUN addgroup -S webui && adduser -S -G webui webui \
    && mkdir -p /data \
    && chown -R webui:webui /app /data
USER webui

EXPOSE 8080

CMD ["./entrypoint.sh"]
