FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY referral_bot.py .

CMD ["python", "referral_bot.py"]
