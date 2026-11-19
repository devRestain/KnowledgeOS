"""C9: role feedback, bounded Experience outbox and HermesTrace dependency."""

from .. import interops, learning
from . import Registry

CAPABILITY = "C9"


def register(reg: Registry) -> None:
    reg.view("experience", learning.experience_view, learning.experience_item)
    reg.view("feedback", learning.feedback_view)
    reg.dependency("hermestrace", interops.status)
    reg.on_start(learning.start)
