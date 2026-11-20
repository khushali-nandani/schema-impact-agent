"""Checkpointing: saving the graph's state after every step so a paused review can resume.

A review is one *thread*. Resuming needs the same thread id, so use `review_config` with an id
that identifies the review (for example the pull request number).

The state holds this project's dataclasses (Impact, Verdict, Project, ...). LangGraph will block
loading unregistered types from a checkpoint in a future version, so the saver is given an
allow-list. It is built by scanning the modules, so a new dataclass is picked up automatically.
"""

import dataclasses
import inspect
from types import ModuleType

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

from schema_impact.agent import judge
from schema_impact.core import changes, impact, lineage, registry

_MODULES: tuple[ModuleType, ...] = (changes, impact, lineage, registry, judge)


def allowed_modules() -> list[tuple[str, str]]:
    """(module, class) for every dataclass defined in the modules that make up the state."""
    return [
        (module.__name__, name)
        for module in _MODULES
        for name, obj in vars(module).items()
        if inspect.isclass(obj)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == module.__name__
    ]


def make_checkpointer() -> InMemorySaver:
    """An in-memory saver that may load this project's dataclasses.

    In memory means a review survives a restart of the *graph object*, not of the process.
    """
    return InMemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=allowed_modules()))


def review_config(thread_id: str) -> dict:
    """The config that ties a run to one review. Use the same id to resume it."""
    return {"configurable": {"thread_id": thread_id}}
