class StablePrefix:
    def __init__(self) -> None:
        self._previous: list[str] = []

    def update(self, text: str) -> str:
        current = text.split()
        common = 0
        for left, right in zip(self._previous, current, strict=False):
            if left != right:
                break
            common += 1
        self._previous = current
        return " ".join(current[:common])

    def reset(self) -> None:
        self._previous.clear()
