from __future__ import annotations

import functools
import signal
import time
from typing import Callable, TypeVar

T = TypeVar("T")


class APITimeoutError(TimeoutError):
    pass


class BudgetExceededError(RuntimeError):
    pass


def with_timeout(seconds: float, label: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            if hasattr(signal, "SIGALRM"):
                def _handler(signum, frame):
                    raise APITimeoutError(f"{label} timed out after {seconds}s")

                previous = signal.signal(signal.SIGALRM, _handler)
                signal.setitimer(signal.ITIMER_REAL, seconds)
                try:
                    return func(*args, **kwargs)
                finally:
                    signal.setitimer(signal.ITIMER_REAL, 0)
                    signal.signal(signal.SIGALRM, previous)
            else:
                deadline = time.monotonic() + seconds
                result_holder: dict[str, T | Exception] = {}

                def _run():
                    try:
                        result_holder["value"] = func(*args, **kwargs)
                    except Exception as exc:
                        result_holder["error"] = exc

                import threading

                thread = threading.Thread(target=_run, daemon=True)
                thread.start()
                thread.join(timeout=seconds)
                if thread.is_alive():
                    raise APITimeoutError(f"{label} timed out after {seconds}s")
                if "error" in result_holder:
                    raise result_holder["error"]
                return result_holder["value"]

        return wrapper

    return decorator


def retry_once(label: str, backoff_seconds: float = 5.0):
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            try:
                return func(*args, **kwargs)
            except Exception as first_error:
                time.sleep(backoff_seconds)
                try:
                    return func(*args, **kwargs)
                except Exception as second_error:
                    raise RuntimeError(
                        f"{label} failed after one retry: {first_error}; retry: {second_error}"
                    ) from second_error

        return wrapper

    return decorator
