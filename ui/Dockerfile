# The Session sheet: stdlib-only, so the image is a bare interpreter plus
# the sheet it serves and the modules serve.py facades (guard, quotas,
# composer, picker, dive, research, research_store, accounts, audit,
# console, report).
# COGNEE_URL / NEXT_TIER_URL are set in compose to the container-network
# service names; the sheet must bind 0.0.0.0 to be reachable through the
# published port.
FROM python:3.12-slim

WORKDIR /app
COPY serve.py guard.py quotas.py composer.py picker.py dive.py research.py research_store.py recall_more.py page_resolver.py accounts.py audit.py console.py report.py ledger.py index.html ./
# The reader's client-side PDF engine (pdf.js, Apache-2.0) — vendored so
# the sheet works on a LAN with no CDN (ADR-0007).
COPY vendor /app/vendor
# The visual pipeline: pages rasterize server-side through PDFium
# (pypdfium2 bundles the binary; Pillow encodes the PNGs) — pdf.js's
# canvas cannot faithfully draw these Persian subset fonts (ADR-0007).
RUN pip install --no-cache-dir pypdfium2==5.9.0 "pillow>=10,<12"

ENV SESSION_UI_HOST=0.0.0.0 \
    SESSION_UI_PORT=8765

EXPOSE 8765
CMD ["python", "serve.py"]
