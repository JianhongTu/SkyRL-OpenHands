from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openhands.llm.async_llm import AsyncLLM
    from openhands.llm.llm import LLM
    from openhands.llm.streaming_llm import StreamingLLM


def __getattr__(name: str):
    if name == 'LLM':
        from openhands.llm.llm import LLM

        return LLM
    if name == 'AsyncLLM':
        from openhands.llm.async_llm import AsyncLLM

        return AsyncLLM
    if name == 'StreamingLLM':
        from openhands.llm.streaming_llm import StreamingLLM

        return StreamingLLM
    raise AttributeError(f'module {__name__!r} has no attribute {name!r}')

__all__ = ['LLM', 'AsyncLLM', 'StreamingLLM']
