import asyncio
import threading
import time
import unittest
from rag_modules.cancellation import stop_signal, stream


class CancellationTests(unittest.TestCase):
    def test_stop_interrupts_awaiting_next_token_and_closes_provider(self):
        entered, closed, stop = threading.Event(), threading.Event(), threading.Event()
        class Provider:
            async def astream(self, query):
                try:
                    yield 'first'
                    entered.set()
                    await asyncio.sleep(60)
                    yield 'must not arrive'
                finally: closed.set()
        result=[]
        def consume():
            token=stop_signal.set(stop)
            try: result.extend(stream(Provider(),'question'))
            finally: stop_signal.reset(token)
        worker=threading.Thread(target=consume); worker.start()
        self.assertTrue(entered.wait(2))
        started=time.monotonic(); stop.set(); worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(closed.is_set())
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(result,['first'])
