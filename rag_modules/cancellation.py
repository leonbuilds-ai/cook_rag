"""Cancel asynchronous model I/O from the request's thread-safe stop signal."""
import asyncio
import inspect
from contextvars import ContextVar

stop_signal = ContextVar('recipe_stop_signal', default=None)


class GenerationCancelled(Exception):
    pass


async def until_stopped(signal):
    while not signal.is_set():
        await asyncio.sleep(.03)


async def cancellable(awaitable, signal):
    operation = asyncio.ensure_future(awaitable)
    watcher = asyncio.create_task(until_stopped(signal))
    try:
        done, _ = await asyncio.wait({operation, watcher}, return_when=asyncio.FIRST_COMPLETED)
        if watcher in done or signal.is_set():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise GenerationCancelled()
        return await operation
    finally:
        watcher.cancel()
        if not operation.done(): operation.cancel()
        await asyncio.gather(watcher, operation, return_exceptions=True)


def invoke(llm, prompt):
    signal = stop_signal.get()
    if signal is None: return llm.invoke(prompt)
    with asyncio.Runner() as runner:
        try: return runner.run(cancellable(llm.ainvoke(prompt), signal))
        finally: runner.run(close_model(llm))


async def close_model(model):
    if model is None: return
    asynchronous = getattr(model, 'root_async_client', None) or getattr(model, '_async_client', None)
    synchronous = getattr(model, 'root_client', None) or getattr(model, '_client', None)
    for client in (asynchronous, synchronous):
        if client is not None:
            result = client.close()
            if inspect.isawaitable(result): await result


def stream(chain, query, model=None):
    signal = stop_signal.get()
    if signal is None:
        yield from chain.stream(query)
        return
    with asyncio.Runner() as runner:
        iterator = chain.astream(query)
        try:
            while not signal.is_set():
                try: yield runner.run(cancellable(anext(iterator), signal))
                except (StopAsyncIteration, GenerationCancelled): return
        finally:
            try: runner.run(iterator.aclose())
            finally: runner.run(close_model(model))
