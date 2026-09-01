FROM python:3.12-slim

RUN useradd -m -u 1000 user
USER user
ENV PATH="/home/user/.local/bin:$PATH" \
    PORT=7860 \
    PYTHONUNBUFFERED=1
WORKDIR /app

COPY --chown=user:user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

COPY --chown=user:user . .

# pre-build the evidence graph so first boot is fast and needs no writable FS
RUN cd Accenture/Accenture && python scripts/build_graph.py || true

EXPOSE 7860
CMD ["python", "api_server.py"]
