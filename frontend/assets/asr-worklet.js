// 语音输入采样工作块：把麦克风流降采样为 16kHz 单声道 PCM（s16le），逐块交给主线程。
// 服务端按 DashScope 实时识别协议直接转发 PCM 二进制流，故浏览器侧统一到 16k/单声道。
class AsrPcmProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._step = Math.max(1, Math.round(sampleRate / 16000));
  }

  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (!ch || !ch.length) return true;
    const n = Math.floor(ch.length / this._step);
    const out = new Int16Array(n);
    for (let i = 0; i < n; i++) {
      let s = ch[i * this._step];
      if (s > 1) s = 1;
      else if (s < -1) s = -1;
      out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    this.port.postMessage(out.buffer, [out.buffer]);
    return true;
  }
}

registerProcessor("asr-pcm", AsrPcmProcessor);
