"""Resource modules — one per Convilyn product surface.

Each resource is a thin object attached to the client (``client.files``,
``client.convert``, ``client.goals``, ``client.workflows``). Resources
own the request shaping and response parsing for a coherent set of
endpoints; they never reach into another resource directly.

This per-resource layout follows the OpenAI / Stripe / Twilio
convention and keeps each surface mockable in isolation.
"""

from convilyn.resources.account import Account, AsyncAccount
from convilyn.resources.builder import AsyncBuilder, Builder
from convilyn.resources.convert import AsyncConvert, Convert
from convilyn.resources.files import AsyncFiles, Files
from convilyn.resources.goals import AsyncGoals, Goals
from convilyn.resources.user_workflows import AsyncUserWorkflows, UserWorkflows
from convilyn.resources.workflows import AsyncWorkflows, Workflows

__all__ = [
    "Account",
    "AsyncAccount",
    "AsyncBuilder",
    "AsyncConvert",
    "AsyncFiles",
    "AsyncGoals",
    "AsyncUserWorkflows",
    "AsyncWorkflows",
    "Builder",
    "Convert",
    "Files",
    "Goals",
    "UserWorkflows",
    "Workflows",
]
