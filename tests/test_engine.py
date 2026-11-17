import sys, time, numpy as np
from tempfile import TemporaryDirectory
sys.path.insert(0, '.')
from app.model import *
from app.audio.engine import Engine, BLOCK, SR
from app.audio.ring import Ring
from app.audio import engine as E
import soundfile as sf

# test file
t = np.arange(SR*2)/SR
tmp = TemporaryDirectory()
tone_path = str(Path(tmp.name) / 'tone.wav')
sf.write(tone_path, (0.5*np.sin(2*np.pi*440*t)).astype('float32'), 44100)

class FakeDev:
    def __init__(s, oid): s.out_id=oid; s.ring=Ring(SR); s.error=''; s.stream=None; s.name=''; s.hostapi=''; s.volume=1
    def stop(s): pass
    def start(s): return True

p = Project()
p.outputs=[Output('o1','Уши'), Output('o2','Кабель')]
fx = EffectSlot('fx1','delay',False,{})
p.channels=[Channel('music','Музыка',volume=1.0,outputs=['o1','o2'],transition='crossfade',transition_ms=500, effects=[fx]),
            Channel('sfx','Эффекты',volume=1.0,outputs=['o1'])]
p.pads=[Pad('a','A',file=tone_path,channel='music',mode='toggle',loop=True),
        Pad('b','B',file=tone_path,channel='music',mode='oneshot', exclude_outputs=['o1']),
        Pad('c','C',file=tone_path,channel='sfx',mode='hold',trim_start=0.5,trim_end=1.0)]
p.repair()
eng = Engine()
for pad in p.pads: eng.cache.load_now(pad.file)
print('decoded', eng.cache.get(tone_path).shape)
eng.project=p
import app.audio.engine as em
em.OutputDevice = lambda oid,n,h,sr: FakeDev(oid)
eng.apply_project(p)
print('outs', list(eng.outputs))
eng.pad_event('a','press')
m = [eng.render_block() for _ in range(50)]
print('A playing o1 peak', abs(m[-1]['o1']).max().round(3), 'o2', abs(m[-1]['o2']).max().round(3))
eng.pad_event('b','press')   # crossfade A->B, B excluded from o1
for _ in range(80): m = eng.render_block()
print('after crossfade o1', abs(m['o1']).max().round(3), 'o2', abs(m['o2']).max().round(3), eng.pad_states())
eng.pad_event('c','press')
for _ in range(10): m = eng.render_block()
print('hold c o1', abs(m['o1']).max().round(3), eng.pad_states())
eng.pad_event('c','release')
for _ in range(10): m = eng.render_block()
print('released', eng.pad_states())
eng.set_effect('music','fx1',True)
eng.pad_event('a','press')
for _ in range(20): m = eng.render_block()
eng.stop_all()
for _ in range(30): m = eng.render_block()
print('after stop states', eng.pad_states(), 'echo tail o2', abs(m['o2']).max().round(3))
# perf
eng.pad_event('a','press')
for t_ in ['reverb','pitch','chorus','radio','compressor']:
    p.channels[0].effects.append(EffectSlot(new_id('fx'), t_, True, {}))
eng.sync_effects('music')
t0=time.perf_counter()
for _ in range(300): eng.render_block()
dt=(time.perf_counter()-t0)/300
print(f'render with 6 fx: {dt*1000:.2f} ms per 10ms block')
