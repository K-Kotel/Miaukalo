import sys; sys.path.insert(0,'.')
from app.model import *
from app.input.router import InputRouter, key_id
calls=[]
class E:
    project=None
    def pad_event(s,p,k): calls.append(('pad',p,k))
    def step_volume(s,c,st): calls.append(('vol',c,st))
    def set_volume(s,c,v): calls.append(('set',c,round(v,2)))
    def set_muted(s,c,m): calls.append(('mute',c,m)); s.project.channel(c).muted=m
    def set_effect(s,c,f,e): calls.append(('fx',c,f,e))
    def stop_all(s): calls.append('stopall')
p=Project(); p.channels=[Channel('mic','Mic')]
p.bindings=[Binding('b1',Trigger('key','ctrl+num 1'),{'type':'pad','pad':'x'}),
            Binding('b2',Trigger('key','num plus'),{'type':'volume_step','channel':'mic','step':0.05}),
            Binding('b3',Trigger('midi_cc',number=7),{'type':'volume_set','channel':'mic'}),
            Binding('b4',Trigger('midi_note',number=36),{'type':'mute','channel':'mic','mode':'ptt'})]
e=E(); e.project=p
r=InputRouter(e); r.set_project(p)
print(key_id('1',79,True), key_id('+',78,True), key_id('right ctrl',29,False), key_id('ф',30,False))
r.handle_key('ctrl',True); r.handle_key('num 1',True); r.handle_key('num 1',True); r.handle_key('ctrl',False); r.handle_key('num 1',False)
r.handle_key('num 1',True); r.handle_key('num 1',False)  # no ctrl: nothing
r.handle_key('num plus',True); r.handle_key('num plus',True); r.handle_key('num plus',False)
import mido
r.handle_midi('pad', mido.Message('control_change',control=7,value=64))
r.handle_midi('pad', mido.Message('note_on',note=36,velocity=100)); r.handle_midi('pad', mido.Message('note_on',note=36,velocity=0))
got=[]; r.start_capture(got.append); r.handle_key('shift',True); r.handle_key('f13',True); r.handle_key('f13',False); r.handle_key('shift',False)
r.start_capture(got.append); r.handle_key('alt',True); r.handle_key('alt',False)
r.start_capture(got.append); r.handle_midi('pad', mido.Message('note_on',note=40,velocity=3))
print(calls); print([g.label() for g in got])
