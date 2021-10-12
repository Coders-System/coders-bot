# Coder's System Bot

Custom bot with modmail features for the Coder's System Discord server. Based on [kyb3r/modmail](https://github.com/kyb3r/modmail/). The only reason we didn't keep it as a fork, is because it doesn't allow us to create issues and our own branches.

## Running it locally

Make sure you have Docker installed

1. Clone this repo and open your terminal in that directory.

2. Rename `.env.example` to `.env` and fill in your credentials

3. Rename `cogs.example.json` to `cogs.json`.

4. Now run `docker-compose up` to start the bot. If you want to stop it, just run `docker-compose down`. After updating the code, run `docker-compose up --build` to rebuild your container
