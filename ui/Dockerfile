# The Session sheet: stdlib-only, so the image is a bare interpreter plus
# the sheet it serves and the modules serve.py facades (guard, quotas,
# composer, picker, dive). COGNEE_URL / NEXT_TIER_URL are set in compose
# to the container-network service names; the sheet must bind 0.0.0.0 to
# be reachable through the published port.
FROM python:3.12-slim

WORKDIR /app
COPY serve.py guard.py quotas.py composer.py picker.py dive.py index.html ./

ENV SESSION_UI_HOST=0.0.0.0 \
    SESSION_UI_PORT=8765

EXPOSE 8765
CMD ["python", "serve.py"]
