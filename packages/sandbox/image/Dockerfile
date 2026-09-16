# Image in which agent-written snippets run (docs/PLAN.md 7.12). Kept minimal and offline.
# /work is root-owned: the runner puts the snippet and the read-only inputs there and a
# writable out/ for the sandbox user; the user cannot create anything else in it.
FROM python:3.12-slim
RUN useradd --uid 1000 --create-home sandbox \
    && pip install --no-cache-dir lxml pdfplumber pypdfium2 jsonschema xmlschema saxonche \
    && mkdir -p /work && chown root:root /work && chmod 755 /work
USER 1000:1000
WORKDIR /work
CMD ["python", "/work/main.py"]
