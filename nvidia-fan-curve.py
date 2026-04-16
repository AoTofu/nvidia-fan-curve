#!/usr/bin/env python3
"""
nvidia-fan-curve.py
NVIDIA GPU ファンカーブ制御スクリプト (Python + NVML 版・完全版)

X11 に依存しないため、Wayland / ヘッドレス / SSH 環境で確実に動作する。

要件:
  - NVIDIA ドライバ 520 以降 (515 で API 追加, 520 で GeForce 全般対応)
  - Python パッケージ: nvidia-ml-py (公式)
      pip install nvidia-ml-py
    ※ PyPI の "pynvml" パッケージは非公式・デモ用扱いなので非推奨
  - root 権限 (sudo で実行するか systemd サービスとして動かす)

参考:
  - NVML API Reference (docs.nvidia.com/deploy/nvml-api)
  - NVIDIA ドライバ 515/520 の change log
  - 既知のバグ: nvmlDeviceSetDefaultFanSpeed_v2 が自動カーブを再開しない
    ケースがあるため、終了時はフォールバック処理を入れている。
"""

import os
import sys
import time
import signal
import logging
from typing import Optional

from pynvml import (
    nvmlInit,
    nvmlShutdown,
    nvmlSystemGetDriverVersion,
    nvmlDeviceGetHandleByIndex,
    nvmlDeviceGetName,
    nvmlDeviceGetTemperature,
    nvmlDeviceGetNumFans,
    nvmlDeviceSetFanSpeed_v2,
    nvmlDeviceSetDefaultFanSpeed_v2,
    NVML_TEMPERATURE_GPU,
    NVMLError,
)

# ================================================================
# ユーザー設定セクション（自由に編集してください）
# ================================================================

# ファンカーブ定義: (温度℃, ファン速度%) の昇順タプル列
# 点と点の間は線形補間される
FAN_CURVE: list[tuple[int, int]] = [
    (30,  30),   # 30℃以下 → 30%
    (45,  35),
    (60,  45),
    (70,  60),
    (80,  70),
    (90, 100),   # 90℃以上 → 100%
]

# ポーリング間隔（秒）
POLL_INTERVAL = 3

# ヒステリシス温度差（℃）
# ファン速度を下げるには、現在温度がこの値ぶん下がる必要がある
HYSTERESIS = 5

# ランプレート（1秒あたりの最大ファン速度変化量, %/秒）
# None にすると即座に目標速度へ変更（旧来の動作）
# 数値を設定すると、ファン速度が時間的になめらかに変化する
#
# 例: RAMP_RATE_UP_PER_SEC = 5 のとき、30% → 80% への変化は
#     最低 (80-30)/5 = 10秒 かけて段階的に上がる
#
# 上昇と下降で別々に設定できる。一般的に:
#   - 上昇は早め (5〜10) → 熱い時はサッと冷やしたい
#   - 下降は遅め (1〜3)  → 静かな時にスーッと自然に下がる
#
# どちらか片方だけ None にして「上昇は即時、下降だけマイルド」
# のような設定も可能。
RAMP_RATE_UP_PER_SEC: Optional[float] = 8     # ファン速度上昇の最大レート (%/秒)
RAMP_RATE_DOWN_PER_SEC: Optional[float] = 2   # ファン速度下降の最大レート (%/秒)

# 対象GPU番号（0始まり）
GPU_INDEX = 0

# 必須ドライババージョン (メジャー)
# 515 で API 追加、520 で GeForce 全般に対応
MIN_DRIVER_MAJOR = 520

# 異常時フェイルセーフ速度(%)
# 温度取得失敗・想定外エラー時はこの速度に固定される
FAILSAFE_SPEED = 100

# 終了時の安全速度(%)
# nvmlDeviceSetDefaultFanSpeed_v2 が機能しない既知バグへの保険として、
# auto 復帰の後にこの速度を「下限保証」として明示的に設定する。
# 自動制御が実際に効いていれば、ドライバがこれを上書きしてくれる。
# None にするとフォールバック設定をスキップ（非推奨）。
SHUTDOWN_SAFE_SPEED: Optional[int] = 60

# ログレベル（logging.DEBUG / INFO / WARNING / ERROR）
LOG_LEVEL = logging.INFO

# ================================================================
# 内部実装
# ================================================================

logging.basicConfig(
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=LOG_LEVEL,
)
log = logging.getLogger("nvidia-fan-curve")


# ----------------------------------------------------------------
# カーブ検証 & 補間
# ----------------------------------------------------------------

def validate_curve(curve: list[tuple[int, int]]) -> None:
    """ファンカーブの妥当性を検証。問題があれば ValueError を投げる。"""
    if len(curve) < 2:
        raise ValueError("ファンカーブには最低2点必要です")
    for i in range(len(curve) - 1):
        if curve[i][0] >= curve[i + 1][0]:
            raise ValueError(
                f"温度は厳密に昇順である必要があります: "
                f"{curve[i][0]} >= {curve[i+1][0]}"
            )
        if curve[i][1] > curve[i + 1][1]:
            raise ValueError(
                f"ファン速度は昇順（同値可）である必要があります: "
                f"{curve[i][1]} > {curve[i+1][1]}"
            )
    for temp, speed in curve:
        if not (0 <= speed <= 100):
            raise ValueError(f"ファン速度は0〜100の範囲: {speed}")


def interpolate(temp: int, curve: list[tuple[int, int]]) -> int:
    """線形補間でファン速度を計算。両端はクランプ。"""
    if temp <= curve[0][0]:
        return curve[0][1]
    if temp >= curve[-1][0]:
        return curve[-1][1]
    for i in range(len(curve) - 1):
        t_lo, f_lo = curve[i]
        t_hi, f_hi = curve[i + 1]
        if t_lo <= temp < t_hi:
            t_delta = t_hi - t_lo
            f_delta = f_hi - f_lo
            t_offset = temp - t_lo
            return round(f_lo + f_delta * t_offset / t_delta)
    return curve[-1][1]


# ----------------------------------------------------------------
# 起動時環境チェック
# ----------------------------------------------------------------

def check_root() -> None:
    """root 権限を確認。なければ即終了。"""
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log.error(
            "このスクリプトは root 権限が必要です。"
            "sudo で実行するか、systemd サービスとして動かしてください。"
        )
        sys.exit(1)


def check_driver_version() -> str:
    """ドライババージョンを確認。古すぎる場合は終了。

    呼び出し前に nvmlInit() 済みであること。
    """
    try:
        driver = nvmlSystemGetDriverVersion()
        # 一部バインディングは bytes を返すので正規化
        if isinstance(driver, bytes):
            driver = driver.decode("utf-8")
    except NVMLError as e:
        log.error(f"ドライババージョン取得失敗: {e}")
        sys.exit(1)

    try:
        major = int(driver.split(".")[0])
    except (ValueError, IndexError):
        log.error(f"ドライババージョンを解釈できません: {driver}")
        sys.exit(1)

    if major < MIN_DRIVER_MAJOR:
        log.error(
            f"NVIDIA ドライバ {driver} は古すぎます。"
            f"{MIN_DRIVER_MAJOR} 以降が必要です "
            f"(nvmlDeviceSetFanSpeed_v2 はドライバ 515 で追加、"
            f"520 で GeForce 全般対応)。"
        )
        sys.exit(1)

    log.info(f"NVIDIA ドライバ: {driver}")
    return driver


# ----------------------------------------------------------------
# コントローラ本体
# ----------------------------------------------------------------

class FanController:
    """NVIDIA GPU ファンカーブコントローラー"""

    def __init__(
        self,
        gpu_index: int,
        curve: list[tuple[int, int]],
        hysteresis: int,
        poll_interval: int,
        failsafe_speed: int,
        shutdown_safe_speed: Optional[int],
        ramp_rate_up: Optional[float],
        ramp_rate_down: Optional[float],
    ):
        self.gpu_index = gpu_index
        self.curve = curve
        self.hysteresis = hysteresis
        self.poll_interval = poll_interval
        self.failsafe_speed = failsafe_speed
        self.shutdown_safe_speed = shutdown_safe_speed
        self.ramp_rate_up = ramp_rate_up
        self.ramp_rate_down = ramp_rate_down

        self.handle = None
        self.gpu_name = "?"
        self.fan_count = 0
        self.prev_fan_speed = -1
        self.step_down_temp = 0
        self.running = True
        self._nvml_inited = False

    # -------- 初期化 --------

    def init_gpu(self) -> None:
        """NVML 初期化、GPU ハンドル取得、ドライバ確認、初回設定の検証。"""
        nvmlInit()
        self._nvml_inited = True

        check_driver_version()

        try:
            self.handle = nvmlDeviceGetHandleByIndex(self.gpu_index)
        except NVMLError as e:
            log.error(f"GPU {self.gpu_index} のハンドル取得失敗: {e}")
            raise

        try:
            name = nvmlDeviceGetName(self.handle)
            if isinstance(name, bytes):
                name = name.decode("utf-8")
            self.gpu_name = name
        except NVMLError:
            self.gpu_name = "(unknown)"

        try:
            self.fan_count = nvmlDeviceGetNumFans(self.handle)
        except NVMLError as e:
            log.error(f"ファン数取得失敗: {e}")
            raise

        if self.fan_count < 1:
            raise RuntimeError(
                f"GPU {self.gpu_index} ({self.gpu_name}) にはファンがありません"
            )

        log.info(f"GPU {self.gpu_index}: {self.gpu_name} (ファン数: {self.fan_count})")

        # 初期速度を設定。ここで失敗したら起動失敗扱いで例外を上げる
        # （= 権限なし or ドライバ非対応 or ハードウェア非対応）。
        initial_speed = self.curve[0][1]
        try:
            self._set_fan_speed_strict(initial_speed)
        except NVMLError as e:
            log.error(
                f"初回ファン速度設定に失敗: {e}\n"
                f"考えられる原因:\n"
                f"  - root 権限で実行していない\n"
                f"  - ドライバが古い (現在のドライバを確認してください)\n"
                f"  - GPU がファン制御をサポートしていない\n"
                f"  - 別のプロセスがファン制御を握っている"
            )
            raise

        self.prev_fan_speed = initial_speed
        log.info(f"初期ファン速度: {initial_speed}%")

    # -------- ファン制御 --------

    def _set_fan_speed_strict(self, speed: int) -> None:
        """全ファンに速度を設定。1 つでも失敗すれば例外を上に投げる。"""
        speed = max(0, min(100, int(speed)))
        for i in range(self.fan_count):
            nvmlDeviceSetFanSpeed_v2(self.handle, i, speed)

    def set_fan_speed(self, speed: int) -> bool:
        """通常運転時のファン速度設定。

        個別ファンの失敗はログだけ出して継続するが、戻り値で成功可否を返す。
        起動後の散発的失敗（バス一時切断など）でループを止めないため。
        """
        speed = max(0, min(100, int(speed)))
        all_ok = True
        for i in range(self.fan_count):
            try:
                nvmlDeviceSetFanSpeed_v2(self.handle, i, speed)
            except NVMLError as e:
                all_ok = False
                log.warning(f"ファン{i}の速度設定に失敗: {e}")
        return all_ok

    def get_temp(self) -> int:
        return nvmlDeviceGetTemperature(self.handle, NVML_TEMPERATURE_GPU)

    # -------- 終了処理 --------

    def restore_auto(self) -> None:
        """自動ファン制御に復帰。

        既知のバグ: nvmlDeviceSetDefaultFanSpeed_v2 が実際には自動カーブを
        再開しないドライバ世代がある。そのため、この呼び出しの後で
        SHUTDOWN_SAFE_SPEED を「下限」として明示設定するフォールバックを
        行う。自動制御が実際に効いていれば、ドライバがこの値を上書きする。
        """
        if self.handle is None:
            return

        log.info("自動ファン制御に復帰します...")
        default_ok = True
        for i in range(self.fan_count):
            try:
                nvmlDeviceSetDefaultFanSpeed_v2(self.handle, i)
            except NVMLError as e:
                default_ok = False
                log.warning(f"ファン{i}の自動制御復帰に失敗: {e}")

        if default_ok:
            log.info("自動ファン制御への復帰コマンドを送信しました")
        else:
            log.warning("自動制御復帰に一部失敗しました")

        # 既知バグへの保険: 安全速度を明示設定
        if self.shutdown_safe_speed is not None:
            try:
                log.info(
                    f"フェイルセーフとして安全速度 {self.shutdown_safe_speed}% "
                    f"を設定します（自動制御が効いていればドライバが上書きします）"
                )
                self._set_fan_speed_strict(self.shutdown_safe_speed)
            except NVMLError as e:
                log.warning(f"安全速度の設定に失敗: {e}")

    def shutdown(self) -> None:
        """クリーンアップ。多重呼び出し安全。"""
        if not self.running and not self._nvml_inited:
            return
        self.running = False
        try:
            self.restore_auto()
        except Exception as e:
            log.warning(f"自動制御復帰中にエラー: {e}")
        if self._nvml_inited:
            try:
                nvmlShutdown()
            except Exception:
                pass
            self._nvml_inited = False

    # -------- メインループ --------

    def _apply_ramp(self, current: int, target: int) -> int:
        """ランプレートを適用して、今回適用する中間値を計算する。

        current: 現在のファン速度
        target:  カーブから計算した目標速度
        戻り値:  この POLL_INTERVAL で実際に設定すべき速度
        """
        if target == current:
            return current

        if target > current:
            # 上昇方向
            if self.ramp_rate_up is None:
                return target
            max_step = self.ramp_rate_up * self.poll_interval
            step = min(target - current, max_step)
            # 1未満の端数は1に切り上げ（永遠に到達しないのを防ぐ）
            stepped = current + max(1, int(round(step)))
            return min(stepped, target)
        else:
            # 下降方向
            if self.ramp_rate_down is None:
                return target
            max_step = self.ramp_rate_down * self.poll_interval
            step = min(current - target, max_step)
            stepped = current - max(1, int(round(step)))
            return max(stepped, target)

    def run(self) -> None:
        ramp_desc_up = (
            f"{self.ramp_rate_up}%/s" if self.ramp_rate_up is not None else "即時"
        )
        ramp_desc_down = (
            f"{self.ramp_rate_down}%/s" if self.ramp_rate_down is not None else "即時"
        )
        log.info(
            f"ファンカーブ: {self.curve}  "
            f"ヒステリシス: {self.hysteresis}℃  "
            f"ポーリング: {self.poll_interval}秒  "
            f"ランプ: ↑{ramp_desc_up} / ↓{ramp_desc_down}"
        )

        while self.running:
            try:
                temp = self.get_temp()
            except NVMLError as e:
                log.error(
                    f"温度取得失敗: {e} → "
                    f"フェイルセーフ速度({self.failsafe_speed}%)を適用"
                )
                # フェイルセーフはランプを無視して即座に適用
                self.set_fan_speed(self.failsafe_speed)
                self.prev_fan_speed = self.failsafe_speed
                self._sleep_interruptible(self.poll_interval)
                continue

            target = interpolate(temp, self.curve)

            # ヒステリシス判定: 「動かす方向に踏み出すか」を決める
            move_allowed = False
            if target > self.prev_fan_speed:
                # 上昇は常に許可
                move_allowed = True
            elif target < self.prev_fan_speed:
                # 下降はヒステリシス越えのみ
                if temp < self.step_down_temp:
                    move_allowed = True

            if move_allowed:
                # ランプレートを適用して今回設定する値を決める
                next_speed = self._apply_ramp(self.prev_fan_speed, target)
                if next_speed != self.prev_fan_speed:
                    if self.set_fan_speed(next_speed):
                        # ランプ中の中間値ごとに step_down_temp を更新する
                        # （ヒステリシスは「現在の速度に対する」温度差なので）
                        self.step_down_temp = temp - self.hysteresis
                        reaching = "" if next_speed == target else f" → 目標:{target}%"
                        log.info(
                            f"温度:{temp}℃ → ファン:{next_speed}%{reaching} "
                            f"(step_down:{self.step_down_temp}℃)"
                        )
                        self.prev_fan_speed = next_speed
                    # set_fan_speed が失敗したらログだけ出して維持
                else:
                    log.debug(
                        f"温度:{temp}℃ / ファン:{self.prev_fan_speed}% (目標到達)"
                    )
            else:
                log.debug(
                    f"温度:{temp}℃ / ファン:{self.prev_fan_speed}% (維持)"
                )

            self._sleep_interruptible(self.poll_interval)

    def _sleep_interruptible(self, seconds: int) -> None:
        """シグナルで即座に起きられる sleep。"""
        end = time.monotonic() + seconds
        while self.running:
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(0.5, remaining))


# ================================================================
# main
# ================================================================

def main() -> None:
    # 1) 環境チェック (NVML 初期化前)
    check_root()

    # 2) カーブ検証
    try:
        validate_curve(FAN_CURVE)
    except ValueError as e:
        log.error(f"ファンカーブ定義エラー: {e}")
        sys.exit(1)

    # 3) コントローラ作成
    controller = FanController(
        gpu_index=GPU_INDEX,
        curve=FAN_CURVE,
        hysteresis=HYSTERESIS,
        poll_interval=POLL_INTERVAL,
        failsafe_speed=FAILSAFE_SPEED,
        shutdown_safe_speed=SHUTDOWN_SAFE_SPEED,
        ramp_rate_up=RAMP_RATE_UP_PER_SEC,
        ramp_rate_down=RAMP_RATE_DOWN_PER_SEC,
    )

    # 4) シグナルハンドラ
    def signal_handler(signum, frame):
        log.info(f"シグナル {signum} を受信。終了処理を開始します...")
        controller.running = False  # ループを抜けさせる

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 5) 起動 → ループ → クリーンアップ
    try:
        controller.init_gpu()
        controller.run()
    except NVMLError as e:
        log.error(f"NVML エラーで終了: {e}")
        sys.exit(1)
    except Exception as e:
        log.exception(f"想定外エラー: {e}")
        sys.exit(1)
    finally:
        controller.shutdown()
        log.info("終了しました")


if __name__ == "__main__":
    main()
