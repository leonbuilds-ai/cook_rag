"""Manual integration smoke against a running server; no credentials are printed."""
import argparse
import json
import time
import httpx


def events(response):
    name, payload = None, None
    for line in response.iter_lines():
        if line.startswith('event: '): name = line[7:]
        elif line.startswith('data: '): payload = json.loads(line[6:])
        elif not line and name:
            yield name, payload
            name, payload = None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    with httpx.Client(base_url=args.url, timeout=180) as client:
        chat = client.post('/chats').json()
        batches = []
        for _ in range(2):
            with client.stream('POST', '/ask/stream', json={'chat_id': chat['id'], 'question': '早餐吃什么', 'topic': 'breakfast'}) as response:
                response.raise_for_status()
                result = next(data for name, data in events(response) if name == 'done')
                batches.append([s['title'] for s in result['sources']])
        print('Recommendation batches:', batches)
        assert not set(batches[0]) & set(batches[1])
        started = time.perf_counter()
        chunks = 0
        first = None
        with client.stream('POST', '/ask/stream', json={'chat_id': chat['id'], 'question': '第一个怎么做'}) as response:
            response.raise_for_status()
            for name, data in events(response):
                if name == 'delta':
                    chunks += 1
                    if first is None:
                        first = time.perf_counter() - started
                        print('First delta after seconds:', round(first, 3), flush=True)
                if name == 'done':
                    print('Resolved:', data['resolved_query'])
                    print('Deltas:', chunks, 'completed seconds:', round(time.perf_counter()-started,3))
        assert chunks > 2
        with client.stream('POST', '/ask/stream', json={'chat_id': chat['id'], 'question': '详细讲一下制作步骤'}) as response:
            for name, data in events(response):
                if name == 'delta':
                    client.post('/chats/' + chat['id'] + '/stop').raise_for_status()
                    break
        for _ in range(200):
            stored = client.get('/chats/' + chat['id']).json()
            if not stored['generating']: break
            time.sleep(.2)
        print('Cancelled status:', stored['messages'][-1]['status'])
        assert stored['messages'][-1]['status'] == 'stopped'
        assert not stored['generating'], 'Backend cleanup has not finished'
        client.delete('/chats/' + chat['id']).raise_for_status()
        assert not client.get('/chats').json()['chats']
        client.post('/chats/' + chat['id'] + '/restore')
        assert client.get('/chats/' + chat['id']).status_code == 200
        client.delete('/chats/' + chat['id'])
        print('History delete/restore: passed')


if __name__ == '__main__': main()
