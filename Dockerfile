FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py .
COPY datetime_parser.py .
COPY recurrence.py .
COPY helpers.py .

ENV MCP_PORT=13081
ENV MCP_HOST=0.0.0.0

EXPOSE ${MCP_PORT}

CMD ["python", "server.py"]
