def test_package_exposes_version() -> None:
    import interview_assistant

    assert interview_assistant.__version__ == "0.1.0"
