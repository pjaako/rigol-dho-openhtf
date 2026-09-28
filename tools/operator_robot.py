"""Plays the operator against a running OpenHTF station: reads the state feed the web GUI reads and answers prompts."""
import asyncio, json, sys, time, urllib.request
from tornado.websocket import websocket_connect
PORT = 4444; answers = sys.argv[1:]          # one answer per prompt, in order
PLUG = 'openhtf.plugs.user_input.UserInput'
async def main():
    ws = await websocket_connect(f'ws://127.0.0.1:{PORT}/sub/station/websocket')
    seen, t0, last = set(), time.time(), None
    while time.time() - t0 < 60:
        try: msg = await asyncio.wait_for(ws.read_message(), 5)
        except asyncio.TimeoutError: continue
        if msg is None: break
        d = json.loads(msg); st = d.get('state') or {}
        status, rec = st.get('status'), st.get('test_record') or {}
        prompt = (st.get('plugs', {}).get('plug_states', {}) or {}).get(PLUG)
        if prompt and prompt['id'] not in seen:
            seen.add(prompt['id']); ans = answers.pop(0) if answers else ''
            print(f"PROMPT: {prompt['message'][:150]!r} | text input {prompt['text-input']} | image {'yes, %d chars' % len(prompt['image-url']) if prompt.get('image-url') else 'no'} -> answering {ans!r}")
            req = urllib.request.Request(f"http://127.0.0.1:{PORT}/tests/{d['test_uid']}/plugs/{PLUG}", data=json.dumps({'method': 'respond', 'args': [prompt['id'], ans]}).encode(), method='POST')
            await asyncio.get_event_loop().run_in_executor(None, lambda: urllib.request.urlopen(req, timeout=5).read())
        if rec.get('outcome') and status == 'COMPLETED':
            ms = {k: (v.get('measured_value'), v.get('outcome')) for p in rec.get('phases', []) for k, v in (p.get('measurements') or {}).items()}
            print(f"RESULT: dut {rec.get('dut_id')} outcome {rec['outcome']} | phases {[(p['name'], p.get('outcome')) for p in rec.get('phases', [])]}")
            print("        measurements", {k: (round(v[0], 4) if isinstance(v[0], float) else v[0], v[1]) for k, v in ms.items()})
            return
    print("no result within 60 s")
asyncio.run(main())
