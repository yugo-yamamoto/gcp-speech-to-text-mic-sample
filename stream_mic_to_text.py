# /// script
# dependencies = [
#   "google-cloud-speech",
#   "sounddevice",
#   "numpy",
# ]
# ///
"""Windowsのマイク音声をGoogle Cloud Speech-to-Text (Streaming API)にリアルタイムで送り、
文字起こし結果をコンソールに表示するサンプル。

事前準備:
  1. Windows側にgcloud CLIをインストール
  2. gcloud auth application-default login
  3. gcloud auth application-default set-quota-project <PROJECT_ID>
  4. gcloud services enable speech.googleapis.com --project <PROJECT_ID>

実行 (このファイルのあるフォルダで):
  uv run stream_mic_to_text.py
"""

import queue
import re
import sys

import numpy as np
import sounddevice as sd
from google.cloud import speech

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECONDS)
LANGUAGE_CODE = "ja-JP"
GAIN = 4.0  # 入力音声の感度(増幅率)。遠くの声が拾えない場合は大きくする
SILENCE_RMS_THRESHOLD = 300  # このRMS(音量)未満は無音とみなす(生の入力レベル基準)
SILENCE_HANGOVER_CHUNKS = 100  # 無音判定してもこの回数(chunk)は送信を続ける(10秒分)


def build_streaming_config():
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.LINEAR16,
        sample_rate_hertz=SAMPLE_RATE,
        language_code=LANGUAGE_CODE,
        enable_automatic_punctuation=True,
    )
    return speech.StreamingRecognitionConfig(config=config, interim_results=True)


def audio_request_generator(audio_queue):
    while True:
        chunk = audio_queue.get()
        if chunk is None:
            return
        yield speech.StreamingRecognizeRequest(audio_content=chunk)


MIC_PATTERN = re.compile(r"マイク|\bmic(rophone)?\b", re.IGNORECASE)
LOOPBACK_PATTERN = re.compile(r"ミキサー|\b(mixer|stereo mix|monitor|loopback)\b", re.IGNORECASE)


def select_mic_device():
    """既定の録音デバイスが「ステレオ ミキサー」/ Linuxの「Monitor of ...」等の
    ループバックになっている環境があるため、名前に「マイク」/"mic"を含み
    ループバック系の名前を含まない実マイクデバイスを優先的に探して選択する。
    ("Microsoft"のような無関係な単語への誤マッチを避けるため単語境界で判定する)"""
    devices = sd.query_devices()
    for i, d in enumerate(devices):
        if d["max_input_channels"] <= 0:
            continue
        name = d["name"]
        if LOOPBACK_PATTERN.search(name):
            continue
        if MIC_PATTERN.search(name):
            return i, name
    default_index = sd.default.device[0]
    return default_index, devices[default_index]["name"]


def amplify(chunk, gain):
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
    samples *= gain
    np.clip(samples, -32768, 32767, out=samples)
    return samples.astype(np.int16).tobytes()


def rms(chunk):
    samples = np.frombuffer(chunk, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples**2)))


def print_responses(responses):
    for response in responses:
        for result in response.results:
            transcript = result.alternatives[0].transcript
            if result.is_final:
                print(f"\r[確定] {transcript}", flush=True)
            else:
                print(f"\r[認識中] {transcript}", end="", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    audio_queue = queue.Queue()
    silence_chunks = 0

    def on_audio(indata, frames, time_info, status):
        nonlocal silence_chunks
        if status:
            print(status, file=sys.stderr)
        raw = bytes(indata)
        if rms(raw) >= SILENCE_RMS_THRESHOLD:
            silence_chunks = 0
        else:
            silence_chunks += 1
        if silence_chunks <= SILENCE_HANGOVER_CHUNKS:
            audio_queue.put(amplify(raw, GAIN))

    client = speech.SpeechClient()
    streaming_config = build_streaming_config()

    device_index, device_name = select_mic_device()
    print(f"入力デバイス: [{device_index}] {device_name}", flush=True)
    print("マイク入力を開始します。Ctrl+Cで終了します。", flush=True)
    with sd.RawInputStream(
        device=device_index,
        samplerate=SAMPLE_RATE,
        blocksize=CHUNK_SIZE,
        dtype="int16",
        channels=1,
        callback=on_audio,
    ):
        responses = client.streaming_recognize(
            config=streaming_config,
            requests=audio_request_generator(audio_queue),
        )
        try:
            print_responses(responses)
        except KeyboardInterrupt:
            pass
        finally:
            audio_queue.put(None)


if __name__ == "__main__":
    main()
