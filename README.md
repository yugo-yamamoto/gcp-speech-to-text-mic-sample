# Google Cloud Speech-to-Text マイク文字起こしサンプル

Windows上のマイク音声をキャプチャし、Google Cloud Speech-to-Text の Streaming API に
リアルタイムで送って文字起こし結果をコンソールに表示するサンプルです。

## アーキテクチャ

```
マイク (Windows, sounddevice)
  → PCM 16kHz/16bit チャンク
  → Google Cloud Speech-to-Text Streaming API
  → 認識結果 (途中経過 / 確定) をコンソール表示
```

WSL経由だとマイクデバイスへのアクセスが不安定なため、Python自体をWindows側の `uv.exe` で
実行し、Windowsネイティブにマイクへアクセスする構成にしています。

## 事前準備

### 1. gcloud CLI をWindowsにインストール

以下からインストーラーをダウンロードして実行してください。

https://cloud.google.com/sdk/docs/install

インストール後、PowerShellまたはコマンドプロンプトを開き直して確認します。

```powershell
gcloud --version
```

### 2. ログインしてADC(Application Default Credentials)を作成

```powershell
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
gcloud auth application-default login
gcloud auth application-default set-quota-project <YOUR_PROJECT_ID>
```

### 3. Speech-to-Text APIを有効化

```powershell
gcloud services enable speech.googleapis.com --project <YOUR_PROJECT_ID>
```

## 実行方法

このフォルダで以下を実行します（Windowsでは`uv`インストール時にPATHが通るため、
`uv.exe`をフルパスで指定する必要はありません）。

```powershell
cd <このリポジトリのパス>
uv run stream_mic_to_text.py
```

マイクに向かって話すと、認識中の文字列と確定した文字列がコンソールに表示されます。
`Ctrl+C` で終了します。

## 制限事項

- Google Cloud Speech-to-Text の Streaming API は1セッション最大305秒までという制限があります。
  このサンプルはその再接続処理を行っていないため、長時間の連続認識が必要な場合はストリームの
  再接続ロジックを追加してください。
- 既定では日本語 (`ja-JP`) で認識します。言語を変えたい場合は `stream_mic_to_text.py` 内の
  `LANGUAGE_CODE` を変更してください。
- 使用するマイクは名前に「マイク」/"mic"を含み「ミキサー」/"monitor"/"loopback"等の
  ループバック系デバイスを含まないものを自動選択します（既定の録音デバイスが
  ステレオミキサーやLinuxの"Monitor of ..."のようなループバックになっている環境が
  あるため）。この判定は英語名（Linux/macOS）と日本語名（Windows）の両方に対応しています。
  別のデバイスを使いたい場合は `stream_mic_to_text.py` の `select_mic_device()` を変更してください。
- 遠くの声が拾いにくい場合は `stream_mic_to_text.py` の `GAIN` の値を大きくしてください
  （デジタルでの増幅なので、上げすぎるとノイズも一緒に増幅されます）。
  ノートPC内蔵のマイクアレイ(Intel Smart Sound Technology等)は近接話者向けの
  ビームフォーミング/ノイズ抑制がハードウェア・ドライバー側でかかっている場合があり、
  `GAIN` を上げても改善しないことがあります。その場合はWindowsの「サウンド」設定
  → 録音デバイスのプロパティ → レベル/詳細タブで「マイクブースト」や「オーディオ強化
  (ノイズ抑制等)」の設定を確認・調整してください。
