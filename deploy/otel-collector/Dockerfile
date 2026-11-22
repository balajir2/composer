FROM otel/opentelemetry-collector-contrib:0.140.0

COPY --chown=10001:10001 config.yaml /etc/otelcol-contrib/config.yaml

ENTRYPOINT ["/otelcol-contrib"]
CMD ["--config=/etc/otelcol-contrib/config.yaml"]
