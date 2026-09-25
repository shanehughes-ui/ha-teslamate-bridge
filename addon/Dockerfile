ARG BUILD_FROM
FROM $BUILD_FROM

# Three runtime dependencies, pinned loosely: pure-python (fastapi, httpx) plus
# uvicorn without its optional C extras so the Alpine build never needs a compiler.
RUN pip3 install --no-cache-dir "fastapi>=0.110,<1" "uvicorn>=0.27,<1" "httpx>=0.27,<1"

WORKDIR /app
COPY bridge /app/bridge
COPY run /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
