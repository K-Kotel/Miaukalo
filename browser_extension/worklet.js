// Собирает звук блоками по 20 мс (960 кадров, стерео вперемешку) и отдаёт их в offscreen.js.
class PcmTap extends AudioWorkletProcessor {
  constructor() {
    super();
    this.frames = 960;
    this.buf = new Float32Array(this.frames * 2);
    this.n = 0;
  }
  process(inputs) {
    const inp = inputs[0];
    if (!inp || inp.length === 0) return true;
    const L = inp[0];
    const R = inp[1] || inp[0];
    for (let i = 0; i < L.length; i++) {
      this.buf[this.n++] = L[i];
      this.buf[this.n++] = R[i];
      if (this.n >= this.buf.length) {
        const out = this.buf;
        this.port.postMessage(out.buffer, [out.buffer]);
        this.buf = new Float32Array(this.frames * 2);
        this.n = 0;
      }
    }
    return true;
  }
}
registerProcessor("pcm-tap", PcmTap);
