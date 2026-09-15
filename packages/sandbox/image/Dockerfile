# Image in which agent-written snippets run (milestone 3). Kept minimal and offline.
FROM python:3.12-slim
RUN useradd --uid 1000 --create-home sandbox \
    && pip install --no-cache-dir lxml pdfplumber pypdfium2 jsonschema xmlschema saxonche
USER 1000:1000
WORKDIR /work
CMD ["python", "/work/main.py"]
