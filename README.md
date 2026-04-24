# Hermes Agent - Ollama Cloud Custom Image

Custom Docker image for deploying Hermes Agent on Railway with Ollama Cloud provider and Gemma 4 31B model.

## Why this exists

The Hermes gateway ignores environment variables for the primary model configuration and only reads from `config.yaml`. This image bakes the correct config at build time, solving the fallback to `anthropic/claude-opus-4.6` issue.

## Configuration

The image pre-configures:
- **Provider**: `ollama-cloud`
- **Model**: `gemma4:31b-cloud`
- **Config path**: `/opt/data/.hermes/config.yaml`

## Environment Variables (set in Railway)

```env
HERMES_HOME=/opt/data/.hermes
API_SERVER_ENABLED=true
API_SERVER_KEY=your-secure-key
GATEWAY_ALLOW_ALL_USERS=false
HERMES_SOUL_OVERRIDE=Your custom soul prompt here
HERMES_STT_PROVIDER=local
HERMES_TTS_PROVIDER=disabled
OLLAMA_API_KEY=your-ollama-api-key
PORT=8642
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_ALLOWED_USERS=your-user-id
TELEGRAM_HOME_CHANNEL=your-channel-id
```

## Deploy to Railway

1. Connect this GitHub repo to Railway
2. Railway auto-detects the Dockerfile
3. Add the environment variables above
4. Deploy

## License

MIT
