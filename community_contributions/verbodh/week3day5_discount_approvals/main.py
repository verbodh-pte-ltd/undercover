"""The file AgentCore Runtime starts. On the laptop: uv run python main.py serves it on :8080."""

from discounts.app import app

if __name__ == "__main__":
    app.run()
