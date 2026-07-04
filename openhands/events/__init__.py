from typing import TYPE_CHECKING, Any

from openhands.events.event import Event, EventSource, RecallType

if TYPE_CHECKING:
    from openhands.events.stream import EventStream, EventStreamSubscriber


def __getattr__(name: str) -> Any:
    if name in {'EventStream', 'EventStreamSubscriber'}:
        from openhands.events.stream import EventStream, EventStreamSubscriber

        value = {
            'EventStream': EventStream,
            'EventStreamSubscriber': EventStreamSubscriber,
        }[name]
        globals()[name] = value
        return value
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

__all__ = [
    'Event',
    'EventSource',
    'EventStream',
    'EventStreamSubscriber',
    'RecallType',
]
