"""The UnderCover Copilot on AgentCore Runtime: the file Runtime starts.

The Copilot is the AgentCore app in agent/copilot_entrypoint.py, which npm run dev
serves on the laptop. The deployment packages this folder with the agent package beside
it, as it does for runtime/main.py, so this file can import the app and run it.
"""

from agent.copilot_entrypoint import app

if __name__ == "__main__":
    app.run()
