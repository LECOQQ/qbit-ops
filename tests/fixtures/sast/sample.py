import urllib.request


def slow() -> None:
    urllib.request.urlopen("https://example.invalid")


def bounded() -> None:
    urllib.request.urlopen("https://example.invalid", timeout=3)
