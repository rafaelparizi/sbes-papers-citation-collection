FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY semantic_scholar_citations.py gerar_dashboard.py servidor.py ./

EXPOSE 8000
CMD ["python", "servidor.py"]
