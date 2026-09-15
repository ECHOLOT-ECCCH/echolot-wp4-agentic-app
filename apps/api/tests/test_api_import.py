import elexifier_api


def test_package_imports() -> None:
    assert elexifier_api.__version__ == "0.1.0"
