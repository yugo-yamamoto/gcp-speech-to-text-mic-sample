# /// script
# dependencies = [
#   "google-cloud-speech",
#   "sounddevice",
#   "numpy",
# ]
# ///
"""Windowsのマイク音声をGoogle Cloud Speech-to-Text v2 (chirp_2モデル)にリアルタイムで送り、
文字起こし結果をコンソールに表示するサンプル。

無音時はストリームを閉じて待機し、音声を検知したら新しいストリームを開く。
(ストリームを開きっぱなしにするとAPIの305秒制限で例外になるため)

chirp_2はグローバルエンドポイントでは使えないため、リージョナルエンドポイント
(us-central1)に接続する。

事前準備:
  1. Windows側にgcloud CLIをインストール
  2. gcloud auth application-default login
  3. gcloud auth application-default set-quota-project <PROJECT_ID>
  4. gcloud services enable speech.googleapis.com --project <PROJECT_ID>

実行 (このファイルのあるフォルダで。マイクにアクセスするため必ずWindows側のuvで実行する):
  WSLのシェルから:            uv.exe run stream_mic_to_text.py
  PowerShell/コマンドプロンプトから: uv run stream_mic_to_text.py
"""

import collections
import queue
import re
import sys
import time

import google.auth
import numpy as np
import sounddevice as sd
from google.api_core.client_options import ClientOptions
from google.cloud import speech_v2
from google.cloud.speech_v2.types import cloud_speech

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1
CHUNK_SIZE = int(SAMPLE_RATE * CHUNK_SECONDS)
LANGUAGE_CODE = "ja-JP"
MODEL = "chirp_2"
LOCATION = "us-central1"  # chirp_2が利用可能なリージョン(globalでは使えない)
GAIN = 4.0  # 入力音声の感度(増幅率)。遠くの声が拾えない場合は大きくする
SILENCE_RMS_THRESHOLD = 300  # このRMS(音量)未満は無音とみなす(生の入力レベル基準)
SILENCE_CLOSE_SECONDS = 2.0  # 無音がこの秒数続いたらストリームを閉じて確定させる
PREROLL_SECONDS = 0.5  # 発話検知時に遡って送る直前の音声(語頭切れ防止)
STREAM_MAX_SECONDS = 240  # 1ストリームの最長時間。APIの305秒制限の手前で開き直す

SILENCE_CLOSE_CHUNKS = int(SILENCE_CLOSE_SECONDS / CHUNK_SECONDS)
PREROLL_CHUNKS = int(PREROLL_SECONDS / CHUNK_SECONDS)


def resolve_project_id():
    _, project_id = google.auth.default()
    if not project_id:
        sys.exit(
            "GCPプロジェクトIDを特定できませんでした。"
            "`gcloud auth application-default set-quota-project <PROJECT_ID>` を実行してください。"
        )
    return project_id


def build_config_request(project_id):
    """ストリームの先頭で送る設定リクエスト(v2では最初のリクエストで
    recognizerとStreamingRecognitionConfigを指定する)"""
    recognition_config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=SAMPLE_RATE,
            audio_channel_count=1,
        ),
        language_codes=[LANGUAGE_CODE],
        model=MODEL,
    )
    streaming_config = cloud_speech.StreamingRecognitionConfig(
        config=recognition_config,
        streaming_features=cloud_speech.StreamingRecognitionFeatures(interim_results=True),
    )
    return cloud_speech.StreamingRecognizeRequest(
        recognizer=f"projects/{project_id}/locations/{LOCATION}/recognizers/_",
        streaming_config=streaming_config,
    )


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


SPINNER_FRAMES = "|/-\\"


def wait_for_speech(audio_queue, preroll):
    """閾値以上の音を検知するまで待機する。待機中の音声はprerollに保持され、
    ストリーム開始時に遡って送信することで語頭の欠けを防ぐ。
    待機中はスピナーのみを表示する。"""
    frame = 0
    try:
        while True:
            print(f"\r{SPINNER_FRAMES[frame % len(SPINNER_FRAMES)]}", end="", flush=True)
            frame += 1
            chunk = audio_queue.get()
            preroll.append(chunk)
            if rms(chunk) >= SILENCE_RMS_THRESHOLD:
                return
    finally:
        print("\r \r", end="", flush=True)


def session_requests(config_request, audio_queue, preroll):
    """1ストリーム分のリクエストを生成する。無音がSILENCE_CLOSE_SECONDS続くか、
    ストリーム開始からSTREAM_MAX_SECONDS経過したらreturnしてストリームを閉じる。
    (閉じた時点でAPIが残りの認識結果をis_finalで返す)"""
    yield config_request
    deadline = time.monotonic() + STREAM_MAX_SECONDS
    for chunk in preroll:
        yield cloud_speech.StreamingRecognizeRequest(audio=amplify(chunk, GAIN))
    preroll.clear()
    silence_chunks = 0
    while time.monotonic() < deadline:
        try:
            chunk = audio_queue.get(timeout=1.0)
        except queue.Empty:
            # 入力ストリームが止まった場合は音声を待たずに閉じる
            return
        yield cloud_speech.StreamingRecognizeRequest(audio=amplify(chunk, GAIN))
        if rms(chunk) < SILENCE_RMS_THRESHOLD:
            silence_chunks += 1
            if silence_chunks >= SILENCE_CLOSE_CHUNKS:
                return
        else:
            silence_chunks = 0


def print_responses(responses):
    for response in responses:
        for result in response.results:
            if not result.alternatives:
                continue
            transcript = result.alternatives[0].transcript
            if result.is_final:
                print(f"\r[確定] {transcript}", flush=True)
            else:
                print(f"\r[認識中] {transcript}", end="", flush=True)


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    audio_queue = queue.Queue()

    def on_audio(indata, frames, time_info, status):
        if status:
            print(status, file=sys.stderr)
        audio_queue.put(bytes(indata))

    project_id = resolve_project_id()
    client = speech_v2.SpeechClient(
        client_options=ClientOptions(api_endpoint=f"{LOCATION}-speech.googleapis.com")
    )
    config_request = build_config_request(project_id)

    device_index, device_name = select_mic_device()
    print(f"入力デバイス: [{device_index}] {device_name}", flush=True)
    print(f"モデル: {MODEL} ({LOCATION})", flush=True)
    print("マイク入力を開始します。Ctrl+Cで終了します。", flush=True)
    preroll = collections.deque(maxlen=PREROLL_CHUNKS)
    try:
        with sd.RawInputStream(
            device=device_index,
            samplerate=SAMPLE_RATE,
            blocksize=CHUNK_SIZE,
            dtype="int16",
            channels=1,
            callback=on_audio,
        ):
            while True:
                wait_for_speech(audio_queue, preroll)
                responses = client.streaming_recognize(
                    requests=session_requests(config_request, audio_queue, preroll)
                )
                print_responses(responses)
    except KeyboardInterrupt:
        print("\n終了します。", flush=True)


if __name__ == "__main__":
    main()
