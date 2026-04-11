# nvidia-fan-curve

NVIDIA GPU のファン速度を NVML 経由で制御する Python スクリプトです。

X11 に依存せず、Wayland / ヘッドレス / SSH 環境でも動かせます。温度に応じた線形補間、ヒステリシス、フェイルセーフ、終了時の自動制御復帰フォールバックを備えています。

## 特徴

- X11 非依存
- 温度とファン速度の線形補間
- 温度下降時のヒステリシス制御
- 温度取得失敗時のフェイルセーフ速度適用
- 終了時の自動ファン制御復帰処理
- GeForce を含む NVIDIA GPU 向けの NVML ベース実装

## 要件

- NVIDIA ドライバ 520 以降
- Python 3
- `nvidia-ml-py`
- root 権限

インストール:

```bash
pip install nvidia-ml-py
```

`pynvml` という別名の非公式パッケージではなく、`nvidia-ml-py` を使う前提です。

## 使い方

1. [nvidia-fan-curve.py](./nvidia-fan-curve.py) の「ユーザー設定セクション」を編集する
2. root 権限で起動する

```bash
sudo python3 nvidia-fan-curve.py
```

停止するときは `Ctrl+C` で終了できます。終了時には自動ファン制御への復帰を試みます。

## 設定項目

設定は [nvidia-fan-curve.py](./nvidia-fan-curve.py) 冒頭の定数で行います。

- `FAN_CURVE`
  温度とファン速度の対応表です。各点の間は線形補間されます。
- `POLL_INTERVAL`
  温度を読み取る間隔です。デフォルトは `3` 秒です。
- `HYSTERESIS`
  温度下降時にファン速度を下げるための余裕幅です。デフォルトは `5` ℃です。
- `GPU_INDEX`
  制御対象の GPU 番号です。デフォルトは `0` です。
- `MIN_DRIVER_MAJOR`
  必須ドライバのメジャーバージョンです。デフォルトは `520` です。
- `FAILSAFE_SPEED`
  温度取得失敗や想定外の異常時に適用する速度です。デフォルトは `100` %です。
- `SHUTDOWN_SAFE_SPEED`
  終了時に自動制御復帰が効かない環境向けの保険速度です。デフォルトは `60` %です。
- `LOG_LEVEL`
  ログ出力レベルです。

## デフォルトのファンカーブ

```text
30C -> 30%
45C -> 35%
60C -> 45%
70C -> 65%
80C -> 85%
90C -> 100%
```

## systemd で動かす例

常駐させたい場合は systemd サービス化できます。

```ini
[Unit]
Description=NVIDIA fan curve controller
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /path/to/nvidia-fan-curve.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

配置後の例:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nvidia-fan-curve.service
```

## 注意

- GPU やドライバによっては手動ファン制御が制限されることがあります
- root 権限なしでは動きません
- 誤ったカーブ設定は冷却不足や騒音増加につながるので注意してください
- 終了時の自動制御復帰はドライバ実装依存です

## License

MIT
