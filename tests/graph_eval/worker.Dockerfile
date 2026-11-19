# Resolve and record the resulting image ID before using this worker.
FROM node:24.22.1-bookworm@sha256:6449e19d8184d84062525b6a7524def03146c2cb04a7f08b076680d0e8e4240a
RUN npm install --global --ignore-scripts @openai/codex@0.170.0 \
    && codex --version
COPY source_reader.mjs network_proxy.mjs worker_launch.mjs worker_probe.mjs /opt/graph-eval/
ENV HOME=/runtime CODEX_HOME=/runtime/codex
WORKDIR /source
ENTRYPOINT ["node", "/opt/graph-eval/worker_launch.mjs"]
