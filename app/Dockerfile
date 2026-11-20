FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY demo_app ./demo_app
COPY loadgen.py .
RUN useradd --system app
USER app
EXPOSE 8000
CMD ["uvicorn", "demo_app.main:app", "--host", "0.0.0.0", "--port", "8000"]
