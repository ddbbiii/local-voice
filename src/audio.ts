const SAMPLE_RATE = 16_000;

function floatTo16BitPCM(view: DataView, offset: number, input: Float32Array) {
  let position = offset;
  for (let i = 0; i < input.length; i += 1) {
    const sample = Math.max(-1, Math.min(1, input[i]));
    view.setInt16(position, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
    position += 2;
  }
}

function writeString(view: DataView, offset: number, value: string) {
  for (let i = 0; i < value.length; i += 1) {
    view.setUint8(offset + i, value.charCodeAt(i));
  }
}

function encodeWav(buffers: Float32Array[]): Blob {
  const sampleCount = buffers.reduce((sum, buffer) => sum + buffer.length, 0);
  const wavBuffer = new ArrayBuffer(44 + sampleCount * 2);
  const view = new DataView(wavBuffer);

  writeString(view, 0, "RIFF");
  view.setUint32(4, 36 + sampleCount * 2, true);
  writeString(view, 8, "WAVE");
  writeString(view, 12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(view, 36, "data");
  view.setUint32(40, sampleCount * 2, true);

  let offset = 44;
  for (const buffer of buffers) {
    floatTo16BitPCM(view, offset, buffer);
    offset += buffer.length * 2;
  }

  return new Blob([view], { type: "audio/wav" });
}

export class AudioRecorder {
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private processor: ScriptProcessorNode | null = null;
  private monitorGain: GainNode | null = null;
  private chunks: Float32Array[] = [];

  async start() {
    this.chunks = [];
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      this.context = new AudioContext({ sampleRate: SAMPLE_RATE });
      this.source = this.context.createMediaStreamSource(this.stream);
      this.processor = this.context.createScriptProcessor(4096, 1, 1);
      this.monitorGain = this.context.createGain();
      this.monitorGain.gain.value = 0;

      this.processor.onaudioprocess = (event: AudioProcessingEvent) => {
        const input = event.inputBuffer.getChannelData(0);
        this.chunks.push(new Float32Array(input));
      };

      this.source.connect(this.processor);
      this.processor.connect(this.monitorGain);
      this.monitorGain.connect(this.context.destination);
    } catch (error) {
      await this.cleanup();
      throw error;
    }
  }

  async stop(): Promise<Blob> {
    if (!this.context || !this.processor || !this.source || !this.stream || !this.monitorGain) {
      throw new Error("Recorder is not active.");
    }

    this.processor.disconnect();
    this.monitorGain.disconnect();
    this.source.disconnect();
    await this.cleanup();

    return encodeWav(this.chunks);
  }

  private async cleanup() {
    this.stream?.getTracks().forEach((track) => track.stop());
    if (this.context && this.context.state !== "closed") {
      await this.context.close();
    }

    this.processor = null;
    this.monitorGain = null;
    this.source = null;
    this.stream = null;
    this.context = null;
  }
}
