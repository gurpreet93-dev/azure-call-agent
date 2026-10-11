"""Create or update the booking agent in Foundry.  Run:  python -m scripts.setup_booking_agent

Uses FOUNDRY_PROJECT_ENDPOINT and FOUNDRY_AGENT_NAME from .env (e.g. clearview-booking-agent). On a first run the
agent is created with starter instructions; later runs refresh only its function tools.
"""
from app.agent_setup import setup_booking_agent

if __name__ == "__main__":
    result = setup_booking_agent()
    action = "Updated tools on" if result["existed"] else "Created"
    print(f"{action} agent '{result['agent']}' version {result['version']} ({result['model']}): "
          f"tools {', '.join(result['tools'])}")
