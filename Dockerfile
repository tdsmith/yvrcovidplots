FROM python:3.10
RUN apt-get update && apt-get install -y fonts-liberation && rm -rf /var/lib/apt/lists/*
WORKDIR /usr/src/app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENTRYPOINT ["python", "bot.py"]
