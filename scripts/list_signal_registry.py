#!/usr/bin/env python3
from weather_alpha.research.registry import default_registry


def main() -> int:
    registry = default_registry()
    print("signal_id | family | stage | enabled | live_order | features")
    print("---|---|---|:---:|:---:|---")
    for signal in registry.signals():
        print(
            f"{signal.signal_id} | {signal.family} | {signal.stage.value} | "
            f"{'yes' if signal.enabled else 'no'} | {'yes' if signal.live_order_enabled else 'no'} | "
            f"{','.join(signal.feature_ids)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
