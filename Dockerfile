FROM python:3.9-slim

ENV MULTIDICT_NO_EXTENSIONS=1

WORKDIR /bot

COPY . .

RUN pip install -r requirements.txt

CMD python bot.py