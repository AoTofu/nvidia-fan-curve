# nvidia-fan-curve

> Wayland / ヘッドレス環境でも確実に動く、NVIDIA GPU のためのシンプルなファンカーブ制御デーモン

[![Python](https://img.shields.io/badge/Python-3.9+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![NVIDIA](https://img.shields.io/badge/NVIDIA-Driver_520+-76B900?logo=nvidia&logoColor=white)](https://www.nvidia.com/)
[![Platform](https://img.shields.io/badge/Platform-Linux-FCC624?logo=linux&logoColor=black)](https://www.kernel.org/)
[![License](https://img.shields.io/badge/License-MIT-blue)](#ライセンス)

---

## 概要

`nvidia-fan-curve` は、NVIDIA GPU のファン速度を温度に応じて自動制御する、軽量な常駐型 Python スクリプトです。

従来の `nvidia-settings` 方式と異なり、**X11 セッションも `Coolbits` 設定も不要**。NVML (NVIDIA Management Library) を直接叩くため、Wayland、ヘッドレスサーバー、SSH 越しなど、あらゆる環境で確実に動作します。

## なぜこれが必要か

Linux で NVIDIA GPU のファンカーブを自分好みに設定するのは、長年つらい作業でした。

| 方式 | X11 必須 | Wayland 対応 | ヘッドレス対応 | 設定の手間 |
|---|:---:|:---:|:---:|:---:|
| `nvidia-settings` (従来方式) | ✅ 必須 | ❌ | ❌ (要 dummy X) | `Coolbits` 設定必要 |
| **NVML (本プロジェクト)** | ❌ 不要 | ✅ | ✅ | なし |

NVIDIA は ドライバ 515 で `nvmlDeviceSetFanSpeed_v2` を追加し、520 で GeForce 全般に開放しました。本スクリプトはこの新しい API を活用し、X11 依存から完全に解放されています。

## 特徴

- 🚀 **X11 非依存** — Wayland / ヘッドレス / SSH 環境でそのまま動く
- 📈 **線形補間カーブ** — 自由に定義できる多点ファンカーブ
- 🛡️ **ヒステリシス制御** — ファン速度が頻繁に上下しないよう温度差を持たせて制御
- 🔧 **マルチファン対応** — RTX シリーズの 2 〜 3 ファン構成を自動検出
- ⚡ **フェイルセーフ** — 温度取得失敗時は自動で 100% に。終了時も安全速度を保証
- 🪶 **軽量** — メモリ使用量は約 25 MB、依存は `nvidia-ml-py` のみ
- 🔍 **起動時診断** — root 権限・ドライババージョン・ハードウェア対応を起動時にチェックし、問題があれば即座に詳細なエラーで停止
- 🧯 **ドライババグ対策** — `nvmlDeviceSetDefaultFanSpeed_v2` の既知バグに対する保険機構を内蔵

## 動作要件

| 項目 | 要件 |
|---|---|
| OS | Linux (Fedora / Ubuntu / Arch など) |
| NVIDIA ドライバ | **520 以降** (`nvidia-smi` で確認) |
| Python | 3.9 以上 |
| 権限 | root (systemd またはsudo 経由) |
| GPU | NVML ファン制御対応の NVIDIA GPU (Maxwell 以降) |

## インストール

### 1. 依存パッケージのインストール

```bash
sudo pip install nvidia-ml-py --break-system-packages
```

> [!NOTE]
> Fedora や最近の Ubuntu では PEP 668 により `--break-system-packages` が必要です。venv を使う場合は後述の[「venv で運用する」](#venv-で運用する)を参照してください。

### 2. スクリプトの配置

```bash
sudo mkdir -p /opt/nvidia-fan-curve
sudo curl -L -o /opt/nvidia-fan-curve/nvidia-fan-curve.py \
    https://raw.githubusercontent.com/<your-username>/nvidia-fan-curve/main/nvidia-fan-curve.py
sudo chmod 644 /opt/nvidia-fan-curve/nvidia-fan-curve.py
```

> URL の `<your-username>` はあなたの GitHub ユーザー名に置き換えてください。

### 3. systemd サービスとして登録

```bash
sudo tee /etc/systemd/system/nvidia-fan-curve.service > /dev/null << 'EOF'
[Unit]
Description=NVIDIA GPU Fan Curve Control
After=multi-user.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/nvidia-fan-curve/nvidia-fan-curve.py
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now nvidia-fan-curve.service
```

### 4. 動作確認

```bash
sudo systemctl status nvidia-fan-curve.service
journalctl -u nvidia-fan-curve.service -f
```

`Active: active (running)` と表示され、ログに GPU 名・ファン数・初期速度が出力されていれば成功です。

## 設定

`/opt/nvidia-fan-curve/nvidia-fan-curve.py` の冒頭にある `ユーザー設定セクション` を編集します。

### ファンカーブ

```python
FAN_CURVE: list[tuple[int, int]] = [
    (30,  30),   # 30℃以下 → 30%
    (45,  35),
    (60,  45),
    (70,  65),
    (80,  85),
    (90, 100),   # 90℃以上 → 100%
]
```

`(温度℃, ファン速度%)` のタプルを温度の昇順で並べます。点と点の間は線形補間されます。

### その他の設定

| パラメータ | デフォルト | 説明 |
|---|---|---|
| `POLL_INTERVAL` | `3` | 温度ポーリング間隔（秒） |
| `HYSTERESIS` | `5` | ファン速度を下げる前に必要な温度低下幅（℃） |
| `GPU_INDEX` | `0` | 対象 GPU 番号（複数 GPU 環境で使用） |
| `FAILSAFE_SPEED` | `100` | 温度取得失敗時のフェイルセーフ速度（%） |
| `SHUTDOWN_SAFE_SPEED` | `60` | 終了時に保証する最低ファン速度（%） |
| `LOG_LEVEL` | `INFO` | ログレベル（`DEBUG` で詳細出力） |

設定変更後の反映:

```bash
sudo systemctl restart nvidia-fan-curve.service
```

## 推奨カーブのプリセット

### 静音重視 (デフォルト)

```python
FAN_CURVE = [(30, 30), (45, 35), (60, 45), (70, 65), (80, 85), (90, 100)]
```

### バランス型

```python
FAN_CURVE = [(30, 35), (50, 45), (65, 60), (75, 80), (85, 100)]
```

### 冷却重視 (高負荷ゲーミング向け)

```python
FAN_CURVE = [(30, 40), (50, 55), (60, 70), (70, 85), (80, 100)]
```

## 管理コマンド

```bash
# サービスの状態確認
sudo systemctl status nvidia-fan-curve.service

# リアルタイムログ
journalctl -u nvidia-fan-curve.service -f

# 一時停止 (GPU は自動制御に戻る)
sudo systemctl stop nvidia-fan-curve.service

# 再開
sudo systemctl start nvidia-fan-curve.service

# 設定変更後の反映
sudo systemctl restart nvidia-fan-curve.service

# 自動起動を無効化
sudo systemctl disable nvidia-fan-curve.service
```

GPU の温度とファン速度をリアルタイムで監視:

```bash
watch -n 1 'nvidia-smi --query-gpu=temperature.gpu,fan.speed --format=csv'
```

## venv で運用する

システム Python を汚したくない場合の手順です。

```bash
sudo python3 -m venv /opt/nvidia-fan-curve/venv
sudo /opt/nvidia-fan-curve/venv/bin/pip install nvidia-ml-py
```

systemd ユニットの `ExecStart` を以下に変更:

```ini
ExecStart=/opt/nvidia-fan-curve/venv/bin/python /opt/nvidia-fan-curve/nvidia-fan-curve.py
```

## トラブルシューティング

<details>
<summary><strong>ModuleNotFoundError: No module named 'pynvml'</strong></summary>

`nvidia-ml-py` がインストールされていない、または非 root の site-packages にしか入っていません。`sudo pip install nvidia-ml-py --break-system-packages` で root のシステムパスにインストールしてください。

</details>

<details>
<summary><strong>NVML エラー: Insufficient Permissions</strong></summary>

ファン制御 API は root 権限が必須です。`sudo` 経由か systemd サービスとして実行してください。

</details>

<details>
<summary><strong>NVIDIA ドライバ X.X.X は古すぎます</strong></summary>

`nvmlDeviceSetFanSpeed_v2` はドライバ 515 で追加され、520 で GeForce 全般に対応しました。`sudo dnf upgrade` (Fedora) や `sudo apt upgrade` (Ubuntu) などでドライバを最新化してください。

</details>

<details>
<summary><strong>初回ファン速度設定に失敗</strong></summary>

主な原因:
- root で実行していない
- ドライバが古い
- 別のファン制御プロセスが動いている (`ps aux | grep nvidia` で確認)
- GPU がファン制御をサポートしていない (一部のラップトップ GPU など)

</details>

<details>
<summary><strong>ファンが暴れる / 速度が頻繁に変わる</strong></summary>

`HYSTERESIS` の値を大きく (例: 8〜10) してください。または `POLL_INTERVAL` を伸ばすのも有効です。

</details>

## アンインストール

```bash
sudo systemctl disable --now nvidia-fan-curve.service
sudo rm /etc/systemd/system/nvidia-fan-curve.service
sudo rm -rf /opt/nvidia-fan-curve
sudo systemctl daemon-reload
```

## 仕組み

```
┌─────────────────────────────────────────────────────────┐
│  起動時チェック                                          │
│  ├─ root 権限                                            │
│  ├─ NVIDIA ドライバ ≥ 520                                │
│  ├─ GPU ハンドル取得                                     │
│  └─ 初回ファン速度設定 (失敗なら即終了)                  │
└────────────────────┬────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│  メインループ (POLL_INTERVAL 秒ごと)                     │
│  ┌──────────────┐                                       │
│  │  温度を取得  │ ─→ 失敗なら FAILSAFE_SPEED            │
│  └──────┬───────┘                                       │
│         ↓                                               │
│  ┌──────────────┐                                       │
│  │  カーブ補間  │                                       │
│  └──────┬───────┘                                       │
│         ↓                                               │
│  ┌──────────────────────────────┐                       │
│  │  ヒステリシス判定             │                       │
│  │  ・上昇 → 即適用              │                       │
│  │  ・下降 → step_down 越えのみ  │                       │
│  └──────┬───────────────────────┘                       │
│         ↓                                               │
│  ┌──────────────┐                                       │
│  │  速度を設定  │                                       │
│  └──────────────┘                                       │
└────────────────────┬────────────────────────────────────┘
                     ↓
┌─────────────────────────────────────────────────────────┐
│  終了時 (SIGINT / SIGTERM)                               │
│  ├─ 自動制御に復帰要求                                   │
│  └─ SHUTDOWN_SAFE_SPEED で下限保証 (バグ対策)            │
└─────────────────────────────────────────────────────────┘
```

## 既知の制限

- **シングル GPU 前提**: マルチ GPU 環境では `GPU_INDEX` を設定し、GPU ごとに別サービスとして起動してください。
- **`nvmlDeviceSetDefaultFanSpeed_v2` のドライババグ**: 一部のドライバ世代でこの API を呼んでも実際には自動カーブが再開されないことが報告されています。本スクリプトは `SHUTDOWN_SAFE_SPEED` で安全側にフォールバックすることで対処しています。
- **Maxwell 以降の GPU 限定**: それより古い GPU では NVML のファン制御 API がサポートされません。

## 謝辞

設計にあたって以下のプロジェクトおよび資料を参考にしました。

- [Cippo95/nvidia-fan-control](https://github.com/Cippo95/nvidia-fan-control) — 線形補間とヒステリシスの実装パターン
- [HackTestes/NVML-GPU-Control](https://github.com/HackTestes/NVML-GPU-Control) — NVML 経由でのファン制御の先行実装
- [NVIDIA NVML API Reference](https://docs.nvidia.com/deploy/nvml-api/) — 公式 API ドキュメント

## ライセンス

MIT License

---

<div align="center">

**Made with ❤️ for the Linux NVIDIA community**

問題報告や Pull Request は [Issues](../../issues) からお気軽にどうぞ

</div>
