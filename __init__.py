"""Backend package marker for the Jira Browser plugin."""


def register(ctx):
    """Keep the agent-plugin half inert; Desktop owns the user interface."""
    return None
