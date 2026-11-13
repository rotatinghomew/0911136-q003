FROM ccusr/prepared:3f007a95fcee-4441968e5ff6
WORKDIR /app
COPY --chown=node:node . /app
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT []
CMD ["python3", "-m", "response_chain.runner"]
