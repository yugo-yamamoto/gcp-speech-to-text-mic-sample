# マイク音声リアルタイム文字起こしツール (Google Cloud Speech-to-Text v2 / chirp_2)

Windows上のマイク音声をキャプチャし、Google Cloud Speech-to-Text v2 の Streaming API
(**chirp_2** モデル)にリアルタイムで送って文字起こし結果をコンソールに表示するツールです。

## 目的と実現したかったこと

- **マイクの音声をリアルタイムに文字起こしする**(話し終わったら速やかに確定結果が出る)
- **「えー」「あのー」「えっと」などのフィラーを除去せず、元の発音のままテキスト化する**
  (一般的な音声認識サービスはフィラーを削除して整文してしまうが、それをさせない)
- 無音時に無駄なAPI課金・接続をせず、話したときだけストリームを開く
- `uv.exe run stream_mic_to_text.py`(Windows側のuv)の1コマンドだけで動く
  (pip install不要・venv不要)

## アーキテクチャ

```
マイク (Windows, sounddevice)
  → PCM 16kHz/16bit チャンク (RMSで無音判定)
  → [発話検知時のみ] Speech-to-Text v2 Streaming API (chirp_2, us-central1)
  → 確定結果をコンソール表示
```

- **発話単位のストリーム開閉**: 待機中はストリームを開かずローカルでRMS(音量)を監視し、
  スピナーだけを表示。閾値以上の音を検知したらストリームを開き、無音が2秒続いたら
  ストリームを閉じる。閉じた時点でAPIが残りの認識結果を確定(is_final)で返す。
- **プリロール**: 待機中も直近0.5秒の音声をリングバッファに保持し、発話検知時に
  遡って送信することで語頭の欠けを防ぐ。
- **305秒制限ガード**: 発話が延々と続く場合も240秒でストリームを閉じて開き直し、
  APIの1ストリーム305秒制限による例外を回避する。
- WSL経由だとマイクデバイスにアクセスできないため、Python自体をWindows側の `uv` で
  実行し、Windowsネイティブにマイクへアクセスする構成にしています。

## 開発の経緯(ハマりどころと対処)

1. **確定(is_final)が全く出ない問題**
   当初はv1 APIで音声を送りっぱなしにする構成だったが、`[認識中]` のまま確定しなかった。
   原因は2つ: (1) `GAIN` による増幅でノイズフロアまで大きくなり、Google側のVADが
   「無音」を検知できず発話終了と判定できない、(2) ローカルの無音ゲートが送信自体を
   止めてしまい、確定に必要な無音がAPIに届かない。
   → **確定をGoogleのVAD任せにせず、ローカルのRMS判定で無音2秒を検知したら
   ストリームを閉じて強制的に確定させる**方式に変更して解決。

2. **305秒制限の例外で落ちる問題**
   確定させるために無音を送り続けるとストリームが305秒制限に達して例外で落ちる。
   → 上記の発話単位のストリーム開閉 + 240秒ガードで解決。ストリームを
   開きっぱなしにしない設計はコスト面でも有利。

3. **フィラーが除去される問題**
   v1のデフォルトモデルは「えー」「あのー」を削除して整文してしまう。
   v1にはフィラー保持のパラメータは存在しない。`model="latest_long"` も試したが
   フィラーは除去された。
   → **v2 API + chirp_2 モデル**に移行して解決。chirp_2 は発話に忠実(verbatim寄り)な
   書き起こしをするモデルで、「えっと」などがそのまま出力されることを実機で確認済み。

## 排除した選択肢とその理由

| 選択肢 | 排除した理由 |
|---|---|
| v1 デフォルトモデル | フィラーを除去して整文する。保持するパラメータが存在しない |
| v1 `latest_long` モデル | verbatim寄りと言われるが、実際に試すとフィラーが除去された |
| 無音を送り続けてストリーム維持 | 305秒制限の例外で落ちる。無音送信分も課金される |
| AmiVoice API | `keepFillerToken=1` でフィラー保持を公式サポートする有力候補だったが、別サービスの契約・APIキー管理が増える。chirp_2で解決したため不要に |
| ローカルWhisper (faster-whisper) | Whisper自体にフィラー除去傾向があり、`initial_prompt` ハックは不安定。ストリーミング処理の自作も必要 |
| Azure AI Speech | lexical(整形前)出力はあるが、日本語フィラーが残る保証がない |

## chirp_2 の既知の制約

- **リージョナルエンドポイント必須**: globalでは使えないため `us-central1` に接続している
  (日本からだとレイテンシは若干増えるが実用上問題ないレベル)。
- **途中経過(interim results)が返らない**: `interim_results=True` を指定しても
  `[認識中]` の逐次表示は出ず、発話区切りごとの確定結果のみ返る。
  無音2秒でストリームを閉じる本ツールの構造では確定が速いため実用上の支障は小さい。

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

プロジェクトIDはADCのquota projectから自動解決されます。

### 3. Speech-to-Text APIを有効化

```powershell
gcloud services enable speech.googleapis.com --project <YOUR_PROJECT_ID>
```

## 実行方法

**必ずWindows側のuv(`uv.exe`)で実行してください。** WSL/Linux側の`uv`で実行すると
マイクデバイスにアクセスできず `PortAudioError` で失敗します。

WSLのシェルから実行する場合(`uv.exe`と明示することでWindows側のuvが使われる):

```bash
cd /mnt/c/Users/<USERNAME>/work/speech-to-text
uv.exe run stream_mic_to_text.py
```

PowerShell/コマンドプロンプトから実行する場合(こちらは`uv`でWindows側が使われる):

```powershell
cd <このリポジトリのパス>
uv run stream_mic_to_text.py
```

待機中はスピナー(`|/-\`)が回り、マイクに向かって話すと確定した文字列が
`[確定] ...` として表示されます。`Ctrl+C` で終了します。

## 調整パラメータ (`stream_mic_to_text.py` 冒頭)

| パラメータ | 既定値 | 説明 |
|---|---|---|
| `SILENCE_CLOSE_SECONDS` | 2.0 | 無音がこの秒数続いたらストリームを閉じて確定させる。短くすると確定が速いが文の途中の間(ま)で切れやすい |
| `SILENCE_RMS_THRESHOLD` | 300 | このRMS未満を無音とみなす。環境ノイズで無音判定されないなら上げる、声を拾わないなら下げる |
| `PREROLL_SECONDS` | 0.5 | 発話検知時に遡って送る音声の長さ。語頭が欠けるなら増やす |
| `STREAM_MAX_SECONDS` | 240 | 1ストリームの最長時間(305秒制限の手前で開き直す) |
| `GAIN` | 4.0 | 入力音声の増幅率。遠くの声が拾えない場合に上げる |
| `LANGUAGE_CODE` | ja-JP | 認識言語 |
| `MODEL` / `LOCATION` | chirp_2 / us-central1 | 認識モデルとリージョン(chirp_2はglobal非対応) |

## その他の注意事項

- 使用するマイクは名前に「マイク」/"mic"を含み「ミキサー」/"monitor"/"loopback"等の
  ループバック系デバイスを含まないものを自動選択します（既定の録音デバイスが
  ステレオミキサーやLinuxの"Monitor of ..."のようなループバックになっている環境が
  あるため）。この判定は英語名（Linux/macOS）と日本語名（Windows）の両方に対応しています。
  別のデバイスを使いたい場合は `stream_mic_to_text.py` の `select_mic_device()` を変更してください。
- 遠くの声が拾いにくい場合は `GAIN` の値を大きくしてください
  （デジタルでの増幅なので、上げすぎるとノイズも一緒に増幅されます）。
  ノートPC内蔵のマイクアレイ(Intel Smart Sound Technology等)は近接話者向けの
  ビームフォーミング/ノイズ抑制がハードウェア・ドライバー側でかかっている場合があり、
  `GAIN` を上げても改善しないことがあります。その場合はWindowsの「サウンド」設定
  → 録音デバイスのプロパティ → レベル/詳細タブで「マイクブースト」や「オーディオ強化
  (ノイズ抑制等)」の設定を確認・調整してください。
