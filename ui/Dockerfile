# The Session sheet: stdlib-only, so the image is a bare interpreter plus
# the two files it serves. COGNEE_URL / NEXT_TIER_URL are set in compose
# to the container-network service names; the sheet must bind 0.0.0.0 to
# be reachable through the published port.
FROM python:3.12-slim

WORKDIR /app
COPY serve.py index.html ./

ENV SESSION_UI_HOST=0.0.0.0 \
    SESSION_UI_PORT=8765

EXPOSE 8765
CMD ["python", "serve.py"]
